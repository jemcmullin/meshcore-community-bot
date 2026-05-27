"""Validation tests for firmware-native coordination modules.

Mirrors the assertions in community/standalone_request_token/ but imports
from the community package (relative-import versions) that are actually used
at runtime.  Every assertion here is derived from a recorded firmware behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from community.meshcore_request_token import (
    BotMessage,
    format_request_token,
    prepend_request_token,
    prepend_request_token_text,
    request_fingerprint_for_message,
    request_token_for_message,
    response_fingerprint_for_message,
)
from community.meshcore_response_coordinator import (
    BOT_CHANNEL_BOT,
    BOT_CHANNEL_DM,
    BOT_CHANNEL_TESTING,
    BOT_HOP_BIAS_MAX_MILLIS,
    BOT_HOP_GROW_MILLIS,
    BOT_HOP_STEP_MILLIS_DEFAULT,
    BOT_RESPONSE_DELAY_BASE_MILLIS,
    BOT_RESPONSE_DELAY_JITTER_MILLIS,
    BOT_RESPONSE_PENDING_TTL_MILLIS,
    BOT_RESPONSE_RECENT_TTL_MILLIS,
    PendingBotResponse,
    RecentBotResponse,
    channel_delay_bias,
    hop_delay_bias,
    parse_peer_request_token,
    queue_delay_bias,
    recently_sent,
    record_recent,
    request_token_for_peer_message,
    response_delay_millis,
    response_due_at,
    suppress_by_request_token,
    suppress_by_response_fingerprint,
    tie_break_bias,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Msg:
    channel_kind: int
    channel_name: str
    sender_name: str
    sender_key_prefix: bytes
    sender_key_prefix_len: int
    sender_timestamp: int
    text: str
    path_hash_count: int = 0


_DICT_MSG = {
    "channel_kind": 1,
    "channel_name": "#bot",
    "sender_name": "Alice",
    "sender_key_prefix": b"\x01\x02\x03\x04\x05\x06",
    "sender_key_prefix_len": 6,
    "sender_timestamp": 1710000000,
    "text": "  Trace\nSent  ",
}

_OBJ_MSG = BotMessage(
    channel_kind=0,
    channel_name="dm",
    sender_name="Bob",
    sender_key_prefix=b"\x10\x20\x30\x40\x50\x60",
    sender_key_prefix_len=6,
    sender_timestamp=1710000001,
    text="Pong",
)


# ---------------------------------------------------------------------------
# meshcore_request_token — dict-style input
# ---------------------------------------------------------------------------

def test_dict_request_fingerprint():
    assert request_fingerprint_for_message(_DICT_MSG) == 0x6714C4F4C3006617


def test_dict_request_token():
    assert request_token_for_message(_DICT_MSG) == 0x6617


def test_dict_format_token():
    assert format_request_token(request_token_for_message(_DICT_MSG)) == "6617"


def test_dict_prepend_token_text():
    assert prepend_request_token_text(_DICT_MSG, "Trace sent") == "[6617] Trace sent"


def test_dict_response_fingerprint():
    assert (
        response_fingerprint_for_message(_DICT_MSG, "Trace sent", dm_channel_kind=0)
        == 0xC6D333E42C1FD502
    )


# ---------------------------------------------------------------------------
# meshcore_request_token — object-style input
# ---------------------------------------------------------------------------

def test_object_request_fingerprint():
    assert request_fingerprint_for_message(_OBJ_MSG) == 0x0E0E4311003CC988


def test_object_request_token():
    assert request_token_for_message(_OBJ_MSG) == 0xC988


def test_object_format_token():
    assert format_request_token(request_token_for_message(_OBJ_MSG)) == "c988"


def test_object_prepend_token_bytes():
    assert prepend_request_token(_OBJ_MSG, b"Pong") == b"[c988] Pong"


def test_object_response_fingerprint():
    assert (
        response_fingerprint_for_message(_OBJ_MSG, "Pong", dm_channel_kind=0)
        == 0xC51D68AD4AAAF5D2
    )


# ---------------------------------------------------------------------------
# meshcore_response_coordinator — timing constants
# ---------------------------------------------------------------------------

def test_constants():
    assert BOT_RESPONSE_DELAY_BASE_MILLIS == 1500
    assert BOT_RESPONSE_DELAY_JITTER_MILLIS == 2200
    assert BOT_RESPONSE_PENDING_TTL_MILLIS == 95000
    assert BOT_RESPONSE_RECENT_TTL_MILLIS == 30000
    assert BOT_HOP_GROW_MILLIS == 400
    assert BOT_HOP_BIAS_MAX_MILLIS == 50000
    assert BOT_HOP_STEP_MILLIS_DEFAULT == 2000
    assert BOT_CHANNEL_DM == 0
    assert BOT_CHANNEL_BOT == 1
    assert BOT_CHANNEL_TESTING == 2


# ---------------------------------------------------------------------------
# meshcore_response_coordinator — timing formula
# ---------------------------------------------------------------------------

_TIMING_MSG = _Msg(
    channel_kind=BOT_CHANNEL_BOT,
    channel_name="#bot",
    sender_name="Alice",
    sender_key_prefix=b"\x01\x02\x03\x04\x05\x06",
    sender_key_prefix_len=6,
    sender_timestamp=1710000000,
    text="Trace now",
    path_hash_count=3,
)
_TIMING_FP = request_fingerprint_for_message(_TIMING_MSG)


def test_channel_delay_bias():
    assert channel_delay_bias(BOT_CHANNEL_BOT) == 200


def test_hop_delay_bias():
    assert hop_delay_bias(BOT_CHANNEL_BOT, 3) == 9600


def test_queue_delay_bias():
    assert queue_delay_bias(4) == 600


def test_tie_break_bias():
    assert tie_break_bias(_TIMING_FP, 0x12345678) == 530


def test_response_delay_millis():
    assert response_delay_millis(_TIMING_MSG, _TIMING_FP, 0x12345678, 4, 9876) == 13506


def test_response_due_at():
    assert response_due_at(1000, _TIMING_MSG, _TIMING_FP, 0x12345678, 4, 9876) == 14506


# ---------------------------------------------------------------------------
# meshcore_response_coordinator — suppression
# ---------------------------------------------------------------------------

_PEER_MSG = {
    "channel_kind": BOT_CHANNEL_BOT,
    "channel_name": "#bot",
    "sender_name": "Alice",
    "sender_key_prefix": b"\x01\x02\x03\x04\x05\x06",
    "sender_key_prefix_len": 6,
    "sender_timestamp": 1710000000,
    "text": "Trace now",
}
_PEER_FP = request_fingerprint_for_message(_PEER_MSG)
_PEER_TOKEN = request_token_for_message(_PEER_MSG)


def test_request_token_for_peer_message():
    assert request_token_for_peer_message(_PEER_MSG) == _PEER_TOKEN


def test_parse_peer_request_token():
    assert parse_peer_request_token("[6617] Trace sent") == 0x6617


def test_suppress_by_request_token():
    pending = [
        PendingBotResponse(request_fingerprint=_PEER_FP, response_fingerprint=0xAAAABBBBCCCCDDDD),
        PendingBotResponse(request_fingerprint=_PEER_FP ^ 1, response_fingerprint=0x1111222233334444),
    ]
    assert suppress_by_request_token(pending, _PEER_TOKEN) is True
    assert pending[0].suppressed is True
    assert pending[1].suppressed is False


def test_suppress_by_response_fingerprint():
    pending = [
        PendingBotResponse(request_fingerprint=_PEER_FP, response_fingerprint=0xAAAABBBBCCCCDDDD),
        PendingBotResponse(request_fingerprint=_PEER_FP ^ 1, response_fingerprint=0x1111222233334444),
    ]
    assert suppress_by_response_fingerprint(pending, 0xAAAABBBBCCCCDDDD) is True
    assert pending[0].suppressed is True
    assert pending[1].suppressed is False


def test_recently_sent_hit():
    recent = [RecentBotResponse(response_fingerprint=0xABCDEF, observed_at_millis=1000)]
    assert recently_sent(recent, 0xABCDEF, 2000) is True


def test_recently_sent_expired():
    recent = [RecentBotResponse(response_fingerprint=0xABCDEF, observed_at_millis=1000)]
    assert recently_sent(recent, 0xABCDEF, 33001) is False


def test_record_recent():
    recent: list[RecentBotResponse] = []
    record_recent(recent, 0x1234, 4000)
    assert recent[-1].response_fingerprint == 0x1234
    assert recent[-1].observed_at_millis == 4000
