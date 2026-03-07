"""Scoring command - shows top repeaters by infrastructure score (mesh_connections fan-in)."""

import math

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class ScoringCommand(BaseCommand):
    """Shows the top 5 repeaters by infrastructure score from mesh_connections fan-in."""

    name = "scoring"
    keywords = ["scoring"]
    description = "Shows top 5 repeaters by infrastructure score"
    requires_dm = True
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            rows = await self.bot.db_manager.aexecute_query(
                """SELECT to_prefix,
                          COUNT(DISTINCT from_prefix) AS fan_in,
                          (SELECT COUNT(DISTINCT from_prefix)
                           FROM mesh_connections) AS total_nodes
                   FROM mesh_connections
                   GROUP BY to_prefix
                   ORDER BY fan_in DESC
                   LIMIT 5""",
                fetch=True,
            )
            if not rows:
                await self.send_response(message, "No repeater data available yet")
                return True

            total_nodes = max(rows[0][2] or 1, 1)
            log_total = math.log1p(total_nodes)

            lines = ["**Top Repeaters (Infrastructure Score)**", ""]
            
            # Summary header
            lines.append(f"Network: {total_nodes} known nodes")
            lines.append("")

            # Detailed rankings
            for rank, (node_id, fan_in, _) in enumerate(rows, start=1):
                score = math.log1p(fan_in or 0) / log_total
                percentage = (fan_in / total_nodes) * 100 if total_nodes > 0 else 0
                
                # Create visual bar (5 chars)
                bar_length = int(score * 5)
                bar = "█" * bar_length + "░" * (5 - bar_length)
                
                # Quality rating
                if score >= 0.85:
                    rating = "⭐ Backbone"
                elif score >= 0.65:
                    rating = "● Good"
                elif score >= 0.45:
                    rating = "○ Fair"
                else:
                    rating = "◐ Local"
                
                lines.append(
                    f"{rank}. **{node_id.upper()}** {bar} {score:.2f} ({rating})\n"
                    f"   └─ {fan_in} feeders ({percentage:.0f}% of network)"
                )

            await self.send_response(message, "\n".join(lines))
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting scoring data")
            return False
