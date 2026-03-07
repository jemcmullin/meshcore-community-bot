"""Scoring command - shows top repeaters by infrastructure score (mesh_connections fan-in)."""

import asyncio
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
            def get_repeaters():
                return self.bot.db_manager.execute_query(
                    """SELECT to_prefix,
                              COUNT(DISTINCT from_prefix) AS fan_in,
                              (SELECT COUNT(DISTINCT from_prefix)
                               FROM mesh_connections) AS total_nodes
                       FROM mesh_connections
                       GROUP BY to_prefix
                       ORDER BY fan_in DESC
                       LIMIT 5"""
                )
            
            rows = await asyncio.to_thread(get_repeaters)
            if not rows:
                await self.send_response(message, "No repeater data available yet")
                return True

            # Extract values from dict rows (execute_query returns List[Dict])
            total_nodes = max(rows[0].get('total_nodes') or 1, 1)
            log_total = math.log1p(total_nodes)

            lines = [f"Top 5 ({total_nodes}n):"]

            # Compact rankings
            for rank, row in enumerate(rows, start=1):
                node_id = row['to_prefix']
                fan_in = row['fan_in']
                score = math.log1p(fan_in or 0) / log_total
                pct = (fan_in / total_nodes) * 100 if total_nodes > 0 else 0
                
                # Quality rating
                if score >= 0.85:
                    rating = "⭐"
                elif score >= 0.65:
                    rating = "●"
                elif score >= 0.45:
                    rating = "○"
                else:
                    rating = "◐"
                
                lines.append(f"{node_id.upper()} {rating}{score:.2f} {fan_in}/{total_nodes} {pct:.0f}%")

            await self.send_response(message, "\n".join(lines))
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting scoring data")
            return False
