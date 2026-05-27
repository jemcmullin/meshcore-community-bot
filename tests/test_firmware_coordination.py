"""Validation tests for firmware-native coordination modules.

Mirrors the assertions in community/standalone_request_token/ but imports
from the community package (relative-import versions) that are actually used
at runtime.  Every assertion here is derived from a recorded firmware behavior.
"""

from __future__ import annotations

import asyncio
from configparser import ConfigParser
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from community.firmware_coordinator import FirmwareCoordinator
from community.message_interceptor import MessageInterceptor, raw_text_var
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


# ---------------------------------------------------------------------------
# Integration-style tests — patched interceptor flow
# ---------------------------------------------------------------------------


class _CapturingFirmwareCoordinator(FirmwareCoordinator):
    def __init__(self):
        super().__init__()
        self.schedule_calls: list[tuple[dict, str, int | None]] = []
        self.mark_calls: list[tuple[PendingBotResponse, int | None]] = []

    def schedule_response(self, fp_input: dict, response_text: str, now_ms: int | None = None):
        self.schedule_calls.append((dict(fp_input), response_text, now_ms))
        return super().schedule_response(fp_input, response_text, now_ms)

    def mark_sent(self, entry: PendingBotResponse, now_ms: int | None = None) -> None:
        self.mark_calls.append((entry, now_ms))
        super().mark_sent(entry, now_ms)


def _build_message(raw_text: str):
    sender, _, content = raw_text.partition(":")
    return SimpleNamespace(
        is_dm=False,
        content=content.strip(),
        sender_id=sender.strip(),
        sender_pubkey="00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
        timestamp=1710000000,
        channel="#bot",
        hops=2,
    )


def _build_interceptor_harness(monkeypatch, *, send_results=None):
    import community.message_interceptor as message_interceptor_module
    import community.firmware_coordinator as firmware_coordinator_module

    config = ConfigParser()
    config.add_section("Discord")
    config.add_section("Bot")
    config.set("Bot", "bot_name", "TestBot")

    sent_payloads: list[str] = []
    channel_payloads: list[tuple[str, str]] = []

    send_queue = list(send_results or [True])

    async def original_send_response(message, content, *args, **kwargs):
        sent_payloads.append(content)
        if send_queue:
            return send_queue.pop(0)
        return True

    async def original_send_channel_message(channel, content, *args, **kwargs):
        channel_payloads.append((channel, content))
        if send_queue:
            return send_queue.pop(0)
        return True

    bot = SimpleNamespace()
    bot.config = config
    bot.logger = Mock()
    bot.message_handler = SimpleNamespace()
    bot.command_manager = SimpleNamespace(
        send_response=AsyncMock(side_effect=original_send_response),
        send_channel_message=AsyncMock(side_effect=original_send_channel_message),
    )

    async def original_process_message(message, *args, **kwargs):
        return await bot.command_manager.send_response(message, "Pong!")

    async def original_handle_channel_message(event, *args, **kwargs):
        message = _build_message(event.payload.get("text", ""))
        return await bot.message_handler.process_message(message, *args, **kwargs)

    bot.message_handler.handle_channel_message = original_handle_channel_message
    bot.message_handler.process_message = original_process_message

    firmware = _CapturingFirmwareCoordinator()

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(message_interceptor_module, "publish_web_viewer_fw_event", _noop)
    monkeypatch.setattr(message_interceptor_module, "send_to_discord", _noop)
    monkeypatch.setattr(firmware_coordinator_module, "response_delay_millis", lambda *args, **kwargs: 0)
    monkeypatch.setattr(firmware_coordinator_module.os, "urandom", lambda n: b"\x00" * n)

    interceptor = MessageInterceptor(bot=bot, firmware_coordinator=firmware)
    return SimpleNamespace(
        bot=bot,
        firmware=firmware,
        interceptor=interceptor,
        sent_payloads=sent_payloads,
        channel_payloads=channel_payloads,
    )


def test_interceptor_schedules_unprefixed_response_but_sends_prefixed(monkeypatch):
    harness = _build_interceptor_harness(monkeypatch)
    event = SimpleNamespace(payload={"text": "HOWL: !ping"})

    result = asyncio.run(harness.bot.message_handler.handle_channel_message(event))

    assert result is True
    assert len(harness.firmware.schedule_calls) == 1

    fp_input, response_text, now_ms = harness.firmware.schedule_calls[0]
    assert fp_input["text"] == "HOWL: !ping"
    assert response_text == "Pong!"
    assert now_ms is not None
    assert harness.sent_payloads == [prepend_request_token_text(fp_input, "Pong!")]
    assert len(harness.firmware.mark_calls) == 1
    assert harness.firmware.mark_calls[0][0].sent is True


def test_interceptor_suppresses_pending_response_when_peer_token_arrives(monkeypatch):
    harness = _build_interceptor_harness(monkeypatch)
    event = SimpleNamespace(payload={"text": "HOWL: !ping"})

    async def suppressing_sleep(delay):
        assert len(harness.firmware.schedule_calls) == 1
        fp_input, _, _ = harness.firmware.schedule_calls[0]
        token = request_token_for_message(fp_input)
        harness.firmware.observe_peer_message(f"[{token:04x}] Peer won")

    with patch("community.message_interceptor.asyncio.sleep", new=suppressing_sleep):
        result = asyncio.run(harness.bot.message_handler.handle_channel_message(event))

    assert result is False
    assert harness.sent_payloads == []
    assert harness.firmware.mark_calls == []
    assert harness.interceptor._original_send_response.await_count == 0


def test_interceptor_multi_chunk_followup_uses_cached_win_without_reschedule(monkeypatch):
    harness = _build_interceptor_harness(monkeypatch)
    message = _build_message("HOWL: !trace")
    token = raw_text_var.set("HOWL: !trace")
    try:
        first = asyncio.run(harness.bot.command_manager.send_response(message, "Chunk one"))
        second = asyncio.run(harness.bot.command_manager.send_response(message, "Chunk two"))
    finally:
        raw_text_var.reset(token)

    assert first is True
    assert second is True
    assert len(harness.firmware.schedule_calls) == 1
    fp_input, _, _ = harness.firmware.schedule_calls[0]
    assert harness.sent_payloads == [
        prepend_request_token_text(fp_input, "Chunk one"),
        prepend_request_token_text(fp_input, "Chunk two"),
    ]


def test_interceptor_send_failure_does_not_mark_sent_or_cache_win(monkeypatch):
    harness = _build_interceptor_harness(monkeypatch, send_results=[False, True])
    message = _build_message("HOWL: !ping")
    token = raw_text_var.set("HOWL: !ping")
    try:
        first = asyncio.run(harness.bot.command_manager.send_response(message, "Pong!"))
        second = asyncio.run(harness.bot.command_manager.send_response(message, "Pong!"))
    finally:
        raw_text_var.reset(token)

    assert first is False
    assert second is True
    assert harness.firmware.mark_calls == [] or len(harness.firmware.mark_calls) == 1
    assert len(harness.firmware.schedule_calls) == 2
    if harness.firmware.mark_calls:
        assert harness.firmware.mark_calls[0][0].sent is True


