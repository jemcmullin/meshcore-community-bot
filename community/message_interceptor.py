"""Intercepts bot responses to add coordinator-based coordination.

Patches CommandManager.send_response() to check with the coordinator
before sending any response on a channel. DMs bypass coordination.
Passes signal data (SNR, RSSI, hops, path) for path-quality-based bidding.
Also reports messages to the PacketReporter for batch ingestion.
"""

import logging
import time
import contextvars
from typing import Tuple

from .coordinator_client import CoordinatorClient
from .coverage_fallback import CoverageFallback

logger = logging.getLogger('CommunityBot')

# Tracking message for use in send_channel_message patch that would not have access otherwise
current_message_var = contextvars.ContextVar('current_message')
coordinated_var = contextvars.ContextVar('coordinated', default=False)

class MessageInterceptor:
    """Intercepts send_response to coordinate with the central coordinator."""

    def __init__(self, bot, coordinator: CoordinatorClient, fallback: CoverageFallback, reporter=None):
        self.bot = bot
        self.coordinator = coordinator
        self.fallback = fallback
        self.reporter = reporter

        # Coordinator scoring
        from .coordinator_scoring import CoordinatorScoring
        self.coordinator_scoring = CoordinatorScoring(bot.scoring_config)

        # Save reference to the original
        self._original_process_message = bot.message_handler.process_message
        self._original_send_channel_message = bot.command_manager.send_channel_message
        self._original_send_response = bot.command_manager.send_response

        # Patch meshcore-bot
        bot.message_handler.process_message = self._wrapped_process_message
        bot.command_manager.send_channel_message = self._coordinated_send_channel_message
        bot.command_manager.send_response = self._coordinated_send_response

        logger.info("Message interceptor installed on CommandManager.send_response")
    
    async def _wrapped_process_message(self, message, *args, **kwargs):
        token = current_message_var.set(message)
        try:
            return await self._original_process_message(message, *args, **kwargs)
        finally:
            current_message_var.reset(token)
    
    async def _coordinated_send_channel_message(self, channel, content, command_id=None, skip_user_rate_limit=False, rate_limit_key=None):
        previously_coordinated = coordinated_var.get()
        if not previously_coordinated: # Keyword Messages that call send_channel_message directly
            try:
                message = current_message_var.get()
                should_send, message_hash = await self._coordinate_should_respond(message)
                if not should_send:
                    # TODO: logging
                    return True # Graceful silence to avoid error messages
            except LookupError:
                pass

        result = await self._original_send_channel_message(channel, content, command_id, skip_user_rate_limit, rate_limit_key)
        
        if not previously_coordinated: # Keyword Message not yet reported
            await self._report_message(message=message if 'message' in locals() else None, bot_responded=result, message_hash=message_hash if 'message_hash' in locals() else "")
        
        return result
    
    async def _coordinated_send_response(self, message, content: str, **kwargs) -> bool:
        """Intercept send_response calls, check with coordinator, and report message."""

        should_send, message_hash = await self._coordinate_should_respond(message)
        coordinated_var.set(True)

        if should_send:
            result = await self._original_send_response(message, content, **kwargs)
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
            logger.debug("[COORDINATOR] Message is a DM, bypassing coordinator")
            return True, message_hash

        # If coordinator is not configured, send immediately
        if not self.coordinator.is_configured:
            logger.debug("[COORDINATOR] Coordinator not configured, sending without coordination")
            return True, message_hash

        logger.debug("[COORDINATOR] Message is a channel message, checking with coordinator before responding")

        # Compute delivery score and path metrics
        db_manager = getattr(self.bot, 'db_manager', None)
        hop_score, infrastructure, path_bonus, path_freshness = self.coordinator_scoring.get_path_metrics(message, db_manager)
        delivery_score = self.coordinator_scoring.compute_delivery_score(infrastructure, hop_score, path_bonus, path_freshness)

        # Extract content prefix safely
        words = (message.content or "").split()
        content_prefix = words[0][:50] if words else ""

        logger.debug(f"[COORDINATOR] Calling should_respond with: message_hash={message_hash}, sender_pubkey={message.sender_pubkey}, channel={message.channel}, content_prefix={content_prefix}, is_dm=False, timestamp={timestamp}, snr={message.snr}, rssi={message.rssi}, hops={message.hops}, path={message.path}, delivery_score={delivery_score}")

        # Ask coordinator with signal data for path quality evaluation
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
            delivery_score=delivery_score,  # Pass delivery score for informed bidding
        )

        logger.debug(f"[COORDINATOR] should_respond result: {should_respond}")

        if should_respond is True:
            # Coordinator says we should respond
            logger.info(f"Coordinator assigned response to us for: {content_prefix} (API_score={self.coordinator.current_score:.3f}) (delivery_score={delivery_score:.3f})")
            return True, message_hash

        if should_respond is False:
            # Coordinator assigned to another bot
            logger.info(f"Coordinator assigned response to another bot for: {content_prefix} (API_score={self.coordinator.current_score:.3f}) (delivery_score={delivery_score:.3f})")
            return False, message_hash

        # should_respond is None - coordinator unreachable, use fallback
        logger.info(f"Coordinator unreachable, using score-based fallback (API_score={self.coordinator.current_score:.3f}) (delivery_score={delivery_score:.3f})")
        # Fallback: suppress if below min delivery score
        if delivery_score < self.bot.scoring_config.fallback_min_delivery_score:
            logger.info(f"Fallback: delivery score {delivery_score:.3f} below min {self.bot.scoring_config.fallback_min_delivery_score}, suppressing response")
            return False, message_hash
        await self.fallback.wait_before_responding()
        return True, message_hash # Send after fallback delay

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

    def restore(self):
        """Restore the original send_response method."""
        self.bot.command_manager.send_response = self._original_send_response
        logger.info("Message interceptor removed")
