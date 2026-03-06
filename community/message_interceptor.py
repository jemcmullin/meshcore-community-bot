"""Intercepts bot responses to add coordinator-based coordination.

Patches CommandManager.send_response() and MessageHandler.process_message()
via types.MethodType so coordination and passive path observation both work
without touching the submodule.

DMs bypass coordination. Channel messages:
  1. Path fed to NetworkObserver for repeater learning.
  2. DB queried for outbound hops + path significance.
  3. Proximity score sent to coordinator (300 ms bidding window).
  4. Falls back to signal-aware delay if coordinator unreachable.
Also reports messages to PacketReporter for batch ingestion.
"""

import logging
import time
from types import MethodType
from typing import Optional

from .coordinator_client import CoordinatorClient
from .coverage_fallback import CoverageFallback

logger = logging.getLogger(__name__)


class MessageInterceptor:
    """Intercepts send_response and process_message to coordinate responses."""

    def __init__(
        self,
        bot,
        coordinator: CoordinatorClient,
        fallback: CoverageFallback,
        reporter=None,
        network_observer=None,
    ):
        self.bot = bot
        self.coordinator = coordinator
        self.fallback = fallback
        self.reporter = reporter
        self.network_observer = network_observer

        # --- Patch send_response ---
        self._original_send_response = bot.command_manager.send_response

        async def _bound_send_response(cm_self, message, content: str, **kwargs):
            return await self._coordinated_send_response(message, content, **kwargs)

        bot.command_manager.send_response = MethodType(
            _bound_send_response, bot.command_manager
        )

        # --- Patch process_message if we have an observer ---
        self._original_process_message = None
        if hasattr(bot, "message_handler"):
            self._original_process_message = bot.message_handler.process_message

            async def _bound_process_message(mh_self, message):
                return await self._observing_process_message(message)

            bot.message_handler.process_message = MethodType(
                _bound_process_message, bot.message_handler
            )
            logger.info("Message interceptor installed on MessageHandler.process_message")

        logger.info("Message interceptor installed on CommandManager.send_response")

    # ------------------------------------------------------------------
    # process_message wrapper — feeds every channel message to observer
    # ------------------------------------------------------------------

    async def _observing_process_message(self, message):
        """Wrap process_message to feed path data to NetworkObserver."""
        if (
            self.network_observer is not None
            and not getattr(message, "is_dm", True)
            and getattr(message, "path", None)
        ):
            path_nodes = CoordinatorClient.parse_path_nodes(message.path)
            if path_nodes:
                self.network_observer.observe_path(path_nodes)

        if hasattr(self.bot, "messages_processed_count"):
            self.bot.messages_processed_count += 1

        return await self._original_process_message(message)

    # ------------------------------------------------------------------
    # send_response wrapper — coordinator bidding + fallback
    # ------------------------------------------------------------------

    async def _coordinated_send_response(self, message, content: str, **kwargs) -> bool:
        """Coordinated version of send_response."""
        # DMs always go through
        if message.is_dm:
            result = await self._original_send_response(message, content, **kwargs)
            await self._report_message(message, bot_responded=result)
            return result

        # No coordinator configured — send immediately
        if not self.coordinator.is_configured:
            result = await self._original_send_response(message, content, **kwargs)
            await self._report_message(message, bot_responded=result)
            return result

        timestamp = message.timestamp or int(time.time())
        message_hash = CoordinatorClient.compute_message_hash(
            sender_pubkey=message.sender_pubkey or "",
            content=message.content or "",
            timestamp=timestamp,
        )
        words = (message.content or "").split()
        content_prefix = words[0][:50] if words else ""

        # Get path metrics for proximity scoring
        outbound_hops, path_significance = await self._get_path_metrics(message)

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
            outbound_hops=outbound_hops,
            path_significance=path_significance,
        )

        if should_respond is True:
            logger.info(f"Coordinator assigned response to us for: {content_prefix}")
            result = await self._original_send_response(message, content, **kwargs)
            if hasattr(self.bot, "messages_responded_count"):
                self.bot.messages_responded_count += 1
            await self._report_message(message, bot_responded=result, message_hash=message_hash)
            return result

        if should_respond is False:
            logger.info(f"Coordinator assigned response to another bot for: {content_prefix}")
            await self._report_message(message, bot_responded=False, message_hash=message_hash)
            return True  # don't surface as failure to command

        # Coordinator unreachable — signal-aware fallback delay
        logger.info("Coordinator unreachable, using signal-aware fallback")
        await self.fallback.wait_before_responding_with_signal(
            hops=message.hops,
            outbound_hops=outbound_hops,
            path_significance=path_significance,
        )
        result = await self._original_send_response(message, content, **kwargs)
        if hasattr(self.bot, "messages_responded_count"):
            self.bot.messages_responded_count += 1
        await self._report_message(message, bot_responded=result, message_hash=message_hash)
        return result

    # ------------------------------------------------------------------
    # Path metrics
    # ------------------------------------------------------------------

    async def _get_path_metrics(self, message) -> tuple[Optional[int], Optional[float]]:
        """Return (outbound_hops, path_significance) for the message sender."""
        outbound_hops: Optional[int] = None
        path_significance: Optional[float] = None

        # Query outbound hop count from DB
        try:
            sender_pubkey = message.sender_pubkey or ""
            if sender_pubkey:
                rows = await self.bot.db_manager.aexecute_query(
                    """
                    SELECT out_path_len FROM complete_contact_tracking
                    WHERE pubkey_prefix = ?
                    ORDER BY last_seen DESC
                    LIMIT 1
                    """,
                    (sender_pubkey[:8],),
                    fetch=True,
                )
                if rows and rows[0][0] is not None:
                    outbound_hops = int(rows[0][0])
        except Exception as e:
            logger.debug(f"Could not fetch outbound_hops: {e}")

        # Compute path significance from observer
        if self.network_observer is not None and getattr(message, "path", None):
            try:
                path_nodes = CoordinatorClient.parse_path_nodes(message.path)
                path_significance = self.network_observer.compute_path_significance(path_nodes)
            except Exception as e:
                logger.debug(f"Could not compute path_significance: {e}")

        return outbound_hops, path_significance

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def _report_message(self, message, bot_responded: bool = False, message_hash: str = ""):
        """Report the message to PacketReporter for batch ingestion."""
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
            words = (message.content or "").split()
            content_prefix = words[0].lower() if words else ""
            was_command = bool(content_prefix)
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

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def restore(self):
        """Restore original patched methods."""
        self.bot.command_manager.send_response = self._original_send_response
        if self._original_process_message is not None and hasattr(self.bot, "message_handler"):
            self.bot.message_handler.process_message = self._original_process_message
        logger.info("Message interceptor removed")
