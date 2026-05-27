"""Intercepts bot responses for firmware-native decentralized coordination.

Patches four meshcore-bot methods to coordinate channel message responses using
the firmware FNV-1a request token protocol.  DMs bypass coordination entirely.

Patch points:
  1. MessageHandler.handle_channel_message  — captures raw packet text before colon-split
  2. MessageHandler.process_message         — sets current_message_var context
  3. CommandManager.send_response           — coordinates command responses
  4. CommandManager.send_channel_message    — coordinates keyword-triggered responses
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Optional

from .discord_webhook import send_to_discord
from .firmware_coordinator import FirmwareCoordinator
from .meshcore_request_token import (
    format_request_token,
    prepend_request_token_text,
    request_token_for_message,
)
from .meshcore_response_coordinator import suppress_by_response_fingerprint
from .web_viewer_packet_stream import publish_web_viewer_fw_event

logger = logging.getLogger("CommunityBot")

# Context var: set to the current MeshMessage during process_message
current_message_var: contextvars.ContextVar = contextvars.ContextVar("current_message")

# Context var: True once send_response has already coordinated for this message
# (prevents double-coordination when send_channel_message is also called)
coordinated_var: contextvars.ContextVar = contextvars.ContextVar("coordinated", default=False)

# Context var: raw payload text e.g. "HOWL: !ping" captured before colon-split
raw_text_var: contextvars.ContextVar[str] = contextvars.ContextVar("raw_text", default="")


def _channel_kind(message) -> int:
    """Map MeshMessage channel to firmware channel_kind integer."""
    if message.is_dm:
        return 0
    ch = message.channel or ""
    if ch == "#bot":
        return 1
    if ch == "#testing":
        return 2
    return 3


def _to_fingerprint_input(message) -> dict:
    """Build the firmware fingerprint input dict from a MeshMessage."""
    try:
        pubkey_bytes = bytes.fromhex(message.sender_pubkey or "")
    except (ValueError, TypeError):
        pubkey_bytes = b""
    key_prefix = pubkey_bytes[:6].ljust(6, b"\x00")
    channel_kind = _channel_kind(message)
    raw_text = raw_text_var.get(message.content or "")
    return {
        "channel_kind": channel_kind,
        "channel_name": message.channel or "",
        "sender_name": message.sender_id or "",
        "sender_key_prefix": key_prefix,
        "sender_key_prefix_len": 6,
        "sender_timestamp": message.timestamp or 0,
        "text": raw_text,
        "path_hash_count": message.hops or 0,
    }


def _first_word(text: Optional[str]) -> str:
    if not text:
        return ""
    words = text.split()
    return words[0] if words else ""


def _token_hex(fp_input: dict) -> str:
    return format_request_token(request_token_for_message(fp_input))


class MessageInterceptor:
    """Installs firmware-native coordination patches on meshcore-bot.

    Note on patched method signatures: All patched methods use *args/**kwargs for
    optional/forwarded parameters to remain compatible with upstream submodule changes.
    We explicitly declare only the parameters we use, and forward everything else via
    *args/**kwargs to preserve compatibility across submodule versions.
    """

    def __init__(self, bot, firmware_coordinator: FirmwareCoordinator):
        self.bot = bot
        self.fw = firmware_coordinator

        # Discord webhook config
        self._discord_bot_webhook = bot.config.get("Discord", "bot_webhook_url", fallback="")
        self._discord_emergency_webhook = bot.config.get("Discord", "emergency_webhook_url", fallback="")
        self._discord_emergency_broadcast = bot.config.get("Discord", "emergency_broadcast_channel", fallback="")
        self._bot_name = bot.config.get("Bot", "bot_name", fallback="CommunityBot")

        # Save originals before patching
        self._original_handle_channel_message = bot.message_handler.handle_channel_message
        self._original_process_message = bot.message_handler.process_message
        self._original_send_channel_message = bot.command_manager.send_channel_message
        self._original_send_response = bot.command_manager.send_response

        # Install patches
        bot.message_handler.handle_channel_message = self._wrapped_handle_channel_message
        bot.message_handler.process_message = self._wrapped_process_message
        bot.command_manager.send_channel_message = self._coordinated_send_channel_message
        bot.command_manager.send_response = self._coordinated_send_response

        logger.info("Firmware message interceptor installed")

    # ------------------------------------------------------------------
    # Patch 1: capture raw text before colon-split
    # ------------------------------------------------------------------

    async def _wrapped_handle_channel_message(self, event, *args, **kwargs):
        """Capture raw payload text into raw_text_var before the message_handler
        strips the sender prefix (e.g. "HOWL: !ping" → "!ping")."""
        raw_text = ""
        try:
            payload = getattr(event, "payload", None) or {}
            raw_text = payload.get("text", "")
        except Exception:
            pass
        token = raw_text_var.set(raw_text)
        try:
            return await self._original_handle_channel_message(event, *args, **kwargs)
        finally:
            raw_text_var.reset(token)

    # ------------------------------------------------------------------
    # Patch 2: observe peer tokens, set message context
    # ------------------------------------------------------------------

    async def _wrapped_process_message(self, message, *args, **kwargs):
        """Set current_message context and observe incoming peer [xxxx] prefixes."""
        # Observe incoming message for peer suppression token
        if not message.is_dm:
            self.fw.observe_peer_message(message.content or "")

        token = current_message_var.set(message)
        coord_token = coordinated_var.set(False)
        try:
            await self._discord_forward_incoming(message)
            return await self._original_process_message(message, *args, **kwargs)
        finally:
            coordinated_var.reset(coord_token)
            current_message_var.reset(token)
    
    async def _coordinated_send_channel_message(self, channel, content, *args, **kwargs):
        previously_coordinated = coordinated_var.get()
        message = None
        message_hash = ""
        if not previously_coordinated: # Keyword Messages that call send_channel_message directly
            try:
                message = current_message_var.get()
                should_send, message_hash = await self._coordinate_should_respond(message)
                if not should_send:
                    return True # Graceful silence to avoid error messages
            except LookupError:
                logger.warning('[COORDINATOR] send_channel_message no context, sending without coordination')

        result = await self._original_send_channel_message(channel, content, *args, **kwargs)

        if not previously_coordinated: # Keyword Message not yet reported
            await self._report_message(message=message, bot_responded=result, message_hash=message_hash)

        return result
    
    async def _coordinated_send_response(self, message, content: str, *args, **kwargs) -> bool:
        """Intercept send_response calls, check with coordinator, and report message."""

        should_send, message_hash = await self._coordinate_should_respond(message)
        coordinated_var.set(True)

        if should_send:
            result = await self._original_send_response(message, content, *args, **kwargs)
            # Forward bot response to Discord webhook
            await self._discord_forward_response(message, content)
        else:
            result = False  # Did not send due to coordinator/fallback decision

        await self._report_message(message, bot_responded=result, message_hash=message_hash)
        return result

    async def _coordinate_should_respond(self, message) -> Tuple[bool, str]:
        """Decision tree on whether to respond to a message, based on coordinator input and fallback logic.
        True = respond, False = do not respond, returned as soon as proper gate reached.
        For DMs: send immediately (no coordination needed).
        For channel messages: check with coordinator first, passing signal data for the bidding window to evaluate path quality.

        Returns:
            Tuple of (should_respond: bool, message_hash: str) hash for deduplication
        """
        logger.debug(f"[COORDINATOR] Intercepted message from {getattr(message, 'sender_id', None)}")
        
        # Compute message hash for deduplication
        timestamp = message.timestamp or int(time.time())
        message_hash = CoordinatorClient.compute_message_hash(
            sender_pubkey=message.sender_pubkey or "",
            content=message.content or "",
            timestamp=timestamp,
        )
            
        # DMs always go through - only this bot received the DM
        if message.is_dm:
            logger.info("[COORDINATOR] Message is a DM, bypassing coordinator")
            asyncio.create_task(publish_web_viewer_dm_event(message, True, self.bot))
            return True, message_hash

        # If coordinator is not configured, send immediately
        if not self.coordinator.is_configured:
            logger.warning("[COORDINATOR] Coordinator not configured, sending without coordination")
            asyncio.create_task(publish_web_viewer_coordination_event(
                bot=self.bot,
                message=message,
                message_hash=message_hash,
                stage="fallback_sent",
                reason="coordinator_not_configured",
                command=(message.content or "").split()[0] if message.content else "",
            ))
            return True, message_hash

        logger.debug("[COORDINATOR] Message is a channel message, checking with coordinator before responding")

        # Extract content prefix safely
        words = (message.content or "").split()
        content_prefix = words[0][:50] if words else ""
        hops = message.hops or 0

        logger.debug(f"[COORDINATOR] Calling should_respond with: message_hash={message_hash}, sender_pubkey={message.sender_pubkey}, channel={message.channel}, content_prefix={content_prefix}, is_dm=False, timestamp={timestamp}, snr={message.snr}, rssi={message.rssi}, hops={message.hops}, path={message.path}")
        asyncio.create_task(publish_web_viewer_coordination_event(
            bot=self.bot,
            message=message,
            message_hash=message_hash,
            stage="bid",
            command=content_prefix,
        ))

        # Ask coordinator with raw signal data; coordinator does the scoring
        should_respond = await self.coordinator.should_respond(
            message_hash=message_hash,
            sender_pubkey=message.sender_pubkey or "",
            channel=message.channel,
            content_prefix=content_prefix,
            is_dm=False,
            timestamp=timestamp,
            receiver_snr=message.snr,
            receiver_rssi=message.rssi,
            receiver_hops=message.hops,
            receiver_path=message.path,
        )

        logger.debug(f"[COORDINATOR] should_respond result: {should_respond}")

        if should_respond is not None and should_respond.get("should_respond", True):
            # Coordinator says we should respond, possibly after a delay
            delay_ms = should_respond.get("response_delay_ms", 0)
            winner_name = should_respond.get("winner_name", "")
            winner_score = should_respond.get("winner_score", 0.0)
            reason = should_respond.get("reason", "")
            self.coordinator.current_score = winner_score
            logger.info(f"[COORDINATOR] assigned response to us for: {content_prefix} (score={winner_score:.3f}, reason={reason}, delay={delay_ms}ms)")
            asyncio.create_task(publish_web_viewer_coordination_event(
                bot=self.bot,
                message=message,
                message_hash=message_hash,
                stage="assigned_us",
                winner_name=winner_name,
                winner_score=winner_score,
                reason=reason,
                delay_ms=delay_ms,
                command=content_prefix,
            ))
            await self.timing.wait_delay_ms(delay_ms)
            return True, message_hash

        if should_respond is not None and not should_respond.get("should_respond", True):
            # Coordinator assigned to another bot
            winner_name = should_respond.get("winner_name", "")
            winner_score = should_respond.get("winner_score", 0.0)
            reason = should_respond.get("reason", "")
            self.coordinator.current_score = winner_score
            logger.info(f"[COORDINATOR] assigned response to another bot for: {content_prefix} (winner={winner_name}, score={winner_score:.3f}, reason={reason})")
            asyncio.create_task(publish_web_viewer_coordination_event(
                bot=self.bot,
                message=message,
                message_hash=message_hash,
                stage="assigned_other",
                winner_name=winner_name,
                winner_score=winner_score,
                reason=reason,
                command=content_prefix,
            ))
            return False, message_hash

        # should_respond is None - coordinator unreachable, use hop-based fallback delay
        logger.info(f"[COORDINATOR] unreachable, using hop-based fallback (hops={hops})")
        await self.timing.fallback_wait_before_responding(hops=hops)
        logger.info("[COORDINATOR] Fallback: sending response after delay")
        asyncio.create_task(publish_web_viewer_coordination_event(
            bot=self.bot,
            message=message,
            message_hash=message_hash,
            stage="fallback_sent",
            command=content_prefix,
        ))
        return True, message_hash  # Send after fallback delay

    async def _report_message(self, message, bot_responded: bool = False, message_hash: str = ""):
        """Report the message to the PacketReporter for batch ingestion."""
        if not self.reporter:
            return

        try:
            timestamp = message.timestamp or int(time.time())
            if not message_hash:
                message_hash = CoordinatorClient.compute_message_hash(
                    sender_pubkey=message.sender_pubkey or "",
                    content=message.content or "",
                    timestamp=timestamp,
                )

            # Detect if this was a command
            words = (message.content or "").split()
            content_prefix = words[0].lower() if words else ""
            was_command = bool(content_prefix)  # All intercepted messages are commands
            command_name = content_prefix if was_command else None

            await self.reporter.add_message(
                message_hash=message_hash,
                sender_pubkey=message.sender_pubkey or "",
                sender_name=message.sender_id or "",
                channel=message.channel,
                content=message.content or "",
                is_dm=message.is_dm,
                hops=message.hops,
                path=message.path,
                snr=message.snr,
                rssi=message.rssi,
                timestamp=timestamp,
                was_command=was_command,
                command_name=command_name,
                bot_responded=bot_responded,
            )
        except Exception as e:
            logger.debug(f"Failed to report message: {e}")

    def _get_discord_webhook_for_channel(self, channel: str) -> str:
        """Return the Discord webhook URL for a given channel, or empty string."""
        if channel == "#bot":
            return self._discord_bot_webhook
        elif channel == "#emergency":
            return self._discord_emergency_webhook
        return ""

    async def _discord_forward_incoming(self, message):
        """Forward an incoming channel message to Discord."""
        if message.is_dm:
            return
        webhook_url = self._get_discord_webhook_for_channel(message.channel)
        if webhook_url:
            asyncio.create_task(send_to_discord(
                webhook_url,
                message.sender_id or "Unknown",
                message.content or "",
                is_incoming=True,
            ))
        # Rebroadcast emergency messages to configured channel
        if message.channel == "#emergency" and self._discord_emergency_broadcast:
            asyncio.create_task(
                self._original_send_channel_message(
                    self._discord_emergency_broadcast,
                    f"EMERGENCY MESSAGE FROM #EMERGENCY: {message.content or ''}",
                )
            )

    async def _discord_forward_response(self, message, content: str):
        """Forward a bot response to Discord."""
        if message.is_dm:
            return
        webhook_url = self._get_discord_webhook_for_channel(message.channel)
        if webhook_url:
            asyncio.create_task(send_to_discord(
                webhook_url,
                self._bot_name,
                content,
                is_incoming=False,
            ))

    def restore(self):
        """Restore all patched methods."""
        self.bot.message_handler.handle_channel_message = self._original_handle_channel_message
        self.bot.message_handler.process_message = self._original_process_message
        self.bot.command_manager.send_channel_message = self._original_send_channel_message
        self.bot.command_manager.send_response = self._original_send_response
        logger.info("Firmware message interceptor removed")
