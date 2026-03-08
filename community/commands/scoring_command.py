"""Scoring command — ranks mesh nodes by estimated bid significance.

Significance = hop_score×0.35 + infra×0.30 + reliability×0.20 + freshness×0.15

Components (same weights as coordinator bidding):
  hop_score   = max(0, 1 - out_hops × 0.35)  [1.0 at 1 hop, 0 at 3+ hops; 0.5 if unknown]
  infra       = reach × (0.5 + 0.5 × depth_frac)  [topology quality]
  reliability = log1p(relay_obs) / log1p(max_relay_obs)  [how often seen in paths]
  freshness   = exp(-age_hours / 24)  [how recently seen]

Answers: "if a message comes in through this relay, how strong will this bot's bid be?"

Output columns: Sig=significance, Inf=infra, Out=outbound hops, Freq=coverage%, ~Hop=avg depth
"""

import asyncio
import math

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class ScoringCommand(BaseCommand):
    """Top 5 mesh nodes ranked by estimated bid significance (all 4 bid components)."""

    name = "scoring"
    keywords = ["score", "scoring", "repeaters"]
    description = "Top repeaters by estimated bid significance (hop + infra + reliability + freshness)"
    requires_dm = True
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            def get_repeaters():
                return self.bot.db_manager.execute_query(
                    """SELECT mc.to_prefix,
                              COUNT(DISTINCT mc.from_prefix) AS fan_in,
                              AVG(COALESCE(mc.avg_hop_position, 1)) AS avg_depth,
                              SUM(mc.observation_count) AS relay_obs,
                              CAST((julianday('now') - julianday(MAX(mc.last_seen))) * 24 AS REAL) AS age_hours,
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
                                     GROUP BY to_prefix)) AS max_fan_in,
                              (SELECT MAX(s)
                               FROM (SELECT SUM(observation_count) AS s
                                     FROM mesh_connections
                                     GROUP BY to_prefix)) AS max_relay_obs,
                              cct.out_hops
                       FROM mesh_connections mc
                       LEFT JOIN (
                         SELECT LOWER(SUBSTR(public_key, 1, 2)) AS pfx,
                                MIN(out_path_len) AS out_hops
                         FROM complete_contact_tracking
                         WHERE out_path_len IS NOT NULL
                         GROUP BY pfx
                       ) AS cct ON cct.pfx = mc.to_prefix
                       GROUP BY mc.to_prefix
                       ORDER BY fan_in DESC
                       LIMIT 20"""
                )

            rows = await asyncio.to_thread(get_repeaters)
            if not rows:
                await self.send_response(message, "No repeater data available yet")
                return True

            total_nodes    = max(rows[0].get('total_nodes') or 1, 1)
            total_obs      = int(rows[0].get('total_obs') or 0)
            max_depth      = max(float(rows[0].get('max_depth') or 1.0), 1.0)
            depth_range    = max(max_depth - 1, 0.001)
            max_fan_in     = max(rows[0].get('max_fan_in') or 1, 1)
            log_max_fan    = math.log1p(max_fan_in)
            max_relay_obs  = max(rows[0].get('max_relay_obs') or 1, 1)
            log_max_relay  = math.log1p(max_relay_obs)

            scored = []
            for row in rows:
                fan_in      = row['fan_in'] or 0
                avg_depth   = float(row['avg_depth'] or 1)
                out_hops    = row.get('out_hops')
                relay_obs   = int(row.get('relay_obs') or 0)
                age_hours   = float(row.get('age_hours') or 999)
                depth_frac  = max(avg_depth - 1, 0) / depth_range
                reach       = math.log1p(fan_in) / log_max_fan
                infra       = reach * (0.5 + 0.5 * depth_frac)
                reliability = math.log1p(relay_obs) / log_max_relay
                freshness   = math.exp(-age_hours / 24.0)
                hop_score   = max(0.0, 1.0 - int(out_hops) * 0.35) if out_hops is not None else 0.5
                significance = hop_score * 0.35 + infra * 0.30 + reliability * 0.20 + freshness * 0.15
                pct         = (fan_in / total_nodes) * 100 if total_nodes > 0 else 0
                scored.append((row['to_prefix'], avg_depth, out_hops, significance, infra, pct))

            scored.sort(key=lambda x: x[3], reverse=True)
            top5 = scored[:5]

            lines = [f"{total_nodes}n [sig inf hop freq]"]
            for node_id, avg_depth, out_hops, significance, infra, pct in top5:
                out_str = str(out_hops) if out_hops is not None else "?"
                lines.append(f"{node_id.upper():<4} {significance:.2f} {infra:.2f} {out_str:<3} {int(pct):>3}%")

            await self.send_response(message, "\n".join(lines))
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting scoring data")
            return False
