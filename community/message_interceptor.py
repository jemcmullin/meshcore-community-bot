"""Patches three MessageHandler/CommandManager methods via MethodType so
coordination, path observation, and reporting all work without touching the
submodule.

Patches installed:
  handle_rf_log_data  — primary path-observation source; fires on every raw RF
                        frame and has routing_info (path_nodes) already decoded.
  process_message     — secondary source for the high-level MeshMessage path
                        field on channel messages that happen to be commands.
  send_response       — coordinator bidding gate; also drives PacketReporter.

Channel message flow through send_response:
  1. DB queried for outbound hops + path significance.
  2. Proximity score sent to coordinator (300 ms bidding window).
  3. Coordinator assigns response to one bot; others suppress.
  4. Falls back to signal-aware delay if coordinator unreachable.
DMs bypass coordination entirely.
"""

import logging
import time
from types import MethodType
from typing import Optional

from .coordinator_client import CoordinatorClient
from .coverage_fallback import CoverageFallback

logger = logging.getLogger("CommunityBot")


class MessageInterceptor:
    """Monkey-patches bot methods to add coordination, path observation, and reporting."""

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

        # --- Patch process_message (secondary path-observation source) ---
        self._original_process_message = None
        if hasattr(bot, "message_handler"):
            self._original_process_message = bot.message_handler.process_message

            async def _bound_process_message(mh_self, message):
                return await self._observing_process_message(message)

            bot.message_handler.process_message = MethodType(
                _bound_process_message, bot.message_handler
            )
            logger.info("Message interceptor installed on MessageHandler.process_message")

        # --- Patch handle_rf_log_data (primary path-observation source) ---
        # Fires on every raw RF frame; routing_info is decoded here before
        # process_message and for packet types that never reach process_message
        # (ADVERT, non-command TXT_MSG, etc.).
        self._original_handle_rf_log_data = None
        if hasattr(bot, "message_handler") and self.network_observer is not None:
            self._original_handle_rf_log_data = bot.message_handler.handle_rf_log_data

            async def _bound_handle_rf_log_data(mh_self, event, metadata=None):
                return await self._observing_handle_rf_log_data(event, metadata)

            bot.message_handler.handle_rf_log_data = MethodType(
                _bound_handle_rf_log_data, bot.message_handler
            )
            logger.info("Message interceptor installed on MessageHandler.handle_rf_log_data")

        logger.info("Message interceptor installed on CommandManager.send_response")

    # ------------------------------------------------------------------
    # process_message patch — secondary path-observation source
    # ------------------------------------------------------------------

    async def _observing_process_message(self, message):
        """Increment messages_processed_count, then run the original.

        Path observation is intentionally omitted here — handle_rf_log_data
        already feeds every decoded path to NetworkObserver (including packet
        types that never reach process_message).  Observing again here would
        double-count the same path nodes for command messages.
        """
        assert self._original_process_message is not None
        if hasattr(self.bot, "messages_processed_count"):
            self.bot.messages_processed_count += 1

        return await self._original_process_message(message)

    # ------------------------------------------------------------------
    # handle_rf_log_data patch — primary path-observation source
    # ------------------------------------------------------------------

    async def _observing_handle_rf_log_data(self, event, metadata=None):
        """Run the original handler, then feed its decoded path to NetworkObserver.

        The original appends to recent_rf_data with routing_info already
        populated.  Snapshot the list length first so we can detect whether a
        new entry was added — guarding against reading a stale [-1] entry when a
        packet had no raw_hex and was never appended.  No duplicate decode and no
        deep copy needed: asyncio is single-threaded so nothing mutates the list
        between the awaited return and our read.
        """
        mh = self.bot.message_handler
        # Snapshot time before the call so we can identify the newly-appended entry
        # afterward.  We cannot use len() because _cleanup_stale_cache_entries runs
        # inside handle_rf_log_data and replaces recent_rf_data with a new filtered
        # list — so len can decrease even when a new entry was added.
        t_before = time.time()

        assert self._original_handle_rf_log_data is not None
        result = await self._original_handle_rf_log_data(event, metadata)

        if self.network_observer is not None:
            try:
                rf_list = getattr(mh, "recent_rf_data", [])
                # The newly-appended entry (if any) will have timestamp >= t_before.
                # Entries added before this call will all be < t_before.
                if rf_list and rf_list[-1].get("timestamp", 0) >= t_before:
                    path_nodes = (rf_list[-1].get("routing_info") or {}).get("path_nodes", [])
                    if path_nodes:
                        self.network_observer.observe_path(path_nodes)
                        logger.debug(
                            "NetworkObserver fed %d path nodes from RF log: %s",
                            len(path_nodes),
                            path_nodes,
                        )
            except Exception:
                pass  # Never let observer errors block the original handler

        return result

    # ------------------------------------------------------------------
    # send_response patch — coordinator bidding + reporting
    # ------------------------------------------------------------------

    async def _coordinated_send_response(self, message, content: str, **kwargs) -> bool:
        """Gate send_response through the coordinator bidding window."""
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

        # Compute and log the proximity score that will be sent to the coordinator
        from .coordinator_client import CoordinatorClient as _CC
        proximity_score = _CC.compute_sender_proximity_score(
            inbound_hops=message.hops,
            outbound_hops=outbound_hops,
            path_significance=path_significance,
        )
        path_sig_str = f"{path_significance:.2f}" if path_significance is not None else "N/A"
        logger.info(
            f"Coordinator bid [{content_prefix}] "
            f"in_hops={message.hops} out_hops={outbound_hops} "
            f"path_sig={path_sig_str} proximity={proximity_score:.3f} "
            f"snr={message.snr} rssi={message.rssi} path={message.path!r}"
        )
        logger.debug(
            f"Scoring detail sender={message.sender_pubkey or 'unknown'!r:.12} "
            f"hash={message_hash[:12]} channel={message.channel}"
        )

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
        """Restore all patched methods to their originals."""
        self.bot.command_manager.send_response = self._original_send_response
        if self._original_process_message is not None and hasattr(self.bot, "message_handler"):
            self.bot.message_handler.process_message = self._original_process_message
        if self._original_handle_rf_log_data is not None and hasattr(self.bot, "message_handler"):
            self.bot.message_handler.handle_rf_log_data = self._original_handle_rf_log_data
        logger.info("Message interceptor removed")
