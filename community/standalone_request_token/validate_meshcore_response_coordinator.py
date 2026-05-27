"""Validation for the standalone MeshCore response coordinator helpers."""

from __future__ import annotations

from dataclasses import dataclass

from meshcore_request_token import request_fingerprint_for_message, request_token_for_message
from meshcore_response_coordinator import (
    PendingBotResponse,
    RecentBotResponse,
    BOT_CHANNEL_BOT,
    BOT_CHANNEL_DM,
    BOT_CHANNEL_TESTING,
    BOT_RESPONSE_DELAY_BASE_MILLIS,
    BOT_RESPONSE_DELAY_JITTER_MILLIS,
    BOT_RESPONSE_PENDING_TTL_MILLIS,
    BOT_RESPONSE_RECENT_TTL_MILLIS,
    BOT_HOP_BIAS_MAX_MILLIS,
    BOT_HOP_GROW_MILLIS,
    BOT_HOP_STEP_MILLIS_DEFAULT,
    channel_delay_bias,
    hop_delay_bias,
    parse_peer_request_token,
    recently_sent,
    record_recent,
    request_token_for_peer_message,
    response_delay_millis,
    response_due_at,
    suppress_by_request_token,
    suppress_by_response_fingerprint,
    tie_break_bias,
    queue_delay_bias,
)


@dataclass(frozen=True)
class SampleMessage:
    channel_kind: int
    channel_name: str
    sender_name: str
    sender_key_prefix: bytes
    sender_key_prefix_len: int
    sender_timestamp: int
    text: str
    path_hash_count: int


def _assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def validate_timing() -> None:
    message = SampleMessage(
        channel_kind=BOT_CHANNEL_BOT,
        channel_name="#bot",
        sender_name="Alice",
        sender_key_prefix=b"\x01\x02\x03\x04\x05\x06",
        sender_key_prefix_len=6,
        sender_timestamp=1710000000,
        text="Trace now",
        path_hash_count=3,
    )
    request_fp = request_fingerprint_for_message(message)

    _assert_equal(channel_delay_bias(message.channel_kind), 200, "channel bias")
    _assert_equal(hop_delay_bias(message.channel_kind, message.path_hash_count), 9600, "hop bias")
    _assert_equal(queue_delay_bias(4), 600, "queue bias")
    _assert_equal(tie_break_bias(request_fp, 0x12345678), 530, "tie-break bias")

    delay = response_delay_millis(
        message,
        request_fp,
        0x12345678,
        4,
        9876,
    )
    _assert_equal(delay, 13506, "response delay")
    _assert_equal(
        response_due_at(1000, message, request_fp, 0x12345678, 4, 9876),
        14506,
        "response due at",
    )


def validate_suppression() -> None:
    peer_message = {
        "channel_kind": BOT_CHANNEL_BOT,
        "channel_name": "#bot",
        "sender_name": "Alice",
        "sender_key_prefix": b"\x01\x02\x03\x04\x05\x06",
        "sender_key_prefix_len": 6,
        "sender_timestamp": 1710000000,
        "text": "Trace now",
    }
    request_fp = request_fingerprint_for_message(peer_message)
    token = request_token_for_message(peer_message)
    _assert_equal(request_token_for_peer_message(peer_message), token, "peer token helper")
    _assert_equal(parse_peer_request_token("[6617] Trace sent"), 0x6617, "parse token")

    pending = [
        PendingBotResponse(request_fingerprint=request_fp, response_fingerprint=0xAAAABBBBCCCCDDDD),
        PendingBotResponse(request_fingerprint=request_fp ^ 1, response_fingerprint=0x1111222233334444),
    ]
    _assert_equal(suppress_by_request_token(pending, token), True, "token suppression")
    _assert_equal(pending[0].suppressed, True, "token suppression flag")
    _assert_equal(pending[1].suppressed, False, "token suppression unaffected")

    pending2 = [
        PendingBotResponse(request_fingerprint=request_fp, response_fingerprint=0xAAAABBBBCCCCDDDD),
        PendingBotResponse(request_fingerprint=request_fp ^ 1, response_fingerprint=0x1111222233334444),
    ]
    _assert_equal(suppress_by_response_fingerprint(pending2, 0xAAAABBBBCCCCDDDD), True, "response suppression")
    _assert_equal(pending2[0].suppressed, True, "response suppression flag")
    _assert_equal(pending2[1].suppressed, False, "response suppression unaffected")

    recent = [RecentBotResponse(response_fingerprint=0xABCDEF, observed_at_millis=1000)]
    _assert_equal(recently_sent(recent, 0xABCDEF, 2000), True, "recent hit")
    _assert_equal(recently_sent(recent, 0xABCDEF, 33001), False, "recent expiry")

    record_recent(recent, 0x1234, 4000)
    _assert_equal(recent[-1].response_fingerprint, 0x1234, "record recent fingerprint")
    _assert_equal(BOT_RESPONSE_PENDING_TTL_MILLIS, 95000, "pending ttl constant")
    _assert_equal(BOT_RESPONSE_RECENT_TTL_MILLIS, 30000, "recent ttl constant")
    _assert_equal(BOT_HOP_GROW_MILLIS, 400, "hop grow constant")
    _assert_equal(BOT_HOP_BIAS_MAX_MILLIS, 50000, "hop cap constant")
    _assert_equal(BOT_HOP_STEP_MILLIS_DEFAULT, 2000, "hop step constant")
    _assert_equal(BOT_RESPONSE_DELAY_BASE_MILLIS, 1500, "base delay constant")
    _assert_equal(BOT_RESPONSE_DELAY_JITTER_MILLIS, 2200, "jitter constant")
    _assert_equal(BOT_CHANNEL_DM, 0, "dm enum")
    _assert_equal(BOT_CHANNEL_BOT, 1, "bot enum")
    _assert_equal(BOT_CHANNEL_TESTING, 2, "testing enum")


def main() -> int:
    validate_timing()
    validate_suppression()
    print("meshcore_response_coordinator validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())