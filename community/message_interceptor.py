"""Patches two MessageHandler/CommandManager methods via MethodType so
coordination and reporting work without touching the submodule.

Patches installed:
  process_message  — increments messages_processed_count counter.
  send_response    — coordinator bidding gate; also drives PacketReporter.

Channel message flow through send_response:
  1. DB queried for outbound hops, infrastructure, reliability, freshness.
  2. Delivery score sent to coordinator (300 ms bidding window).
  3. Coordinator assigns response to one bot; others suppress.
  4. Falls back to delivery-score-aware delay if coordinator unreachable.
DMs bypass coordination entirely.
"""

import logging
import math
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
    ):
        self.bot = bot
        self.coordinator = coordinator
        self.fallback = fallback
        self.reporter = reporter

        # --- Patch send_response ---
        self._original_send_response = bot.command_manager.send_response

        async def _bound_send_response(cm_self, message, content: str, **kwargs):
            return await self._coordinated_send_response(message, content, **kwargs)

        bot.command_manager.send_response = MethodType(
            _bound_send_response, bot.command_manager
        )

        # --- Patch process_message (messages_processed_count counter) ---
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
    # process_message patch — messages_processed_count counter
    # ------------------------------------------------------------------

    async def _observing_process_message(self, message):
        """Increment messages_processed_count, then run the original."""
        assert self._original_process_message is not None
        if hasattr(self.bot, "messages_processed_count"):
            self.bot.messages_processed_count += 1

        return await self._original_process_message(message)

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

        # Get path metrics for delivery scoring
        outbound_hops, infrastructure, path_reliability, path_freshness = \
            await self._get_path_metrics(message)

        # Compute and log the delivery score that will be sent to the coordinator
        from .coordinator_client import CoordinatorClient as _CC
        scoring_cfg = getattr(self.bot, "scoring_config", None)
        w_hops        = scoring_cfg.hop_weight        if scoring_cfg else 0.50
        w_infra       = scoring_cfg.infra_weight      if scoring_cfg else 0.25
        w_reliability = scoring_cfg.reliability_weight if scoring_cfg else 0.15
        w_freshness   = scoring_cfg.freshness_weight   if scoring_cfg else 0.10

        delivery_score = _CC.compute_delivery_score(
            inbound_hops=message.hops,
            outbound_hops=outbound_hops,
            infrastructure=infrastructure,
            path_reliability=path_reliability,
            path_freshness=path_freshness,
            w_hops=w_hops,
            w_infra=w_infra,
            w_reliability=w_reliability,
            w_freshness=w_freshness,
        )
        logger.info(
            f"Coordinator bid [{content_prefix}] "
            f"in_hops={message.hops} out_hops={outbound_hops} "
            f"infra={infrastructure} reliability={path_reliability} "
            f"freshness={path_freshness} delivery={delivery_score:.3f} "
            f"path={message.path!r}"
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
            receiver_hops=message.hops,
            outbound_hops=outbound_hops,
            infrastructure=infrastructure,
            path_reliability=path_reliability,
            path_freshness=path_freshness,
            w_hops=w_hops,
            w_infra=w_infra,
            w_reliability=w_reliability,
            w_freshness=w_freshness,
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

        # Coordinator unreachable — delivery-score-aware fallback delay
        logger.info("Coordinator unreachable, using delivery-score-aware fallback")
        await self.fallback.wait_before_responding_with_signal(
            hops=message.hops,
            outbound_hops=outbound_hops,
            infrastructure=infrastructure,
            path_reliability=path_reliability,
            path_freshness=path_freshness,
        )
        result = await self._original_send_response(message, content, **kwargs)
        if hasattr(self.bot, "messages_responded_count"):
            self.bot.messages_responded_count += 1
        await self._report_message(message, bot_responded=result, message_hash=message_hash)
        return result

    # ------------------------------------------------------------------
    # Path metrics
    # ------------------------------------------------------------------

    async def _get_path_metrics(
        self, message
    ) -> tuple[Optional[int], Optional[float], Optional[float], Optional[float]]:
        """
        Return (outbound_hops, infrastructure, path_reliability, path_freshness).
        All values may be None; None is treated as neutral (0.5) in scoring.
        Sources: complete_contact_tracking, mesh_connections, observed_paths.
        """
        sender_pubkey  = message.sender_pubkey or ""
        sender_prefix8 = sender_pubkey[:8].upper() if sender_pubkey else ""
        sender_prefix2 = sender_pubkey[:2].lower()  if sender_pubkey else ""
        path_nodes = CoordinatorClient.parse_path_nodes(getattr(message, "path", None))

        outbound_hops:    Optional[int]   = None
        infrastructure:   Optional[float] = None
        path_reliability: Optional[float] = None
        path_freshness:   Optional[float] = None

        # --- outbound_hops from complete_contact_tracking ---
        if sender_prefix8:
            try:
                rows = await self.bot.db_manager.aexecute_query(
                    """SELECT out_path_len FROM complete_contact_tracking
                       WHERE public_key LIKE ? AND out_path_len IS NOT NULL
                       ORDER BY last_heard DESC LIMIT 1""",
                    (sender_prefix8 + "%",),
                    fetch=True,
                )
                if rows and rows[0][0] is not None:
                    outbound_hops = int(rows[0][0])
            except Exception as e:
                logger.debug(f"Could not fetch outbound_hops: {e}")

        # --- infrastructure from mesh_connections fan-in ---
        # Normalized against the total distinct nodes in the network (not the max
        # fan-in). This prevents saturation: a local feeder that only ever routes
        # for 2 nodes scores ~0.36 in a 20-node network and ~0.24 in a 100-node
        # network — it cannot inflate its way to 1.0 as data accumulates.
        # log1p compresses the power-law skew; backbone nodes (high fraction of
        # total) naturally anchor near 1.0 while local feeders stay proportionally low.
        if path_nodes:
            try:
                node_lower = [n.lower()[:2] for n in path_nodes]
                placeholders = ",".join("?" * len(node_lower))
                rows = await self.bot.db_manager.aexecute_query(
                    f"""SELECT to_prefix,
                               COUNT(DISTINCT from_prefix) AS fan_in,
                               (SELECT COUNT(DISTINCT from_prefix)
                                FROM mesh_connections) AS total_nodes
                        FROM mesh_connections
                        WHERE to_prefix IN ({placeholders})
                        GROUP BY to_prefix""",
                    tuple(node_lower),
                    fetch=True,
                )
                if rows:
                    total_nodes = max(rows[0][2] or 1, 1)
                    log_total = math.log1p(total_nodes)
                    scores = [math.log1p(r[1] or 0) / log_total for r in rows]
                    infrastructure = sum(scores) / len(scores)
            except Exception as e:
                logger.debug(f"Could not compute infrastructure score: {e}")

        # --- path_reliability + path_freshness from observed_paths ---
        if sender_prefix2:
            try:
                rows = await self.bot.db_manager.aexecute_query(
                    """SELECT observation_count,
                              CAST((julianday('now') - julianday(last_seen)) * 24 AS REAL)
                       FROM observed_paths
                       WHERE from_prefix = ? AND packet_type != 'ADVERT'
                       ORDER BY observation_count DESC LIMIT 1""",
                    (sender_prefix2,),
                    fetch=True,
                )
                if rows:
                    obs_count, age_hours = rows[0]
                    path_reliability = min(1.0, (obs_count or 1) / 20.0)
                    path_freshness   = math.exp(-(age_hours or 999) / 6.0)
            except Exception as e:
                logger.debug(f"Could not fetch path reliability/freshness: {e}")

        return outbound_hops, infrastructure, path_reliability, path_freshness

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
        logger.info("Message interceptor removed")
