#!/usr/bin/env python3
"""
PacketCapture Patch for Banned Users

Patches the PacketCapture service to filter MQTT packets from banned senders.
This wrapper operates in the community layer without modifying meshcore-bot.

The patch intercepts process_packet() before formatting/publishing to check if the
sender (extracted from decoded packet) is in the Banned_Users list. If banned, the
packet is not published to MQTT, but logging and file output are unaffected.
"""

import logging
import types
from typing import Any, Optional

from .banned_users_integration import BannedUsersManager


def patch_packet_capture_for_banned_users(bot: Any, banned_users_manager: BannedUsersManager) -> None:
    """Patch PacketCapture service to filter banned senders from MQTT.

    Args:
        bot: MeshCoreBot instance
        banned_users_manager: BannedUsersManager instance with loaded banned users
    """
    if not hasattr(bot, "packet_capture_service"):
        # Packet capture may not be enabled
        return

    service = bot.packet_capture_service
    if not service or not hasattr(service, "process_packet"):
        return

    logger = logging.getLogger("PacketCapturePatch")
    original_process_packet = service.process_packet

    async def patched_process_packet(raw_hex: str, payload: dict[str, Any], metadata: Optional[dict[str, Any]] = None) -> None:
        """Patched version of process_packet() that filters banned senders from MQTT.

        Packets from banned senders are:
        - NOT published to MQTT
        - Still written to local file (if configured)
        - Still logged normally
        """
        # Decode packet to check sender
        packet_info = service.decode_packet(raw_hex, payload)

        # Check if sender is banned (only for message-type packets)
        if packet_info and banned_users_manager.should_block_packet_from_mqtt(packet_info):
            sender_name, sender_pubkey = banned_users_manager.extract_sender_from_packet(packet_info)
            ban_msg = f"sender '{sender_name}'" if sender_name else "sender"
            if sender_pubkey:
                ban_msg = f"{ban_msg} (key: {sender_pubkey[:16]}...)" if sender_name else f"key {sender_pubkey[:16]}..."
            service.logger.debug(
                f"Blocked MQTT publish for packet from banned {ban_msg} "
                f"({packet_info.get('payload_type', 'UNKNOWN')} type)"
            )

            # Still count the packet and write to file, but skip MQTT
            service.packet_count += 1

            # Write to file if enabled
            if service.output_handle:
                try:
                    import json

                    formatted_packet = service._format_packet_data(raw_hex, packet_info or {}, payload, metadata)
                    service.output_handle.write_line(json.dumps(formatted_packet, default=str))
                except Exception as e:
                    service.logger.debug(f"Error writing banned packet to file: {e}")

            # Log summary
            service._log_packet_summary(
                f"📦 Captured packet #{service.packet_count}: "
                f"{packet_info.get('route', 'U') if packet_info else 'U'} type "
                f"{packet_info.get('packet_type', '0') if packet_info else '0'}, "
                f"{len(raw_hex) // 2} bytes (MQTT blocked: sender banned)"
            )
            return

        # Not banned; call original process_packet
        await original_process_packet(raw_hex, payload, metadata)

    # Monkey-patch the service
    service.process_packet = types.MethodType(patched_process_packet, service)
    ban_count = len(banned_users_manager.banned_usernames) + len(banned_users_manager.banned_pubkeys)
    logger.info(f"Patched PacketCapture.process_packet() for banned users ({ban_count} bans: {len(banned_users_manager.banned_usernames)} usernames, {len(banned_users_manager.banned_pubkeys)} public keys)")
