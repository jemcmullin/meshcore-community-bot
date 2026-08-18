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
import time
from typing import Optional
import os
import json
from pathlib import Path

from .discord_webhook import send_to_discord
from .firmware_coordinator import FirmwareCoordinator
from .meshcore_request_token import (
    format_request_token,
    prepend_request_token_text,
    request_token_for_message,
    request_fingerprint_for_message,
    BotChannelKind,
)
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
        return BotChannelKind.BOT_CHANNEL_DM
    ch = message.channel or ""
    if ch == "#bot":
        return BotChannelKind.BOT_CHANNEL_BOT
    if ch == "#testing":
        return BotChannelKind.BOT_CHANNEL_TESTING
    if ch == "#emergency":
        return BotChannelKind.BOT_CHANNEL_EMERGENCY
    # Default to public channel kind for normal channels
    return BotChannelKind.BOT_CHANNEL_PUBLIC


def _split_firmware_channel_text(raw_text: str) -> tuple[str, str] | None:
    if not raw_text:
        return None
    head, sep, tail = raw_text.partition(": ")
    if sep and head.strip():
        return head.strip(), tail.strip()
    return None


def _to_fingerprint_input(message) -> dict:
    """Build the firmware fingerprint input dict from a MeshMessage."""
    # DMs short-circuit in _coordinated_send_response and never reach coordination.
    channel_kind = _channel_kind(message)
    sender_name = message.sender_id or ""
    text_for_fingerprint = message.content or ""

    # Firmware channel token input uses zero key bytes and len=0.
    key_prefix = b"\x00" * 6
    key_source = "firmware_channel_zero"
    key_prefix_len = 0
    parsed = _split_firmware_channel_text(raw_text_var.get(""))
    if parsed is not None:
        sender_name, text_for_fingerprint = parsed

    # Prefer raw payload bytes when available on the MeshMessage. This ensures
    # the exact on-wire bytes are fed into the firmware-compatible hashing
    # routine (avoids decoding/utf-8 re-encoding mismatches).
    text_value = text_for_fingerprint
    text_len = len(text_for_fingerprint.encode("utf-8"))
    try:
        # message.payload_bytes is attached by MessageHandler when RF data is present
        raw_bytes = getattr(message, "payload_bytes", None)
        if raw_bytes:
            text_value = raw_bytes
            text_len = len(raw_bytes)
    except Exception:
        pass

    return {
        "channel_kind": channel_kind,
        "channel_name": message.channel or "",
        "sender_name": sender_name,
        "sender_key_prefix": key_prefix,
        "sender_key_source": key_source,
        "sender_key_prefix_len": key_prefix_len,
        "sender_timestamp": message.timestamp or 0,
        "text": text_value,
        "text_len": text_len,
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

    # How long to remember the won/suppressed outcome for a token so that
    # subsequent chunks of the same multi-part response are treated consistently.
    _TOKEN_OUTCOME_TTL = 120.0  # seconds
    _ADD_DELAY_MS_QUEUE_MODE = 0 #1000  # Additional delay to add in queue mode to improve suppression reliability (empirically determined, not from firmware source)

    def __init__(self, bot, firmware_coordinator: FirmwareCoordinator):
        self.bot = bot
        self.fw = firmware_coordinator

        self._debug_no_send = False
        community_cfg = getattr(bot, "community_config", None)
        if community_cfg is not None:
            self._debug_no_send = bool(getattr(community_cfg, "coordination_debug_no_send", False))
            # Propagate queue-mode flag from CommunityConfig to firmware coordinator
            try:
                self.fw.coordination_mode_queue = bool(getattr(community_cfg, "coordination_mode_queue", False))
            except Exception:
                pass
        elif getattr(bot, "config", None) and bot.config.has_section("Community"):
            self._debug_no_send = bot.config.getboolean(
                "Community",
                "coordination_debug_no_send",
                fallback=False,
            )
            try:
                self.fw.coordination_mode_queue = bot.config.getboolean(
                    "Community", "coordination_mode_queue", fallback=False
                )
            except Exception:
                pass

        # Discord webhook config
        self._discord_bot_webhook = bot.config.get("Discord", "bot_webhook_url", fallback="")
        self._discord_emergency_webhook = bot.config.get("Discord", "emergency_webhook_url", fallback="")
        self._discord_emergency_broadcast = bot.config.get("Discord", "emergency_broadcast_channel", fallback="")
        self._bot_name = bot.config.get("Bot", "bot_name", fallback="CommunityBot")

        # Per-token outcome cache: maps request_token_16bit → ("won"|"suppressed", monotonic_time)
        # Used to propagate the first-chunk outcome to all subsequent chunks of the same message.
        self._token_outcomes: dict[int, tuple[str, float]] = {}

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
        if self._debug_no_send:
            logger.warning("Community debug mode enabled: responses will be coordinated but not transmitted")

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
        # Observe incoming message for peer suppression token (only for non-DM).
        if not message.is_dm:
            # Pass channel info so suppression only applies to matching channels.
            self.fw.observe_peer_message(message.content or "", _channel_kind(message), message.channel or "")

        token = current_message_var.set(message)
        coord_token = coordinated_var.set(False)
        try:
            await self._discord_forward_incoming(message)
            return await self._original_process_message(message, *args, **kwargs)
        finally:
            coordinated_var.reset(coord_token)
            current_message_var.reset(token)
    
    async def _coordinated_send_channel_message(self, channel, content, *args, **kwargs):
        """Coordinate keyword-triggered channel messages (no inbound MeshMessage in signature)."""
        bypass_coordination = bool(
            kwargs.pop("bypass_coordination", False)
            or kwargs.pop("skip_coordination", False)
            or kwargs.pop("bypass_corrdination", False)
            or kwargs.pop("skip_corrdination", False)
        )
        if bypass_coordination:
            logger.debug(
                "Bypassing firmware coordination for send_channel_message (channel=%s payload=%r)",
                channel,
                content,
            )
            if self._debug_no_send:
                logger.debug(
                    "DEBUG_NO_SEND: skipped bypassed channel send (channel=%s payload=%r)",
                    channel,
                    content,
                )
                return True
            return await self._original_send_channel_message(channel, content, *args, **kwargs)

        if coordinated_var.get():
            # Already coordinated by _coordinated_send_response for this message — just send.
            if self._debug_no_send:
                logger.debug(
                    "DEBUG_NO_SEND: coordinated channel continuation skipped (channel=%s payload=%r)",
                    channel,
                    content,
                )
                return True
            return await self._original_send_channel_message(channel, content, *args, **kwargs)

        try:
            message = current_message_var.get()
        except LookupError:
            # No inbound context (e.g. scheduled broadcast) — send immediately.
            logger.debug("send_channel_message: no current_message context, sending immediately")
            if self._debug_no_send:
                logger.debug(
                    "DEBUG_NO_SEND: skipped non-context channel send (channel=%s payload=%r)",
                    channel,
                    content,
                )
                return True
            return await self._original_send_channel_message(channel, content, *args, **kwargs)

        if message.is_dm:
            if self._debug_no_send:
                logger.debug(
                    "DEBUG_NO_SEND: skipped DM channel send (channel=%s payload=%r)",
                    channel,
                    content,
                )
                return True
            return await self._original_send_channel_message(channel, content, *args, **kwargs)

        fp_input = _to_fingerprint_input(message)
        send_fn = lambda text: self._original_send_channel_message(channel, text, *args, **kwargs)
        return await self._firmware_coordinate_and_send(fp_input, content, send_fn, message)

    async def _coordinated_send_response(self, message, content: str, *args, **kwargs) -> bool:
        """Coordinate command responses via firmware timing protocol."""
        bypass_coordination = bool(
            kwargs.pop("bypass_coordination", False)
            or kwargs.pop("skip_coordination", False)
            or kwargs.pop("bypass_corrdination", False)
            or kwargs.pop("skip_corrdination", False)
        )

        if bypass_coordination:
            logger.debug(
                "Bypassing firmware coordination for send_response (sender=%s payload=%r)",
                message.sender_id,
                content,
            )
            coordinated_var.set(True)  # prevent inner send_channel_message from coordinating
            if self._debug_no_send:
                logger.debug("DEBUG_NO_SEND: skipped bypassed response (sender=%s payload=%r)", message.sender_id, content)
                return True
            result = await self._original_send_response(message, content, *args, **kwargs)
            await self._discord_forward_response(message, content)
            return result

        # DMs bypass coordination — only this bot received the DM.
        if message.is_dm:
            if self._debug_no_send:
                logger.debug("DEBUG_NO_SEND: skipped DM response (sender=%s payload=%r)", message.sender_id, content)
                return True
            result = await self._original_send_response(message, content, *args, **kwargs)
            await self._discord_forward_response(message, content)
            return result

        fp_input = _to_fingerprint_input(message)
        coordinated_var.set(True)  # prevent double-coordination if send_channel_message is also called
        send_fn = lambda text: self._original_send_response(message, text, *args, **kwargs)
        return await self._firmware_coordinate_and_send(fp_input, content, send_fn, message)

    def _purge_stale_token_outcomes(self) -> None:
        now = time.monotonic()
        self._token_outcomes = {
            t: v for t, v in self._token_outcomes.items()
            if now - v[1] < self._TOKEN_OUTCOME_TTL
        }

    async def _firmware_coordinate_and_send(self, fp_input: dict, content: str, send_fn, message) -> bool:
        """Core firmware coordination: compute delay, sleep, check suppression, send.

        For multi-chunk responses (same request token), the outcome of the first
        chunk is cached in _token_outcomes so that:
          - Subsequent chunks are sent immediately without re-coordinating ("won").
          - Subsequent chunks are silently dropped if the first was suppressed.

          Flow for first chunk:
             1. Ask FirmwareCoordinator to schedule the unprefixed response — returns (entry, delay_ms)
             or None if the response was recently sent (duplicate suppression).
             2. Sleep for delay_ms.  During this sleep the asyncio loop processes
             incoming messages; if a peer bot responds first, observe_peer_message()
             will mark entry.suppressed = True.
             3. If not suppressed, prepend [xxxx] and call send_fn.  Record "won" in
                 _token_outcomes only on successful send.
             4. If suppressed, record "suppressed" in _token_outcomes and return False.
        """
        command = _first_word(message.content if message else None)
        token_hex = _token_hex(fp_input)
        tokenised = None

        # If the inbound channel message is an '@' mention addressing this bot
        # (e.g. "@BotName do something"), do not prefix our response with
        # the firmware token; still coordinate timing/suppression but send the
        # unprefixed content so replies look natural for addressed messages.
        should_prefix = True if not self.fw.coordination_mode_queue else False
        should_send_immediately = False
        try:
            bot_name = getattr(self, "_bot_name", "") or ""
            first_word = (message.content or "").lstrip().split()[0] if getattr(message, "content", None) else ""
            if first_word.startswith("@") and first_word[1:].strip(':,').lower() == bot_name.strip().lower():
                should_prefix = False
                # If addressed directly, attempt to send immediately without waiting.
                should_send_immediately = True
        except Exception:
            should_prefix = True
            should_send_immediately = False
        req_token_16 = request_token_for_message(fp_input) & 0xFFFF

        key_prefix = fp_input.get("sender_key_prefix", b"")
        if isinstance(key_prefix, (bytes, bytearray)):
            key_prefix_text = bytes(key_prefix).hex()
        else:
            key_prefix_text = str(key_prefix)
        logger.debug(
            "[TOKEN] input: channel_kind=%s channel_name=%r sender_name=%r sender_key_prefix=%s sender_key_source=%s sender_timestamp=%s text=%r text_len=%s path_hash_count=%s",
            fp_input.get("channel_kind"),
            fp_input.get("channel_name"),
            fp_input.get("sender_name"),
            key_prefix_text,
            fp_input.get("sender_key_source", ""),
            fp_input.get("sender_timestamp"),
            fp_input.get("text"),
            fp_input.get("text_len"),
            fp_input.get("path_hash_count"),
            extra={"log_color": "HIGHLIGHT"},
        )
        logger.debug(
            "[TOKEN] output: token=[%s] token_u16=0x%04x message_output=%r",
            token_hex,
            req_token_16,
            tokenised,
            extra={"log_color": "HIGHLIGHT"},
        )

        # Diagnostic logging: capture raw payload bytes (if available), computed
        # fingerprint/token, recent observed peer tokens in the last 20s, and a
        # snapshot of pending tokens. This aids comparison against firmware
        # observations when debugging mismatches.
        try:
            computed_fp = None
            try:
                computed_fp = request_fingerprint_for_message(fp_input)
            except Exception:
                computed_fp = None

            raw_hex = getattr(message, "payload_hex", None) if message else None
            raw_len = len(raw_hex) // 2 if raw_hex else None

            observed_tokens = []
            try:
                observed_tokens = [f"{t:04x}" for t in self.fw.get_observed_peer_tokens(20000)]
            except Exception:
                observed_tokens = []

            pending_tokens = []
            try:
                for e in list(self.fw.pending):
                    if getattr(e, "request_fingerprint", None):
                        pending_tokens.append(f"{(e.request_fingerprint & 0xFFFF):04x}")
            except Exception:
                pending_tokens = []

            logger.debug(
                "[TOKEN] raw_hex_prefix=%s raw_len=%s computed_fp=%s token=%s observed_peers=%s pending_tokens=%s",
                (raw_hex[:64] + "...") if raw_hex else None,
                raw_len,
                (f"0x{computed_fp:016x}" if computed_fp is not None else None),
                token_hex,
                observed_tokens,
                pending_tokens,
                extra={"log_color": "HIGHLIGHT"},
            )
            # Also append a JSON line to a diagnostics file for easier capture.
            try:
                # Use env override if provided, otherwise place diagnostics in the
                # repository `logs/` directory so it sits alongside other bot logs.
                diag_path = os.environ.get("FW_DIAG_LOG")
                if not diag_path:
                    repo_root = Path(__file__).resolve().parents[1]
                    logs_dir = repo_root / "logs"
                    try:
                        logs_dir.mkdir(parents=True, exist_ok=True)
                    except Exception:
                        # If we cannot create the repo logs dir, fall back to /tmp
                        diag_path = "/tmp/mesh_fw_diag.log"
                    else:
                        diag_path = str(logs_dir / "mesh_fw_diag.log")
                entry = {
                    "ts_ms": int(time.time() * 1000),
                    "raw_hex_prefix": (raw_hex[:128] + "...") if raw_hex else None,
                    "raw_len": raw_len,
                    "computed_fp": (f"0x{computed_fp:016x}" if computed_fp is not None else None),
                    "token": token_hex,
                    "observed_peers": observed_tokens,
                    "pending_tokens": pending_tokens,
                }
                with open(diag_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                logger.debug("Failed to write firmware diagnostic log", exc_info=True)
        except Exception:
            logger.debug("Failed to log firmware coordination diagnostics", exc_info=True)

        self._purge_stale_token_outcomes()

        # If this was an addressed @mention to this bot, send immediately
        # (unprefixed) and skip the normal firmware scheduling/suppression.
        if should_send_immediately:
            payload = content
            sent = await self._send_or_debug(payload, send_fn, message, token_hex, command, 0)
            if sent:
                await self._discord_forward_response(message, payload)
            asyncio.create_task(publish_web_viewer_fw_event(
                self.bot, message, "fw_sent", delay_ms=0, command=command, token_hex=token_hex,
            ))
            return sent

        prior = self._token_outcomes.get(req_token_16)
        if prior is not None:
            outcome, _ = prior
            if outcome == "suppressed":
                # A peer bot already handled this message — drop this chunk too.
                logger.info(
                    "Chunk suppressed (token=[%s] peer already responded, dropping chunk)",
                    token_hex,
                )
                asyncio.create_task(publish_web_viewer_fw_event(
                    self.bot, message, "fw_suppressed", delay_ms=0, command=command, token_hex=token_hex,
                ))
                return False
            elif outcome == "won":
                # We already won the race for this token — send this chunk immediately
                # without re-coordinating (same message, just a continuation packet).
                logger.info(
                    "Chunk sent without re-coordination (token=[%s] already won)",
                    token_hex,
                )
                payload = (prepend_request_token_text(fp_input, content) if should_prefix else content)
                sent = await self._send_or_debug(payload, send_fn, message, token_hex, command, 0)
                if sent:
                    await self._discord_forward_response(message, payload)
                asyncio.create_task(publish_web_viewer_fw_event(
                    self.bot, message, "fw_sent", delay_ms=0, command=command, token_hex=token_hex,
                ))
                logger.info("Sent chunk (token=[%s] command=%s)", token_hex, command)
                return sent

        # First chunk for this token — run full coordination.
        now_ms = int(time.time() * 1000) & 0xFFFFFFFF
        result = self.fw.schedule_response(fp_input, content, now_ms)
        if result is None:
            # Recently sent — skip silently (duplicate suppression).
            logger.debug("Skipping duplicate response for token=[%s]", token_hex)
            return False

        entry, delay_ms = result
        if self.fw.coordination_mode_queue:
            #add a second
            delay_ms += self._ADD_DELAY_MS_QUEUE_MODE
        logger.debug(
            "Firmware coordination: sleeping %dms before responding (token=[%s] command=%s)",
            delay_ms, token_hex, command,
        )
        asyncio.create_task(publish_web_viewer_fw_event(
            self.bot, message, "fw_pending", delay_ms=delay_ms, command=command, token_hex=token_hex,
        ))

        await asyncio.sleep(delay_ms / 1000.0)

        if entry.suppressed:
            self._token_outcomes[req_token_16] = ("suppressed", time.monotonic())
            logger.info(
                "Response suppressed by peer bot (token=[%s] command=%s delay=%dms)",
                token_hex, command, delay_ms,
            )
            asyncio.create_task(publish_web_viewer_fw_event(
                self.bot, message, "fw_suppressed", delay_ms=delay_ms, command=command, token_hex=token_hex,
            ))
            return False

        payload = (prepend_request_token_text(fp_input, content) if should_prefix else content)
        sent = await self._send_or_debug(payload, send_fn, message, token_hex, command, delay_ms)
        if sent:
            self._token_outcomes[req_token_16] = ("won", time.monotonic())
            now_ms2 = int(time.time() * 1000) & 0xFFFFFFFF
            self.fw.mark_sent(entry, now_ms2)
            await self._discord_forward_response(message, payload)
            asyncio.create_task(publish_web_viewer_fw_event(
                self.bot, message, "fw_sent", delay_ms=delay_ms, command=command, token_hex=token_hex,
            ))
            logger.info("Sent response (token=[%s] command=%s delay=%dms)", token_hex, command, delay_ms)
        else:
            logger.warning("Response send failed after coordination (token=[%s] command=%s)", token_hex, command)
        return sent

    async def _send_or_debug(self, payload: str, send_fn, message, token_hex: str, command: str, delay_ms: int) -> bool:
        if self._debug_no_send:
            logger.debug(
                "DEBUG_NO_SEND: send skipped (token=[%s] command=%s delay=%dms channel=%s payload=%r)",
                token_hex,
                command,
                delay_ms,
                getattr(message, "channel", ""),
                payload,
            )
            return True
        return await send_fn(payload)

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
        if self._debug_no_send:
            logger.debug("DEBUG_NO_SEND: skipped Discord forward for response payload=%r", content)
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
