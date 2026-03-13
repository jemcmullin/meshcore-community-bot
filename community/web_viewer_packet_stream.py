import asyncio
import json
import sqlite3
import time
import logging
from typing import Optional

logger = logging.getLogger('WebViewerPacketStream')

async def publish_web_viewer_dm_event(message, result, bot):
    """
    Log a DM event to the packet_stream table for the community web viewer.
    Args:
        message: MeshMessage object
        result: bool (success/failure of response)
        bot: Bot instance (for db path)
    """
    try:
        wvi = getattr(bot, "web_viewer_integration", None)
        db_path = None
        if wvi and hasattr(wvi, "_get_web_viewer_db_path"):
            db_path = wvi._get_web_viewer_db_path()
        elif hasattr(bot, "db_manager") and hasattr(bot.db_manager, "db_path"):
            db_path = bot.db_manager.db_path
        else:
            logger.debug("No DB path found for packet_stream event logging.")
            return

        command_id = f"dm_{getattr(message, 'sender_id', 'unknown')}"
        dm_event = {
            "command_id": command_id,
            "user": getattr(message, 'sender_id', 'Unknown'),
            "success": bool(result),
            "timestamp": int(getattr(message, 'timestamp', time.time())),
            "content": (getattr(message, 'content', '')[:100]),
        }

        def _insert():
            conn = sqlite3.connect(str(db_path), timeout=60.0)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO packet_stream (timestamp, data, type) VALUES (?, ?, ?)",
                    (float(dm_event["timestamp"]), json.dumps(dm_event), "command"),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_insert)
    except Exception as e:
        logger.debug(f"Failed to log DM event for web viewer: {e}")

async def publish_web_viewer_coordination_event(
    bot,
    message,
    message_hash: str,
    stage: str,
    delivery_score: float,
    hop_component: Optional[float] = None,
    infra_component: Optional[float] = None,
    path_bonus_component: Optional[float] = None,
    freshness_component: Optional[float] = None,
):
    """
    Publish coordination score snapshots to web viewer command stream.
    Uses existing BotIntegration.capture_command() so no submodule changes are required.
    """
    wvi = getattr(bot, "web_viewer_integration", None)
    if not wvi or not getattr(wvi, "bot_integration", None):
        return

    summary_parts = [f"stage={stage}", f"score={delivery_score:.3f}"]
    if hop_component is not None:
        summary_parts.append(f"hop_comp={hop_component:.3f}")
    if infra_component is not None:
        summary_parts.append(f"infra_comp={infra_component:.3f}")
    if path_bonus_component is not None:
        summary_parts.append(f"path_bonus_comp={path_bonus_component:.3f}")
    if freshness_component is not None:
        summary_parts.append(f"fresh_comp={freshness_component:.3f}")
    summary = " ".join(summary_parts)

    command_id = f"coord:{message_hash[:12]}"
    try:
        await asyncio.to_thread(
            wvi.bot_integration.capture_command,
            message,
            f"coord_{stage}",
            summary,
            True,
            command_id,
        )
    except Exception as e:
        logger.debug(f"Failed to publish coordination event to web viewer: {e}")
