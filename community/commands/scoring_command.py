"""Owner-facing scoring snapshot."""

import asyncio
from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class ScoringCommand(BaseCommand):
    """Bid-score diagnostics for bot owners."""

    name = "scoring"
    keywords = ["score", "scoring", "repeaters"]
    description = "Shows top infra relays and simple bid-health metrics"
    requires_dm = True
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            def load_metrics():
                infra_rows = self.bot.db_manager.execute_query(
                    """SELECT mc.to_prefix,
                              COUNT(DISTINCT mc.from_prefix) AS fan_in,
                              CAST((julianday('now') - julianday(MAX(mc.last_seen))) * 24 AS REAL) AS age_hours
                       FROM mesh_connections mc
                       GROUP BY mc.to_prefix
                       ORDER BY fan_in DESC
                       LIMIT 8"""
                )

                return infra_rows

            infra_rows = await asyncio.to_thread(load_metrics)

            if not infra_rows:
                await self.send_response(message, "No infrastructure data yet. Wait for mesh traffic.")
                return True

            top_nodes = []
            stale_nodes = 0
            for row in infra_rows:
                node = (row.get("to_prefix") or "").upper().replace("!", "")[:4]
                fan_in = int(row.get("fan_in") or 0)
                age_hours = float(row.get("age_hours") or 999)
                hops = row.get("hops")
                if age_hours > 48:
                    stale_nodes += 1
                top_nodes.append((node, fan_in, hops))

            # Keep output within radio-safe message length.
            max_len = self.get_max_message_length(message)

            lines = [f"{'Node':<4} {'Links':>5} {'Hops':>5}"]
            for node, links, hops in top_nodes[:4]:
                hop_str = f"{hops}h" if hops is not None else "?h"
                lines.append(f"{node:<4} {links:>5} {hop_str:>5}")

            if stale_nodes > 0:
                lines.append(f"Stale: {stale_nodes}")

            text = "\n".join(lines)
            if len(text) > max_len:
                text = text[: max_len - 3] + "..."

            await self.send_response(message, text)
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting scoring diagnostics")
            return False
