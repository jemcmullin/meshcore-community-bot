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
                # May inflate score when mixed prefix length connections exist and public key is missing. Actual scoring implements deduplication.
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
                    LIMIT 100
                    """
                )

            infra_rows = await asyncio.to_thread(load_metrics)

            if not infra_rows:
                await self.send_response(message, "No infrastructure data yet. Wait for mesh traffic.")
                return True


            top_nodes = []
            stale_nodes = 0
            import math
            # Gather all fan_in values and hops for normalization
            node_fanins = []
            node_hops = []
            for row in infra_rows:
                fan_in = int(row.get("fan_in") or 0)
                node_fanins.append(fan_in)
                hops = row.get("out_hops")
                node_hops.append(hops)
            # Calculate 90th percentile normalization factor (as in coordinator_scoring.py)
            percentile = 0.9
            sorted_fanins = sorted(node_fanins)
            if sorted_fanins:
                idx = int(math.ceil(percentile * len(sorted_fanins))) - 1
                idx = max(0, min(idx, len(sorted_fanins) - 1))
                norm_factor = max(3, sorted_fanins[idx])
            else:
                norm_factor = 3

            # Calculate max hops (excluding None)
            max_hops = max([h for h in node_hops if h is not None], default=0)
            max_hop_score = (1.0 / (1 + max_hops)) if max_hops > 0 else 0.1

            for row in infra_rows:
                node_val = row.get("node") or ""
                node = node_val.upper().replace("!", "")[:4]
                fan_in = int(row.get("fan_in") or 0)
                age_hours = float(row.get("age_hours") or 999)
                hops = row.get("out_hops")
                # Calculate scoring components
                infra = min(1.0, math.log1p(fan_in) / math.log1p(norm_factor))
                hop_score = (1.0 / (1 + hops)) if hops is not None else max_hop_score
                # path_bonus = 0.0
                # freshness = math.exp(-age_hours / 24.0)
                significance = (
                    infra * scoring_cfg.infrastructure_weight +
                    hop_score * scoring_cfg.hop_weight
                )
                top_nodes.append((node, fan_in, hops, significance, age_hours))

            top_nodes.sort(key=lambda x: x[3], reverse=True)  # Sort by significance
            top_nodes = top_nodes[:20]  # Keep top 20 for stale filtering
            # Count and remove stale nodes (not seen in 48+ hours)
            for i in range(len(top_nodes)-1, -1, -1):
                if top_nodes[i][4] > 48:
                    stale_nodes += 1
                    del top_nodes[i]

            # Radio-safe output: limit to 5 nodes, keep message short
            max_len = self.get_max_message_length(message)
            lines = [f"{'Node'}|{'Links'}|{'Hops'}|{'Scr(1-5)'}"]
            max_sig = top_nodes[0][3] if top_nodes else 1.0  # Avoid division by zero

            for node, links, hops, sig, age_hours in top_nodes[:5]:
                hop_str = f"{hops}" if hops is not None else "?"
                # Calculate 1-5 rank
                rank = round((sig / max_sig) * 5) if max_sig > 0 else 1
                rank = max(1, min(5, rank))  # Clamp between 1 and 5
                nodes_str = f"{node:<4}" if len(node) >= 4 else f"{node:<6}"
                lines.append(f"{nodes_str} {str(links):>5} {str(hop_str):>5} {str(rank):>5}") # extra pad to compensate for font

            if stale_nodes > 0:
                lines = lines[:5] # Keep only header + top 4 nodes to make room for stale count
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