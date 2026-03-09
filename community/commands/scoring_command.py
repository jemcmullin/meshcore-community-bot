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

                hop_stats = self.bot.db_manager.execute_query(
                    """SELECT AVG(CASE WHEN out_path_len > 0 THEN out_path_len ELSE in_path_len END) AS avg_hops
                       FROM complete_contact_tracking
                       WHERE (julianday('now') - julianday(updated)) * 24 <= 48"""
                )

                return infra_rows, hop_stats

            infra_rows, hop_stats = await asyncio.to_thread(load_metrics)

            if not infra_rows:
                await self.send_response(message, "No infrastructure data yet. Wait for mesh traffic.")
                return True

            top_nodes = []
            stale_nodes = 0
            for row in infra_rows:
                node = (row.get("to_prefix") or "").upper().replace("!", "")[:4]
                fan_in = int(row.get("fan_in") or 0)
                age_hours = float(row.get("age_hours") or 999)
                if age_hours > 48:
                    stale_nodes += 1
                top_nodes.append(f"{node}({fan_in})")

            hop_row = hop_stats[0] if hop_stats else {}
            avg_hops = float(hop_row.get("avg_hops") or 0)

            # Keep output within radio-safe message length.
            max_len = self.get_max_message_length(message)
            
            lines = ["Top   Links"]
            for node_str in top_nodes[:4]:
                # node_str is like "A1B2(28)"
                parts = node_str.split("(")
                node = parts[0]
                links = parts[1].rstrip(")")
                lines.append(f"{node:<4}  {links}")
            
            footer = []
            if stale_nodes > 0:
                footer.append(f"Stale: {stale_nodes}")
            footer.append(f"AvgHop: {avg_hops:.1f}")
            lines.append("  ".join(footer))
            
            text = "\n".join(lines)
            if len(text) > max_len:
                text = text[: max_len - 3] + "..."

            await self.send_response(message, text)
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting scoring diagnostics")
            return False
