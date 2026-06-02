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
    response_delay_millis,
    suppress_by_request_token,
)

logger = logging.getLogger('CommunityBot')

def _now_ms() -> int:
    return int(time.monotonic() * 1000)


class FirmwareCoordinator:
    """Manages firmware-native decentralized bot coordination state."""

    def __init__(self) -> None:
        self.bot_identity_seed: int = 0
        self.pending: list[PendingBotResponse] = []
        self.recent: list[RecentBotResponse] = []
        self._start_time_ms: int = _now_ms()
        # Observed peer tokens: list of tuples (token:int, observed_at_ms:int)
        # Used for diagnostics to compare which peer tokens were heard nearby in time.
        self.observed_peer_tokens: list[tuple[int, int]] = []
        self.coordination_mode_queue: bool = False  # If True, suppress all responses when any pending (conservative fallback mode)

    def set_identity_seed(self, public_key_hex: str) -> None:
        """Derive bot identity seed from public key (first 4 bytes, big-endian uint32)."""
        try:
            key_bytes = bytes.fromhex(public_key_hex)
            if len(key_bytes) < 4:
                raise ValueError("public key shorter than 4 bytes")
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
        now = _now_ms()
        # Record observed token for diagnostics (keep bounded history)
        try:
            self.observed_peer_tokens.append((token, now))
            # Keep recent window of ~60s to avoid unbounded growth
            cutoff = now - 60000
            self.observed_peer_tokens = [t for t in self.observed_peer_tokens if t[1] >= cutoff]
        except Exception:
            pass
        logger.debug("[TOKEN] observed peer token prefix: [%04x]", token, extra={"log_color": "HIGHLIGHT"})
        if self.coordination_mode_queue:
            # clear queue as conservative coordination without proper token matching
            for entry in self.pending:
                entry.suppressed = True
            logger.info("Responses suppressed by observed peer message (queue mode)")
            return True
        suppressed = suppress_by_request_token(self.pending, token)
        if suppressed:
            logger.debug("[TOKEN] Peer token [%04x] suppressed %d pending response(s)", token, sum(1 for e in self.pending if e.suppressed), extra={"log_color": "HIGHLIGHT"})
        return suppressed

    def get_observed_peer_tokens(self, window_ms: int = 20000) -> list[int]:
        """Return peer tokens observed within the last `window_ms` milliseconds.

        Returns a list of unique token ints (low 16 bits) observed in that window,
        ordered newest-first.
        """
        now = _now_ms()
        cutoff = now - int(window_ms)
        tokens = [t for t, ts in self.observed_peer_tokens if ts >= cutoff]
        # Deduplicate preserving order (newest first)
        seen = set()
        out = []
        for token in reversed(tokens):
            if token not in seen:
                seen.add(token)
                out.append(token)
        return list(reversed(out))

    def schedule_response(
        self,
        fp_input: dict,
        response_text: str,
        now_ms: Optional[int] = None,
    ) -> Optional[tuple[PendingBotResponse, int]]:
        """Compute delay and create a pending entry for a new response.

        fp_input must contain all fields required by the firmware fingerprint
        functions (channel_kind, channel_name, sender_name, sender_key_prefix,
        sender_key_prefix_len, sender_timestamp, text, path_hash_count).

        Returns (entry, delay_ms) or None if the response was recently sent
        and should be skipped entirely.
        """
        if now_ms is None:
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
            due_at_millis=(now_ms + delay_ms) & 0xFFFFFFFF,
            expires_at_millis=(now_ms + BOT_RESPONSE_PENDING_TTL_MILLIS) & 0xFFFFFFFF,
            active=True,
            sent=False,
            suppressed=False,
        )
        self.pending.append(entry)
        logger.debug(
            "[TOKEN] Scheduled response in %dms: token=[%04x] resp_fp=0x%016x queue=%d",
            delay_ms,
            req_fp & 0xFFFF,
            resp_fp,
            queue_depth,
            extra={"log_color": "HIGHLIGHT"},
        )
        return entry, delay_ms

    def mark_sent(self, entry: PendingBotResponse, now_ms: Optional[int] = None) -> None:
        """Record that a pending response was actually sent."""
        if now_ms is None:
            now_ms = _now_ms()
        entry.sent = True
        record_recent(self.recent, entry.response_fingerprint, now_ms)
        logger.debug("Marked sent: resp_fp=0x%016x", entry.response_fingerprint, extra={"log_color": "HIGHLIGHT"})

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
