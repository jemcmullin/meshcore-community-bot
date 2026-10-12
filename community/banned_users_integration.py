#!/usr/bin/env python3
"""
Banned Users Integration for Community Layer

Extends the meshcore-bot Banned_Users config to also block MQTT packet publishing.
This wrapper operates entirely in the community layer and does not modify meshcore-bot.

The bot already blocks responses to banned users via message_handler.should_process_message().
This module extends that blocking to the MQTT observer (PacketCapture service).

Supports two ban types:
  1. Sender names: Prefix-match (starts-with) like meshcore-bot does
     Example: banned_users = BadUser, Troublemaker
  
  2. Public keys: Hex format, can be full 64-char key or prefix
     Example: banned_pubkeys = 0123456789abcdef, fedcba9876543210

Sender/key extraction works for text messages (TXT_MSG, GRP_TXT); other packet types are
published normally since they don't have a "sender" in the user sense.
"""

import logging
from typing import Any, Optional


class BannedUsersManager:
    """Manages banned users (by name and/or public key) from config."""

    def __init__(self, config: Any, logger: Optional[logging.Logger] = None):
        """Initialize banned users manager.

        Args:
            config: ConfigParser instance (same config object used by bot)
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger("BannedUsersIntegration")
        self.banned_usernames: list[str] = self._load_banned_usernames()
        self.banned_pubkeys: list[str] = self._load_banned_pubkeys()

    def _load_banned_usernames(self) -> list[str]:
        """Load banned usernames from [Banned_Users] section.

        Mirrors meshcore-bot's command_manager.load_banned_users().
        Uses prefix (starts-with) matching: "Awful" matches "Awful Username" and "Awful Username 🍆".
        """
        if not self.config.has_section("Banned_Users"):
            return []
        banned = self.config.get("Banned_Users", "banned_users", fallback="")
        users = [user.strip() for user in banned.split(",") if user.strip()]
        if users:
            self.logger.debug(f"Loaded {len(users)} banned usernames from config")
        return users

    def _load_banned_pubkeys(self) -> list[str]:
        """Load banned public keys from [Banned_Users] section.

        Public keys are hex format (64 chars for full key, or shorter prefix).
        Stored as lowercase for case-insensitive matching.
        """
        if not self.config.has_section("Banned_Users"):
            return []
        banned = self.config.get("Banned_Users", "banned_pubkeys", fallback="")
        keys = [
            key.strip().replace("0x", "").replace(" ", "").lower()
            for key in banned.split(",")
            if key.strip()
        ]
        if keys:
            self.logger.debug(f"Loaded {len(keys)} banned public keys from config")
        return keys

    def is_sender_banned(self, sender_id: Optional[str], sender_pubkey: Optional[str] = None) -> bool:
        """Check if sender is banned by name or public key.

        Args:
            sender_id: Sender name/username (checked with prefix matching)
            sender_pubkey: Sender's public key in hex (checked with prefix or exact matching)

        Returns:
            bool: True if sender matches any banned entry (name or key), False otherwise
        """
        # Check username (prefix matching like meshcore-bot)
        if sender_id:
            if any(sender_id.startswith(entry) for entry in self.banned_usernames):
                return True

        # Check public key (prefix matching: "abc" matches "abcdef123456..." for partial matches)
        if sender_pubkey:
            # Normalize to lowercase hex, remove 0x prefix
            pubkey_normalized = sender_pubkey.replace("0x", "").replace(" ", "").lower()
            if any(pubkey_normalized.startswith(entry) for entry in self.banned_pubkeys):
                return True

        return False

    def extract_sender_from_packet(self, packet_info: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """Extract sender name and public key from decoded packet.

        For text-message types (TXT_MSG, GRP_TXT), the sender is the first field in the decoded payload.
        Public key may be available in the decoded payload.

        Args:
            packet_info: Decoded packet info dict (from decode_meshcore_packet)

        Returns:
            tuple[str | None, str | None]: (sender_name, public_key) or (None, None) if cannot extract
        """
        if not packet_info:
            return None, None

        payload_type = packet_info.get("payload_type")
        if not payload_type:
            return None, None

        # Only text message types have a "sender" field
        if payload_type not in ("TXT_MSG", "GRP_TXT"):
            return None, None

        sender_name = None
        sender_pubkey = None

        # Try to extract from decoded payload (if available in packet_info)
        decoded = packet_info.get("decoded")
        if isinstance(decoded, dict):
            sender_name = decoded.get("sender")
            if sender_name and not isinstance(sender_name, str):
                sender_name = None

            sender_pubkey = decoded.get("public_key")
            if sender_pubkey and not isinstance(sender_pubkey, str):
                sender_pubkey = None

        # If not in decoded, we'd need to manually parse the payload bytes
        # For now, best-effort extraction from what's available
        return sender_name, sender_pubkey

    def should_block_packet_from_mqtt(self, packet_info: dict[str, Any]) -> bool:
        """Determine if a packet should be blocked from MQTT publishing.

        Checks both sender name and public key against ban lists.

        Args:
            packet_info: Decoded packet info dict

        Returns:
            bool: True if packet should be blocked from MQTT, False to publish normally
        """
        if not self.banned_usernames and not self.banned_pubkeys:
            return False

        sender_name, sender_pubkey = self.extract_sender_from_packet(packet_info)

        # Check if either name or key is banned
        if self.is_sender_banned(sender_name, sender_pubkey):
            ban_reason = []
            if sender_name:
                ban_reason.append(f"name='{sender_name}'")
            if sender_pubkey:
                ban_reason.append(f"key='{sender_pubkey[:16]}...'")
            self.logger.debug(f"Blocking MQTT packet: sender banned ({', '.join(ban_reason)})")
            return True

        return False
