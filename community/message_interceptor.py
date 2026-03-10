"""Patches MessageHandler/CommandManager methods via MethodType.

send_response uses path-familiarity scoring for coordinator bidding.
DMs bypass coordination.
"""

import asyncio
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

            # --- DM metrics event for web viewer ---
            try:
                import json
                import sqlite3
                wvi = getattr(self.bot, "web_viewer_integration", None)
                if wvi:
                    db_path = wvi._get_web_viewer_db_path() if hasattr(wvi, "_get_web_viewer_db_path") else self.bot.db_manager.db_path
                    command_id = f"dm_{message.sender_id or 'unknown'}"
                    dm_event = {
                        "command_id": command_id,
                        "user": message.sender_id or "Unknown",
                        "success": bool(result),
                        "timestamp": int(message.timestamp or time.time()),
                        "content": (message.content or "")[:100],
                    }
                    def _insert():
                        conn = sqlite3.connect(str(db_path), timeout=60.0)
                        try:
                            cursor = conn.cursor()
                            cursor.execute(
                                "INSERT INTO packet_stream (timestamp, data, type) VALUES (?, ?, ?)",
                                (float(dm_event["timestamp"]), json.dumps(dm_event), "command"),
                            )
                            conn.commit()
                        finally:
                            conn.close()
                    await asyncio.to_thread(_insert)
            except Exception as e:
                logger.debug(f"Failed to log DM event for web viewer: {e}")
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
        infrastructure, path_bonus, path_freshness = await self._get_path_metrics(message)

        # Compute and log the delivery score that will be sent to the coordinator
        from .coordinator_client import CoordinatorClient as _CC
        scoring_cfg = getattr(self.bot, "scoring_config", None)
        w_infrastructure = (
            scoring_cfg.infrastructure_weight if scoring_cfg else 0.40
        )
        w_hops = scoring_cfg.hop_weight if scoring_cfg else 0.35
        w_path_bonus = scoring_cfg.path_bonus_weight if scoring_cfg else 0.15
        w_freshness = scoring_cfg.freshness_weight if scoring_cfg else 0.10

        delivery_score = _CC.compute_delivery_score(
            infrastructure=infrastructure,
            inbound_hops=message.hops,
            path_bonus=path_bonus,
            path_freshness=path_freshness,
            w_infrastructure=w_infrastructure,
            w_hops=w_hops,
            w_path_bonus=w_path_bonus,
            w_freshness=w_freshness,
        )
        hop_score = 1.0 / (1.0 + message.hops) if message.hops is not None else 0.5
        infra_score = infrastructure if infrastructure is not None else 0.5
        path_bonus_score = path_bonus if path_bonus is not None else 0.0
        freshness_score = path_freshness if path_freshness is not None else 0.5

        hop_component = hop_score * w_hops
        infra_component = infra_score * w_infrastructure
        path_bonus_component = path_bonus_score * w_path_bonus
        freshness_component = freshness_score * w_freshness
        logger.info(
            f"Coordinator bid [{content_prefix}] "
            f"in_hops={message.hops} infra={infrastructure} "
            f"path_bonus={path_bonus} "
            f"freshness={path_freshness} delivery={delivery_score:.3f} "
            f"path={message.path!r}"
        )
        logger.info(
            "Score breakdown [%s] infra=%.3f*%.2f=%.3f hop=%.3f*%.2f=%.3f "
            "path_bonus=%.3f*%.2f=%.3f freshness=%.3f*%.2f=%.3f total=%.3f",
            content_prefix,
            infra_score,
            w_infrastructure,
            infra_component,
            hop_score,
            w_hops,
            hop_component,
            path_bonus_score,
            w_path_bonus,
            path_bonus_component,
            freshness_score,
            w_freshness,
            freshness_component,
            delivery_score,
        )
        await self._publish_web_viewer_coordination_event(
            message=message,
            message_hash=message_hash,
            stage="bid",
            delivery_score=delivery_score,
            inbound_hops=message.hops,
            infrastructure=infrastructure,
            path_bonus=path_bonus,
            path_freshness=path_freshness,
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
            infrastructure=infrastructure,
            path_bonus=path_bonus,
            path_freshness=path_freshness,
            w_infrastructure=w_infrastructure,
            w_hops=w_hops,
            w_path_bonus=w_path_bonus,
            w_freshness=w_freshness,
        )

        if should_respond is True:
            logger.info(f"Coordinator assigned response to us for: {content_prefix}")
            await self._publish_web_viewer_coordination_event(
                message=message,
                message_hash=message_hash,
                stage="assigned_us",
                delivery_score=delivery_score,
                inbound_hops=message.hops,
                infrastructure=infrastructure,
                path_bonus=path_bonus,
                path_freshness=path_freshness,
            )
            result = await self._original_send_response(message, content, **kwargs)
            if hasattr(self.bot, "messages_responded_count"):
                self.bot.messages_responded_count += 1
            await self._report_message(message, bot_responded=result, message_hash=message_hash)
            return result

        if should_respond is False:
            logger.info(f"Coordinator assigned response to another bot for: {content_prefix}")
            await self._publish_web_viewer_coordination_event(
                message=message,
                message_hash=message_hash,
                stage="assigned_other",
                delivery_score=delivery_score,
                inbound_hops=message.hops,
                infrastructure=infrastructure,
                path_bonus=path_bonus,
                path_freshness=path_freshness,
            )
            await self._report_message(message, bot_responded=False, message_hash=message_hash)
            return True  # don't surface as failure to command

        # Coordinator unreachable — delivery-score-aware fallback delay
        logger.info("Coordinator unreachable, using delivery-score-aware fallback")
        min_fallback_score = (
            scoring_cfg.fallback_min_delivery_score if scoring_cfg else 0.30
        )
        if delivery_score < min_fallback_score:
            logger.info(
                "Fallback suppressed [%s]: delivery_score=%.3f below threshold=%.3f",
                content_prefix,
                delivery_score,
                min_fallback_score,
            )
            await self._publish_web_viewer_coordination_event(
                message=message,
                message_hash=message_hash,
                stage="fallback_suppressed",
                delivery_score=delivery_score,
                inbound_hops=message.hops,
                infrastructure=infrastructure,
                path_bonus=path_bonus,
                path_freshness=path_freshness,
            )
            await self._report_message(message, bot_responded=False, message_hash=message_hash)
            return True  # command handled; intentionally silenced in fallback

        await self._publish_web_viewer_coordination_event(
            message=message,
            message_hash=message_hash,
            stage="fallback",
            delivery_score=delivery_score,
            inbound_hops=message.hops,
            infrastructure=infrastructure,
            path_bonus=path_bonus,
            path_freshness=path_freshness,
        )
        await self.fallback.wait_before_responding_with_signal(
            hops=message.hops,
            infrastructure=infrastructure,
            path_bonus=path_bonus,
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
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Return (infrastructure, path_bonus, path_freshness).
        All values may be None; None is treated as neutral (0.5) in scoring.
        Sources: mesh_connections and observed_paths.
        """
        sender_pubkey = message.sender_pubkey or ""
        sender_prefix2 = sender_pubkey[:2].lower() if sender_pubkey else ""
        path_nodes = CoordinatorClient.parse_path_nodes(getattr(message, "path", None))
        path_hex = "".join(path_nodes).lower() if path_nodes else ""

        infrastructure: Optional[float] = None
        path_bonus: Optional[float] = None
        path_freshness: Optional[float] = None

        # Infrastructure: harmonic mean across path-node fan-in scores.
        if path_nodes:
            try:
                node_lower = [n.lower()[:2] for n in path_nodes]
                placeholders = ",".join("?" * len(node_lower))
                rows = await self.bot.db_manager.aexecute_query(
                    f"""SELECT to_prefix,
                               COUNT(DISTINCT from_prefix) AS fan_in,
                               (SELECT MAX(c)
                                FROM (SELECT COUNT(DISTINCT from_prefix) AS c
                                      FROM mesh_connections
                                      GROUP BY to_prefix)) AS max_fan_in
                        FROM mesh_connections
                        WHERE to_prefix IN ({placeholders})
                        GROUP BY to_prefix""",
                    tuple(node_lower),
                    fetch=True,
                )
                if rows:
                    max_fan_in = max(rows[0][2] or 1, 1)
                    log_max_fan = math.log1p(max_fan_in)
                    fan_in_by_node = {r[0]: (r[1] or 0) for r in rows}
                    node_scores = []
                    for node in node_lower:
                        fan_in = fan_in_by_node.get(node)
                        if fan_in is None:
                            node_scores.append(0.5)
                        else:
                            node_scores.append(math.log1p(fan_in) / log_max_fan)
                    if node_scores and min(node_scores) > 0:
                        infrastructure = len(node_scores) / sum(1.0 / x for x in node_scores)
                    elif node_scores:
                        infrastructure = 0.0
            except Exception as e:
                logger.debug(f"Could not compute infrastructure score: {e}")

        # Exact-path bonus and sender freshness from observed_paths.
        if sender_prefix2:
            try:
                if path_hex:
                    exact_rows = await self.bot.db_manager.aexecute_query(
                        """SELECT 1
                           FROM observed_paths
                                                     WHERE LOWER(from_prefix) = ?
                                                         AND LOWER(path_hex) = ?
                             AND packet_type = 'message'
                           LIMIT 1""",
                        (sender_prefix2, path_hex),
                        fetch=True,
                    )
                    path_bonus = 1.0 if exact_rows else 0.0

                rows = await self.bot.db_manager.aexecute_query(
                    """SELECT CAST((julianday('now', 'localtime') - julianday(last_seen)) * 24 AS REAL)
                       FROM observed_paths
                                             WHERE LOWER(from_prefix) = ?
                         AND packet_type = 'message'
                       ORDER BY last_seen DESC LIMIT 1""",
                    (sender_prefix2,),
                    fetch=True,
                )
                if rows:
                    age_hours = rows[0][0]
                    path_freshness = math.exp(-((age_hours or 999) / 24.0))
            except Exception as e:
                logger.debug(f"Could not fetch path familiarity/freshness: {e}")

        logger.info(
            "Path metrics sender=%s in_hops=%s infra=%s path_bonus=%s freshness=%s path_hex=%s",
            sender_prefix2 or "??",
            getattr(message, "hops", None),
            None if infrastructure is None else round(infrastructure, 3),
            None if path_bonus is None else round(path_bonus, 3),
            None if path_freshness is None else round(path_freshness, 3),
            path_hex,
        )

        return infrastructure, path_bonus, path_freshness

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

            # Store message data locally in packet_stream for web viewer
            await self._store_message_for_viewer(
                message=message,
                message_hash=message_hash,
                timestamp=timestamp,
                was_command=was_command,
                command_name=command_name,
                bot_responded=bot_responded,
            )

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

    async def _publish_web_viewer_coordination_event(
        self,
        message,
        message_hash: str,
        stage: str,
        delivery_score: float,
        inbound_hops: Optional[int],
        infrastructure: Optional[float],
        path_bonus: Optional[float],
        path_freshness: Optional[float],
    ) -> None:
        """Publish coordination score snapshots to web viewer command stream.

        Uses existing BotIntegration.capture_command() so no submodule changes are required.
        """
        wvi = getattr(self.bot, "web_viewer_integration", None)
        if not wvi or not getattr(wvi, "bot_integration", None):
            return

        summary = (
            f"stage={stage} score={delivery_score:.3f} in={inbound_hops} "
            f"infra={infrastructure if infrastructure is not None else 'n/a'} "
            f"path_bonus={path_bonus if path_bonus is not None else 'n/a'} "
            f"fresh={path_freshness if path_freshness is not None else 'n/a'}"
        )

        command_id = f"coord:{message_hash[:12]}"
        try:
            await asyncio.to_thread(
                wvi.bot_integration.capture_command,
                message,
                f"coord_{stage}",
                summary,
                True,
                command_id,
            )
        except Exception as e:
            logger.debug(f"Failed to publish coordination event to web viewer: {e}")

    async def _store_message_for_viewer(
        self,
        message,
        message_hash: str,
        timestamp: int,
        was_command: bool,
        command_name: Optional[str],
        bot_responded: bool,
    ) -> None:
        """Store message data in packet_stream table for web viewer analytics.

        Creates a 'message' type entry similar to how coordination events are stored as 'command' type.
        """
        wvi = getattr(self.bot, "web_viewer_integration", None)
        if not wvi:
            return

        try:
            import json
            import sqlite3

            message_data = {
                "message_hash": message_hash,
                "sender_name": message.sender_id or "",
                "sender_pubkey": message.sender_pubkey or "",
                "channel": message.channel,
                "content": (message.content or "")[:100],  # Truncate for privacy/space
                "is_dm": message.is_dm,
                "hops": message.hops,
                "path": message.path,
                "snr": message.snr,
                "rssi": message.rssi,
                "timestamp": timestamp,
                "was_command": was_command,
                "command_name": command_name,
                "bot_responded": bot_responded,
            }

            # Insert into packet_stream via helper method
            db_path = wvi._get_web_viewer_db_path() if hasattr(wvi, "_get_web_viewer_db_path") else self.bot.db_manager.db_path
            
            def _insert():
                conn = sqlite3.connect(str(db_path), timeout=60.0)
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO packet_stream (timestamp, data, type) VALUES (?, ?, ?)",
                        (float(timestamp), json.dumps(message_data), "message"),
                    )
                    conn.commit()
                finally:
                    conn.close()
            
            await asyncio.to_thread(_insert)
        except Exception as e:
            logger.debug(f"Failed to store message for web viewer: {e}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def restore(self):
        """Restore all patched methods to their originals."""
        self.bot.command_manager.send_response = self._original_send_response
        if self._original_process_message is not None and hasattr(self.bot, "message_handler"):
            self.bot.message_handler.process_message = self._original_process_message
        logger.info("Message interceptor removed")
