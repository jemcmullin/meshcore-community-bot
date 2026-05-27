"""Bot-scoped firmware coordination state manager.

Manages the decentralized, firmware-native response coordination protocol:
- Tracks pending responses waiting to be sent or suppressed
- Tracks recently sent responses to prevent duplicates
- Observes peer bot messages to suppress our own duplicate responses
- Computes per-message delays based on firmware timing formula
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from .meshcore_request_token import (
    parse_request_token_prefix,
    response_fingerprint_for_message,
)
from .meshcore_response_coordinator import (
    BOT_RESPONSE_PENDING_TTL_MILLIS,
    BOT_RESPONSE_RECENT_TTL_MILLIS,
    PendingBotResponse,
    RecentBotResponse,
    record_recent,
    recently_sent,
    request_token_for_peer_message,
    response_delay_millis,
    suppress_by_request_token,
)

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


class FirmwareCoordinator:
    """Manages firmware-native decentralized bot coordination state."""

    def __init__(self) -> None:
        self.bot_identity_seed: int = 0
        self.pending: list[PendingBotResponse] = []
        self.recent: list[RecentBotResponse] = []
        self._start_time_ms: int = _now_ms()

    def set_identity_seed(self, public_key_hex: str) -> None:
        """Derive bot identity seed from public key (first 4 bytes, big-endian uint32)."""
        try:
            key_bytes = bytes.fromhex(public_key_hex)
            self.bot_identity_seed = int.from_bytes(key_bytes[:4], "big")
            logger.info(
                "Firmware coordinator identity seed set: 0x%08x (from pubkey %s...)",
                self.bot_identity_seed,
                public_key_hex[:8],
            )
        except (ValueError, TypeError) as e:
            logger.warning("Failed to set identity seed from pubkey %r: %s", public_key_hex, e)
            self.bot_identity_seed = 0

    def observe_peer_message(self, text: str) -> bool:
        """Check incoming channel text for a peer bot token prefix.

        If a peer bot's ``[xxxx] `` prefix is found and matches any pending
        entry, those entries are suppressed.

        Returns True if any pending entries were suppressed.
        """
        parsed = parse_request_token_prefix(text)
        if parsed is None:
            return False
        token, _ = parsed
        suppressed = suppress_by_request_token(self.pending, token)
        if suppressed:
            logger.debug("Peer token [%04x] suppressed %d pending response(s)", token, sum(1 for e in self.pending if e.suppressed))
        return suppressed

    def schedule_response(
        self,
        fp_input: dict,
        response_text: str,
    ) -> Optional[tuple[PendingBotResponse, int]]:
        """Compute delay and create a pending entry for a new response.

        fp_input must contain all fields required by the firmware fingerprint
        functions (channel_kind, channel_name, sender_name, sender_key_prefix,
        sender_key_prefix_len, sender_timestamp, text, path_hash_count).

        Returns (entry, delay_ms) or None if the response was recently sent
        and should be skipped entirely.
        """
        now_ms = _now_ms()
        self.cleanup(now_ms)

        from .meshcore_request_token import request_fingerprint_for_message

        req_fp = request_fingerprint_for_message(fp_input)
        resp_fp = response_fingerprint_for_message(fp_input, response_text)

        if recently_sent(self.recent, resp_fp, now_ms):
            logger.debug("Skipping duplicate response (recently_sent): fp=0x%016x", resp_fp)
            return None

        jitter_seed = int.from_bytes(os.urandom(4), "big")
        queue_depth = self.queue_depth()

        delay_ms = response_delay_millis(
            fp_input,
            req_fp,
            self.bot_identity_seed,
            queue_depth,
            jitter_seed,
        )

        entry = PendingBotResponse(
            request_fingerprint=req_fp,
            response_fingerprint=resp_fp,
            due_at_millis=now_ms + delay_ms,
            expires_at_millis=now_ms + BOT_RESPONSE_PENDING_TTL_MILLIS,
            active=True,
            sent=False,
            suppressed=False,
        )
        self.pending.append(entry)
        logger.debug(
            "Scheduled response in %dms: token=[%04x] resp_fp=0x%016x queue=%d",
            delay_ms,
            req_fp & 0xFFFF,
            resp_fp,
            queue_depth,
        )
        return entry, delay_ms

    def mark_sent(self, entry: PendingBotResponse) -> None:
        """Record that a pending response was actually sent."""
        now_ms = _now_ms()
        entry.sent = True
        record_recent(self.recent, entry.response_fingerprint, now_ms)
        logger.debug("Marked sent: resp_fp=0x%016x", entry.response_fingerprint)

    def queue_depth(self) -> int:
        """Return number of active pending entries."""
        return sum(1 for e in self.pending if e.active and not e.sent and not e.suppressed)

    def cleanup(self, now_ms: Optional[int] = None) -> None:
        """Remove expired pending and recent entries."""
        if now_ms is None:
            now_ms = _now_ms()
        self.pending = [e for e in self.pending if e.active and now_ms < e.expires_at_millis]
        self.recent = [r for r in self.recent if (now_ms - r.observed_at_millis) < BOT_RESPONSE_RECENT_TTL_MILLIS]

    def uptime_seconds(self) -> int:
        return (_now_ms() - self._start_time_ms) // 1000
