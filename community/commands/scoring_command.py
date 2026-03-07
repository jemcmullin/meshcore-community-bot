"""Scoring command - shows top repeaters by significance score."""

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class ScoringCommand(BaseCommand):
    """Shows the top 5 repeaters by significance score from the network observer."""

    name = "scoring"
    keywords = ["scoring"]
    description = "Shows top 5 repeaters by significance score"
    requires_dm = True
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            observer = getattr(self.bot, "network_observer", None)
            if not observer:
                await self.send_response(message, "Network observer not available")
                return True

            node_counts = observer._node_counts
            if not node_counts:
                await self.send_response(message, "No repeater data observed yet")
                return True

            scored = [
                (node_id, observer.get_node_significance(node_id))
                for node_id in node_counts
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            top5 = scored[:5]

            lines = ["Top Repeaters:"]
            for rank, (node_id, sig) in enumerate(top5, start=1):
                counts = node_counts[node_id]
                lines.append(
                    f"{rank}. {node_id} {sig:.2f} "
                    f"(seen={counts['total']} lh={counts['last_hop']})"
                )

            await self.send_response(message, "\n".join(lines))
            return True
        except Exception as e:
            self.logger.error(f"Scoring command error: {e}")
            await self.send_response(message, "Error getting scoring data")
            return False
