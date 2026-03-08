"""Scoring command — ranks mesh nodes by infrastructure score.

Score = (log1p(fan_in) / log1p(max_fan_in)) × depth_frac  [0, 1]
  fan_in     = distinct nodes that route through this node
  depth_frac = (avg_hop_position - 1) / (max_depth - 1)
               0 for hop-1 feeders, 1 for the deepest node in the network

Output columns: Scr=score, Freq=% of known nodes that route here, ~Hop=avg hop position
"""

import asyncio
import math

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class ScoringCommand(BaseCommand):
    """Top 5 mesh nodes ranked by infrastructure score (reach × path depth). Feeders score near 0."""

    name = "scoring"
    keywords = ["score", "scoring", "repeaters"]
    description = "Top repeaters by reach × path depth (feeders score near 0)"
    requires_dm = True
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            def get_repeaters():
                return self.bot.db_manager.execute_query(
                    """SELECT to_prefix,
                              COUNT(DISTINCT from_prefix) AS fan_in,
                              AVG(COALESCE(avg_hop_position, 1)) AS avg_depth,
                              (SELECT COUNT(DISTINCT from_prefix)
                               FROM mesh_connections) AS total_nodes,
                              (SELECT SUM(observation_count)
                               FROM mesh_connections) AS total_obs,
                              (SELECT MAX(d)
                               FROM (SELECT AVG(COALESCE(avg_hop_position, 1)) AS d
                                     FROM mesh_connections
                                     GROUP BY to_prefix)) AS max_depth,
                              (SELECT MAX(c)
                               FROM (SELECT COUNT(DISTINCT from_prefix) AS c
                                     FROM mesh_connections
                                     GROUP BY to_prefix)) AS max_fan_in
                       FROM mesh_connections
                       GROUP BY to_prefix
                       ORDER BY fan_in DESC
                       LIMIT 20"""
                )

            rows = await asyncio.to_thread(get_repeaters)
            if not rows:
                await self.send_response(message, "No repeater data available yet")
                return True

            total_nodes  = max(rows[0].get('total_nodes') or 1, 1)
            total_obs    = int(rows[0].get('total_obs') or 0)
            max_depth    = max(float(rows[0].get('max_depth') or 1.0), 1.0)
            depth_range  = max(max_depth - 1, 0.001)
            max_fan_in   = max(rows[0].get('max_fan_in') or 1, 1)
            log_max_fan  = math.log1p(max_fan_in)

            scored = []
            for row in rows:
                fan_in     = row['fan_in'] or 0
                avg_depth  = float(row['avg_depth'] or 1)
                depth_frac = max(avg_depth - 1, 0) / depth_range
                score      = (math.log1p(fan_in) / log_max_fan) * depth_frac
                pct        = (fan_in / total_nodes) * 100 if total_nodes > 0 else 0
                scored.append((row['to_prefix'], avg_depth, score, pct))

            scored.sort(key=lambda x: x[2], reverse=True)
            top5 = scored[:5]

            lines = [
                f"{total_nodes}n/{total_obs}obs - top:",
                "Node  Scr   Freq  ~Hop",
            ]
            for node_id, avg_depth, score, pct in top5:
                lines.append(f"{node_id.upper():<4}  {score:.2f} {int(pct):>4}%  {avg_depth:.1f}")

            await self.send_response(message, "\n".join(lines))
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting scoring data")
            return False
