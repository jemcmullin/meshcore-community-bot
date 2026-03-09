"""Coverage command — which mesh nodes this bot hears, and how reliably.

Buckets:
  Close  — confirmed outbound path (0 or 1 hop), seen within ~34h
  Active — no confirmed path yet, but seen recently
  Stale  — not seen in ~34h+ (freshness < 0.25, i.e. exp(-age_h/24) < 0.25)

Answers: "where is this bot useful, and is anything broken?"
"""

import asyncio
import math

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class ScoringCommand(BaseCommand):
    """Bot coverage overview: close/active/stale node buckets."""

    name = "scoring"
    keywords = ["score", "scoring", "repeaters"]
    description = "Bot coverage overview: which nodes are close, active, or stale"
    requires_dm = True
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            def get_repeaters():
                return self.bot.db_manager.execute_query(
                    """SELECT mc.to_prefix,
                              COUNT(DISTINCT mc.from_prefix) AS fan_in,
                              CAST((julianday('now') - julianday(MAX(mc.last_seen))) * 24 AS REAL) AS age_hours,
                              (SELECT COUNT(DISTINCT from_prefix)
                               FROM mesh_connections) AS total_nodes,
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
                       LIMIT 30"""
                )

            rows = await asyncio.to_thread(get_repeaters)
            if not rows:
                await self.send_response(message, "No repeater data available yet")
                return True

            total_nodes = max(rows[0].get('total_nodes') or 1, 1)

            close, active, stale = [], [], []
            for row in rows:
                node_id   = (row['to_prefix'] or '').upper()
                age_hours = float(row.get('age_hours') or 999)
                out_hops  = row.get('out_hops')
                freshness = math.exp(-age_hours / 24.0)

                if freshness < 0.25:           # not seen in ~34h+
                    stale.append(node_id)
                elif out_hops is not None:     # confirmed path, fresh
                    close.append(f"{node_id}({out_hops})")
                else:                          # active but path unconfirmed
                    active.append(node_id)

            lines = [f"{total_nodes}n nodes"]
            if close:
                lines.append(f"Close({len(close)}): {' '.join(close)}")
            if active:
                lines.append(f"Active({len(active)}): {' '.join(active)}")
            if stale:
                lines.append(f"Stale({len(stale)}): {' '.join(stale)}")

            await self.send_response(message, "\n".join(lines))
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting coverage data")
            return False
