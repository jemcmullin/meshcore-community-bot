import asyncio
from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage

class ScoringCommand(BaseCommand):
    """Bid-score diagnostics for bot owners (community extension)."""

    name = "scoring"
    keywords = ["score", "scoring", "repeaters"]
    description = "Shows top infra relays and simple bid-health metrics"
    requires_dm = True
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            from community.config import ScoringConfig
            scoring_cfg = ScoringConfig()
            
            def load_metrics():
                # Query top infrastructure relays and basic bid-health metrics
                return self.bot.db_manager.execute_query(
                    f"""
                    SELECT COALESCE(mc.to_public_key, mc.to_prefix) AS node,
                        COUNT(DISTINCT mc.from_public_key) AS fan_in,
                        CAST((julianday('now', 'localtime') - julianday(MAX(mc.last_seen))) * 24 AS REAL) AS age_hours,
                        (SELECT MAX(c)
                        FROM (SELECT COUNT(DISTINCT from_public_key) AS c
                            FROM mesh_connections
                            GROUP BY to_public_key)) AS max_fan_in,
                        cct.out_hops,
                        cct2.name
                    FROM mesh_connections mc
                    LEFT JOIN (
                    SELECT public_key,
                        MAX(hop_count) AS out_hops
                    FROM complete_contact_tracking
                    WHERE out_path_len IS NOT NULL
                    GROUP BY public_key
                    ) AS cct ON (
                    (mc.to_public_key IS NOT NULL AND cct.public_key = mc.to_public_key)
                    OR (mc.to_public_key IS NULL AND cct.public_key LIKE mc.to_prefix || '%')
                    )
                    LEFT JOIN (
                    SELECT public_key, MAX(name) AS name
                    FROM complete_contact_tracking
                    WHERE name IS NOT NULL AND name != ''
                    GROUP BY public_key
                    ) AS cct2 ON (
                    (mc.to_public_key IS NOT NULL AND cct2.public_key = mc.to_public_key)
                    OR (mc.to_public_key IS NULL AND cct2.public_key LIKE mc.to_prefix || '%')
                    )
                    GROUP BY node
                    ORDER BY fan_in DESC
                    LIMIT 10
                    """
                )

            infra_rows = await asyncio.to_thread(load_metrics)

            if not infra_rows:
                await self.send_response(message, "No infrastructure data yet. Wait for mesh traffic.")
                return True

            top_nodes = []
            stale_nodes = 0
            import math
            # Calculate max_fan_in and log_max_fan for normalization
            max_fan_in = max(int(infra_rows[0]["max_fan_in"] or 1), 1) if infra_rows else 1
            log_max_fan = math.log1p(max_fan_in)

            for row in infra_rows:
                node_val = row.get("node") or ""
                node = node_val.upper().replace("!", "")[:4]
                fan_in = int(row.get("fan_in") or 0)
                age_hours = float(row.get("age_hours") or 999)
                if age_hours > 48:
                    stale_nodes += 1
                    continue # Skip in this list once counted as stale
                hops = row.get("out_hops")
                # Calculate scoring components
                infra = math.log1p(fan_in) / log_max_fan if log_max_fan > 0 else 0.0
                hop_score = 0.25 if hops is None else (1.0 / (1 + hops))
                path_bonus = 0.0
                freshness = math.exp(-age_hours / 24.0)
                significance = (
                    infra * scoring_cfg.infrastructure_weight +
                    hop_score * scoring_cfg.hop_weight +
                    path_bonus * scoring_cfg.path_bonus_weight +
                    freshness * scoring_cfg.freshness_weight
                )
                
                top_nodes.append((node, fan_in, hops, significance))
                hop_score   = 0.25 if hops is None else (1.0 / (1 + hops))
                path_bonus  = 0.0
                freshness   = math.exp(-age_hours / 24.0)
                significance = (
                    infra * scoring_cfg.infrastructure_weight +
                    hop_score * scoring_cfg.hop_weight +
                    path_bonus * scoring_cfg.path_bonus_weight +
                    freshness * scoring_cfg.freshness_weight
                )
            

            # for row in infra_rows:
            #     node_val = row.get("node") or ""
            #     node = node_val.upper().replace("!", "")[:4]
            #     fan_in = int(row.get("fan_in") or 0)
            #     age_hours = float(row.get("age_hours") or 999)
            #     hops = row.get("out_hops")
            #     if age_hours > 48:
            #         stale_nodes += 1
            #     top_nodes.append((node, fan_in, hops))

            # Radio-safe output: limit to 5 nodes, keep message short
            max_len = self.get_max_message_length(message)
            lines = [f"{'Node':<4} {'Links':>5} {'Hops':>5} {'Sig':>6}"]
            for node, links, hops, sig in top_nodes[:5]:
                hop_str = f"{hops}" if hops is not None else "?"
                sig_str = f"{sig:.2f}"
                lines.append(f"{node:<4} {links:>5} {hop_str:>5} {sig_str:>6}")

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