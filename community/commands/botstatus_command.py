"""Bot status command - shows firmware coordination status."""

import time

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage

from community.meshcore_response_coordinator import (
    BOT_RESPONSE_DELAY_BASE_MILLIS,
    BOT_RESPONSE_DELAY_JITTER_MILLIS,
)


class BotstatusCommand(BaseCommand):
    """Shows the bot's firmware coordination status."""

    name = "botstatus"
    keywords = ["botstatus", "botstat"]
    description = "Shows firmware coordination status"
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            fw = getattr(self.bot, "firmware_coordinator", None)

            if fw is None:
                await self.send_response(message, "Mode: standalone (no coordinator)")
                return True

            seed = fw.bot_identity_seed
            pending = fw.queue_depth()
            recent = len(fw.recent)
            uptime = int(fw.uptime_seconds())
            hours = uptime // 3600
            mins = (uptime % 3600) // 60

            parts = [
                "Mode: firmware-native",
                f"Seed: 0x{seed:08x}",
                f"Pending: {pending}  Recent: {recent}",
                f"Uptime: {hours}h {mins}m",
                f"Base: {BOT_RESPONSE_DELAY_BASE_MILLIS}ms  Jitter: {BOT_RESPONSE_DELAY_JITTER_MILLIS}ms",
            ]

            await self.send_response(message, "\n".join(parts))
            return True
        except Exception as e:
            self.logger.error("Botstatus command error: %s", e)
            await self.send_response(message, "Error getting bot status")
            return False
