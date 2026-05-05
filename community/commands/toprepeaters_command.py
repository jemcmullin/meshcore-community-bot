import asyncio
from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage

class TopRepeatersCommand(BaseCommand):
    """Best repeaters nearby for bot (community extension)."""

    name = "NearbyTopRepeaters"
    keywords = ["nearby_top_repeaters", "bot_top_repeaters", "bot_top_reps"]
    description = "Shows top infra relays nearby the bot"
    requires_dm = True
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
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

            for row in infra_rows:
                node_val = row.get("node") or ""
                node = node_val.upper().replace("!", "")[:4]
                fan_in = int(row.get("fan_in") or 0)
                age_hours = float(row.get("age_hours") or 999)
                hops = row.get("out_hops")
                # Rank by links (fan-in), with fewer hops as a secondary signal
                hop_score = 0.25 if hops is None else (1.0 / (1 + hops))
                rank_key = (fan_in, hop_score)
                top_nodes.append((node, fan_in, hops, rank_key, age_hours))

            top_nodes.sort(key=lambda x: x[3], reverse=True)  # Sort by (fan_in, hop_score)
            top_nodes = top_nodes[:20]  # Keep top 20 for stale filtering
            # Count and remove stale nodes (not seen in 48+ hours)
            for i in range(len(top_nodes)-1, -1, -1):
                if top_nodes[i][4] > 48:
                    stale_nodes += 1
                    del top_nodes[i]

            # Radio-safe output: limit to 5 nodes, keep message short
            max_len = self.get_max_message_length(message)
            lines = [f"{'Node'}|{'Links'}|{'Hops'}"]
            max_links = top_nodes[0][1] if top_nodes else 1

            for node, links, hops, rank_key, age_hours in top_nodes[:5]:
                hop_str = f"{hops}" if hops is not None else "?"
                nodes_str = f"{node:<4}" if len(node) >= 4 else f"{node:<6}"
                lines.append(f"{nodes_str} {str(links):>5} {str(hop_str):>5}")

            if stale_nodes > 0:
                lines = lines[:5] # Keep only header + top 4 nodes to make room for stale count
                lines.append(f"Stale: {stale_nodes}")

            text = "\n".join(lines)
            if len(text) > max_len:
                text = text[: max_len - 3] + "..."

            await self.send_response(message, text)
            return True
        except Exception as e:
            self.logger.error(f"TopRepeaters command error: {e}")
            await self.send_response(message, "Error getting top repeaters")
            return False