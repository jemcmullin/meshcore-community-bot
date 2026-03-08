"""Scoring command - shows top repeaters by infrastructure score.

Score = (log1p(fan_in) / log1p(max_fan_in)) x depth_fraction  — range [0, 1]
  fan_in         = distinct origin nodes that route through this relay
  max_fan_in     = highest fan_in in the network (normalises reach to 0-1)
  depth_fraction = (avg_hop_position - 1) / (network_max_depth - 1)
                   0 for co-located feeders (always at hop 1), 1 for deepest relay

Scale: backbone ~0.65-1.0, distributor ~0.30-0.65, local relay ~0.10-0.30, feeder ~0.
"""

import asyncio
import math

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class ScoringCommand(BaseCommand):
    """Top 5 repeaters by score = (log1p(fan_in)/log1p(max_fan_in)) × depth_fraction [0-1]. Feeders score near 0."""

    name = "scoring"
    keywords = ["scoring"]
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
                              (SELECT MAX(d)
                               FROM (SELECT AVG(COALESCE(avg_hop_position, 1)) AS d
                                     FROM mesh_connections
                                     GROUP BY to_prefix)) AS max_depth,
                              (SELECT MAX(COUNT(DISTINCT from_prefix))
                               FROM mesh_connections
                               GROUP BY to_prefix) AS max_fan_in
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
            max_depth    = max(float(rows[0].get('max_depth') or 1.0), 1.0)
            depth_range  = max(max_depth - 1, 0.001)
            max_fan_in   = max(rows[0].get('max_fan_in') or 1, 1)
            log_max_fan  = math.log1p(max_fan_in)

            # score = (log1p(fan_in) / log1p(max_fan_in)) × depth_fraction  [0, 1]
            # depth_fraction = 0 at hop 1 (feeder), 1 at deepest observed relay
            scored = []
            for row in rows:
                fan_in     = row['fan_in'] or 0
                avg_depth  = float(row['avg_depth'] or 1)
                depth_frac = max(avg_depth - 1, 0) / depth_range
                score      = (math.log1p(fan_in) / log_max_fan) * depth_frac
                pct        = (fan_in / total_nodes) * 100 if total_nodes > 0 else 0
                scored.append((row['to_prefix'], fan_in, score, pct))

            scored.sort(key=lambda x: x[2], reverse=True)
            top5 = scored[:5]

            lines = [f"Top 5 ({total_nodes}n, max fan-in {max_fan_in}, max depth {max_depth:.1f}):"]
            for node_id, fan_in, score, pct in top5:
                if score >= 0.65:
                    rating = "🟢"  # backbone
                elif score >= 0.30:
                    rating = "🟡"  # distributor
                elif score >= 0.10:
                    rating = "🟠"  # local relay
                else:
                    rating = "🔴"  # feeder / shallow
                lines.append(f"{node_id.upper()} {rating}{score:.2f} {fan_in}/{total_nodes} {pct:.0f}%")

            await self.send_response(message, "\n".join(lines))
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting scoring data")
            return False
