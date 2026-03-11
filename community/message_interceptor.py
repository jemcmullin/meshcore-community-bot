"""Intercepts bot responses to add coordinator-based coordination.

Patches CommandManager.send_response() to check with the coordinator
before sending any response on a channel. DMs bypass coordination.
Passes signal data (SNR, RSSI, hops, path) for path-quality-based bidding.
Also reports messages to the PacketReporter for batch ingestion.
"""

import logging
import time

from .coordinator_client import CoordinatorClient
from .coverage_fallback import CoverageFallback

logger = logging.getLogger('CommunityBot')


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

        # Save reference to the original send_response
        self._original_send_response = bot.command_manager.send_response

        # Patch the command manager's send_response
        bot.command_manager.send_response = self._coordinated_send_response

        logger.info("Message interceptor installed on CommandManager.send_response")

    async def _coordinated_send_response(self, message, content: str, **kwargs) -> bool:
        """Coordinated version of send_response.
        For DMs: send immediately (no coordination needed).
        For channel messages: check with coordinator first, passing signal data for the bidding window to evaluate path quality.
        """
        logger.debug(f"--Intercepted send_response for message from {message.sender_id}")
        # DMs always go through - only this bot received the DM
        if message.is_dm:
            logger.debug("--Message is a DM, bypassing coordinator")
            result = await self._original_send_response(message, content, **kwargs)
            await self._report_message(message, bot_responded=result)
            return result

        # If coordinator is not configured, send immediately
        if not self.coordinator.is_configured:
            logger.debug("--Coordinator not configured, sending without coordination")
            result = await self._original_send_response(message, content, **kwargs)
            await self._report_message(message, bot_responded=result)
            return result

        logger.debug("--Message is a channel message, checking with coordinator before responding")
        # Compute message hash for deduplication
        timestamp = message.timestamp or int(time.time())
        message_hash = CoordinatorClient.compute_message_hash(
            sender_pubkey=message.sender_pubkey or "",
            content=message.content or "",
            timestamp=timestamp,
        )

        # Compute delivery score and path metrics
        db_manager = getattr(self.bot, 'db_manager', None)
        hop_score, infrastructure, path_bonus, path_freshness = self.coordinator_scoring.get_path_metrics(message, db_manager)
        delivery_score = self.coordinator_scoring.compute_delivery_score(infrastructure, hop_score, path_bonus, path_freshness)

        # Extract content prefix safely
        words = (message.content or "").split()
        content_prefix = words[0][:50] if words else ""

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

        if should_respond is True:
            # Coordinator says we should respond
            logger.info(f"Coordinator assigned response to us for: {content_prefix} (score={delivery_score:.3f})")
            result = await self._original_send_response(message, content, **kwargs)
            await self._report_message(message, bot_responded=result, message_hash=message_hash)
            return result

        if should_respond is False:
            # Coordinator assigned to another bot
            logger.info(f"Coordinator assigned response to another bot for: {content_prefix} (score={delivery_score:.3f})")
            await self._report_message(message, bot_responded=False, message_hash=message_hash)
            return True  # Return True so command doesn't report failure

        # should_respond is None - coordinator unreachable, use fallback
        logger.info(f"Coordinator unreachable, using score-based fallback (score={delivery_score:.3f})")
        # Fallback: suppress if below min delivery score
        if delivery_score < self.bot.scoring_config.fallback_min_delivery_score:
            logger.info(f"Fallback: delivery score {delivery_score:.3f} below min {self.bot.scoring_config.fallback_min_delivery_score}, suppressing response")
            await self._report_message(message, bot_responded=False, message_hash=message_hash)
            return True
        await self.fallback.wait_before_responding()
        result = await self._original_send_response(message, content, **kwargs)
        await self._report_message(message, bot_responded=result, message_hash=message_hash)
        return result

        #TODO Remove old code after confirming new code works as intended
        # # Extract content prefix safely
        # words = (message.content or "").split()
        # content_prefix = words[0][:50] if words else ""

        # # Ask coordinator with signal data for path quality evaluation
        # should_respond = await self.coordinator.should_respond(
        #     message_hash=message_hash,
        #     sender_pubkey=message.sender_pubkey or "",
        #     channel=message.channel,
        #     content_prefix=content_prefix,
        #     is_dm=False,
        #     timestamp=timestamp,
        #     receiver_snr=message.snr,
        #     receiver_rssi=message.rssi,
        #     receiver_hops=message.hops,
        #     receiver_path=message.path,
        # )

        # if should_respond is True:
        #     # Coordinator says we should respond
        #     logger.info(f"Coordinator assigned response to us for: {content_prefix}")
        #     result = await self._original_send_response(message, content, **kwargs)
        #     await self._report_message(message, bot_responded=result, message_hash=message_hash)
        #     return result

        # if should_respond is False:
        #     # Coordinator assigned to another bot
        #     logger.info(f"Coordinator assigned response to another bot for: {content_prefix}")
        #     await self._report_message(message, bot_responded=False, message_hash=message_hash)
        #     return True  # Return True so command doesn't report failure

        # # should_respond is None - coordinator unreachable, use fallback
        # logger.info("Coordinator unreachable, using score-based fallback")
        # await self.fallback.wait_before_responding()
        # result = await self._original_send_response(message, content, **kwargs)
        # await self._report_message(message, bot_responded=result, message_hash=message_hash)
        # return result

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
