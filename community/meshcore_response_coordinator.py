"""Standalone MeshCore response coordination helpers.

This module mirrors the current firmware timing model from the patch series:
- base delay + channel bias + hop bias + queue bias + tie-break bias + jitter
- quadratic hop growth with a cap
- response suppression by request token or response fingerprint
- recent-response suppression using the firmware TTL

The helpers are pure Python and are meant to be transported together with
``meshcore_request_token.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableSequence, Sequence, Union

from .meshcore_request_token import (
    parse_request_token_prefix,
    request_token_for_message,
    BotChannelKind,
)

MessageInput = Union[Mapping[str, Any], Any]

BOT_RESPONSE_DELAY_BASE_MILLIS = 1500
BOT_RESPONSE_DELAY_JITTER_MILLIS = 2200
BOT_RESPONSE_PENDING_TTL_MILLIS = 95000
BOT_RESPONSE_RECENT_TTL_MILLIS = 30000
BOT_HOP_STEP_MILLIS_DEFAULT = 2000
BOT_HOP_GROW_MILLIS = 400
BOT_HOP_BIAS_MAX_MILLIS = 50000


def _read_field(message: MessageInput, name: str, default: Any = None) -> Any:
    if isinstance(message, Mapping):
        return message.get(name, default)
    return getattr(message, name, default)


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def channel_delay_bias(channel_kind: BotChannelKind) -> int:
    if channel_kind == BotChannelKind.BOT_CHANNEL_DM:
        return 0
    if channel_kind == BotChannelKind.BOT_CHANNEL_BOT:
        return 200
    if channel_kind == BotChannelKind.BOT_CHANNEL_TESTING:
        return 400
    return 800


def hop_delay_bias(channel_kind: BotChannelKind, path_hash_count: int, hop_step_millis: int = BOT_HOP_STEP_MILLIS_DEFAULT) -> int:
    if channel_kind == BotChannelKind.BOT_CHANNEL_DM:
        return 0
    hop = max(0, int(path_hash_count))
    if hop <= 3:
        return 0
    bias = hop * int(hop_step_millis) + hop * hop * BOT_HOP_GROW_MILLIS
    return min(bias, BOT_HOP_BIAS_MAX_MILLIS)


def queue_delay_bias(queue_depth: int) -> int:
    return max(0, int(queue_depth)) * 150


def tie_break_bias(request_fingerprint: int, bot_identity_seed: int) -> int:
    fingerprint = _u32(int(request_fingerprint))
    mixed = fingerprint ^ _u32(int(request_fingerprint) >> 32) ^ _u32(int(bot_identity_seed))
    mixed = _u32(mixed)
    mixed ^= mixed >> 16
    mixed = _u32(mixed * 0x7FEB352D)
    mixed ^= mixed >> 15
    return mixed % 900


def response_delay_millis(
    message: MessageInput,
    request_fingerprint: int,
    bot_identity_seed: int,
    queue_depth: int,
    jitter_seed: int,
    *,
    base_delay_millis: int = BOT_RESPONSE_DELAY_BASE_MILLIS,
    jitter_millis: int = BOT_RESPONSE_DELAY_JITTER_MILLIS,
    hop_step_millis: int = BOT_HOP_STEP_MILLIS_DEFAULT,
) -> int:
    channel_kind = BotChannelKind(int(_read_field(message, "channel_kind", BotChannelKind.BOT_CHANNEL_DM)))
    path_hash_count = int(_read_field(message, "path_hash_count", 0))
    jitter = int(jitter_seed) % int(jitter_millis) if jitter_millis else 0
    return (
        int(base_delay_millis)
        + channel_delay_bias(channel_kind)
        + hop_delay_bias(channel_kind, path_hash_count, hop_step_millis)
        + queue_delay_bias(queue_depth)
        + tie_break_bias(request_fingerprint, bot_identity_seed)
        + jitter
    )


def response_due_at(
    now_millis: int,
    message: MessageInput,
    request_fingerprint: int,
    bot_identity_seed: int,
    queue_depth: int,
    jitter_seed: int,
    **kwargs: Any,
) -> int:
    return _u32(int(now_millis) + response_delay_millis(message, request_fingerprint, bot_identity_seed, queue_depth, jitter_seed, **kwargs))


@dataclass
class PendingBotResponse:
    request_fingerprint: int
    response_fingerprint: int
    # Channel information captured at schedule time to allow observed-peer
    # suppression to be restricted to the same channel.
    channel_kind: int = 0
    channel_name: str | bytes | None = None
    due_at_millis: int = 0
    expires_at_millis: int = 0
    active: bool = True
    sent: bool = False
    suppressed: bool = False


@dataclass
class RecentBotResponse:
    response_fingerprint: int
    observed_at_millis: int


def suppress_by_response_fingerprint(
    pending: MutableSequence[PendingBotResponse],
    response_fingerprint: int,
) -> bool:
    suppressed = False
    if response_fingerprint == 0:
        return False
    for entry in pending:
        if entry.active and entry.response_fingerprint == response_fingerprint:
            entry.suppressed = True
            suppressed = True
    return suppressed


def suppress_by_request_token(
    pending: MutableSequence[PendingBotResponse],
    request_token: int,
    *,
    channel_kind: int | None = None,
    channel_name: str | bytes | None = None,
) -> bool:
    suppressed = False
    if request_token == 0:
        return False
    token16 = int(request_token) & 0xFFFF
    for entry in pending:
        if not entry.active or entry.request_fingerprint == 0:
            continue
        if (int(entry.request_fingerprint) & 0xFFFF) != token16:
            continue
        # If caller provided channel constraints, only suppress when the
        # pending entry was scheduled for the same channel.
        if channel_kind is not None:
            try:
                if int(entry.channel_kind) != int(channel_kind):
                    continue
            except Exception:
                continue
        if channel_name is not None:
            try:
                # Normalize to bytes for comparison when possible
                en = entry.channel_name
                if isinstance(en, bytes):
                    en_cmp = en
                elif en is None:
                    en_cmp = b""
                else:
                    en_cmp = str(en).encode("utf-8")
                if isinstance(channel_name, bytes):
                    cn_cmp = channel_name
                else:
                    cn_cmp = str(channel_name or "").encode("utf-8")
                # Strip leading '#' if present (matches fingerprint logic)
                if cn_cmp.startswith(b"#"):
                    cn_cmp = cn_cmp[1:]
                if en_cmp.startswith(b"#"):
                    en_cmp = en_cmp[1:]
                if en_cmp.lower() != cn_cmp.lower():
                    continue
            except Exception:
                continue
        entry.suppressed = True
        suppressed = True
    return suppressed


def recently_sent(
    recent: Sequence[RecentBotResponse],
    response_fingerprint: int,
    now_millis: int,
    ttl_millis: int = BOT_RESPONSE_RECENT_TTL_MILLIS,
) -> bool:
    if response_fingerprint == 0:
        return False
    for entry in recent:
        if entry.response_fingerprint == response_fingerprint and (int(now_millis) - int(entry.observed_at_millis)) < int(ttl_millis):
            return True
    return False


def record_recent(
    recent: MutableSequence[RecentBotResponse],
    response_fingerprint: int,
    now_millis: int,
) -> None:
    if response_fingerprint == 0:
        return
    recent.append(RecentBotResponse(response_fingerprint=response_fingerprint, observed_at_millis=int(now_millis)))


def parse_peer_request_token(text: Union[str, bytes, bytearray, memoryview, None]) -> int | None:
    parsed = parse_request_token_prefix(text)
    return None if parsed is None else parsed[0]


def request_token_for_peer_message(message: MessageInput) -> int:
    return request_token_for_message(message)
