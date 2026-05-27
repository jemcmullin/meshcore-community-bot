"""Standalone MeshCore request fingerprint and response token helpers.

This module reconstructs the firmware behavior used by the MeshCore companion
radio bot patches:
- request fingerprints are 64-bit FNV-1a hashes over normalized message fields
- the response token is the low 16 bits of the request fingerprint
- the response prefix is the token rendered as "[xxxx] " with lowercase hex

The module is intentionally self-contained and depends only on the Python
standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Union

FNV64_OFFSET = 1469598103934665603
FNV64_PRIME = 1099511628211

BOT_MAX_TEXT_LEN = 160
BOT_MAX_RESPONSE_LEN = 144
BOT_MAX_CHANNEL_NAME_LEN = 23
BOT_MAX_SENDER_NAME_LEN = 31
BOT_SENDER_KEY_PREFIX_LEN = 6
BOT_GROUP_RESPONSE_GUARD_PREFIX = "# "
REQUEST_TOKEN_PREFIX_LEN = 7

BytesLike = Union[bytes, bytearray, memoryview]
MessageInput = Union[Mapping[str, Any], Any]


@dataclass(frozen=True)
class BotMessage:
    channel_kind: int
    channel_name: Union[str, BytesLike]
    sender_name: Union[str, BytesLike]
    sender_key_prefix: Union[str, BytesLike]
    sender_key_prefix_len: int
    sender_timestamp: int
    text: Union[str, BytesLike]


def _read_field(message: MessageInput, name: str, default: Any = None) -> Any:
    if isinstance(message, Mapping):
        return message.get(name, default)
    return getattr(message, name, default)


def _as_bytes(value: Union[str, BytesLike, None]) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"expected bytes-like or str value, got {type(value)!r}")


def _bounded_c_string(value: Union[str, BytesLike, None], limit: int) -> bytes:
    data = _as_bytes(value)
    nul_index = data.find(b"\x00")
    if nul_index != -1:
        data = data[:nul_index]
    return data[:limit]


def _normalize_text(value: Union[str, BytesLike, None], input_len: int) -> bytes:
    """Match FirmwareBot::normalizeText()."""
    data = _as_bytes(value)
    if input_len < len(data):
        data = data[:input_len]

    out = bytearray()
    pending_space = False

    for byte_value in data:
        if byte_value == 0:
            break

        if byte_value in (9, 10, 13, 32) or byte_value < 0x20 or byte_value == 0x7F:
            if len(out) >= BOT_MAX_TEXT_LEN:
                break
            pending_space = len(out) > 0
            continue

        if len(out) >= BOT_MAX_TEXT_LEN:
            break

        if pending_space:
            out.append(32)
            pending_space = False

        out.append(byte_value)

    return bytes(out)


def _fnv1a_update(hash_value: int, byte_value: int) -> int:
    return ((hash_value ^ (byte_value & 0xFF)) * FNV64_PRIME) & 0xFFFFFFFFFFFFFFFF


def _fnv1a_update_bytes(hash_value: int, data: bytes) -> int:
    for byte_value in data:
        hash_value = _fnv1a_update(hash_value, byte_value)
    return hash_value


def _fnv1a_update_text_lower(hash_value: int, value: Union[str, BytesLike, None], limit: int) -> int:
    return _fnv1a_update_bytes(hash_value, _bounded_c_string(value, limit).lower())


def _fnv1a_update_u32(hash_value: int, value: int) -> int:
    return _fnv1a_update_bytes(hash_value, int(value & 0xFFFFFFFF).to_bytes(4, "little", signed=False))


def _update_channel(hash_value: int, message: MessageInput) -> int:
    channel_kind = int(_read_field(message, "channel_kind", 0))
    channel_name = _bounded_c_string(_read_field(message, "channel_name", b""), BOT_MAX_CHANNEL_NAME_LEN + 1)
    if channel_name.startswith(b"#"):
        channel_name = channel_name[1:]
    hash_value = _fnv1a_update(hash_value, channel_kind)
    return _fnv1a_update_bytes(hash_value, channel_name.lower())


def request_fingerprint_for_message(message: MessageInput) -> int:
    """Return the firmware-compatible 64-bit request fingerprint.

    Required message fields:
    - channel_kind: integer
    - channel_name: bytes or str
    - sender_name: bytes or str
    - sender_key_prefix: bytes or str
    - sender_key_prefix_len: integer
    - sender_timestamp: integer
    - text: bytes or str

    Notes:
    - channel_name and sender_name are treated like C strings and trimmed at the
      first NUL byte, then bounded to their firmware limits.
    - channel_name loses a leading '#'.
    - sender_name and normalized text are hashed in lowercase.
    - sender_timestamp is hashed as little-endian uint32.
    - sender_key_prefix is always hashed as exactly 6 bytes for request
      fingerprints, padded with zero bytes if needed.
    """
    hash_value = FNV64_OFFSET
    hash_value = _update_channel(hash_value, message)

    sender_key_prefix = _as_bytes(_read_field(message, "sender_key_prefix", b""))
    sender_key_prefix = sender_key_prefix[:BOT_SENDER_KEY_PREFIX_LEN].ljust(BOT_SENDER_KEY_PREFIX_LEN, b"\x00")
    hash_value = _fnv1a_update_bytes(hash_value, sender_key_prefix)

    hash_value = _fnv1a_update_text_lower(
        hash_value,
        _read_field(message, "sender_name", b""),
        BOT_MAX_SENDER_NAME_LEN + 1,
    )
    hash_value = _fnv1a_update_u32(hash_value, int(_read_field(message, "sender_timestamp", 0)))

    text_value = _read_field(message, "text", b"")
    text_len = int(_read_field(message, "text_len", len(_as_bytes(text_value))))
    normalized_text = _normalize_text(text_value, text_len)
    hash_value = _fnv1a_update_bytes(hash_value, normalized_text.lower())
    return hash_value


def request_token_for_message(message: MessageInput) -> int:
    """Return the 16-bit token used in the response prefix."""
    return request_fingerprint_for_message(message) & 0xFFFF


def format_request_token(token: int) -> str:
    """Render a token exactly like the firmware: four lowercase hex digits."""
    return f"{token & 0xFFFF:04x}"


def parse_request_token_prefix(text: Union[str, BytesLike, None]) -> tuple[int, int] | None:
    """Parse the firmware request-token prefix.

    Returns (token, prefix_len) for strings like ``[1a2b] rest``.
    Returns None when the prefix is absent or malformed.
    """
    data = _as_bytes(text)
    if len(data) < REQUEST_TOKEN_PREFIX_LEN:
        return None
    if data[0] != ord("[") or data[5] != ord("]") or data[6] != ord(" "):
        return None

    token = 0
    for byte_value in data[1:5]:
        if 48 <= byte_value <= 57:
            nibble = byte_value - 48
        elif 97 <= byte_value <= 102:
            nibble = 10 + (byte_value - 97)
        elif 65 <= byte_value <= 70:
            nibble = 10 + (byte_value - 65)
        else:
            return None
        token = (token << 4) | nibble

    return token, REQUEST_TOKEN_PREFIX_LEN


def prepend_request_token(message: MessageInput, response_text: Union[str, BytesLike]) -> bytes:
    """Return b"[xxxx] " + response_text.

    This matches FirmwareBot::prependRequestToken(). The output is bytes so it
    can be sent directly over a transport without any additional encoding.
    """
    token = format_request_token(request_token_for_message(message)).encode("ascii")
    return b"[" + token + b"] " + _as_bytes(response_text)


def response_fingerprint_for_message(
    message: MessageInput,
    response_text: Union[str, BytesLike],
    *,
    dm_channel_kind: int = 0,
) -> int:
    """Return the firmware-compatible 64-bit response fingerprint.

    The firmware includes sender_key_prefix bytes only for DM responses. If your
    program uses different channel enum values, pass the DM enum value via
    dm_channel_kind.
    """
    hash_value = FNV64_OFFSET
    hash_value = _update_channel(hash_value, message)

    if int(_read_field(message, "channel_kind", 0)) == int(dm_channel_kind):
        sender_key_prefix_len = int(_read_field(message, "sender_key_prefix_len", BOT_SENDER_KEY_PREFIX_LEN))
        sender_key_prefix = _as_bytes(_read_field(message, "sender_key_prefix", b""))[:sender_key_prefix_len]
        hash_value = _fnv1a_update(hash_value, sender_key_prefix_len)
        hash_value = _fnv1a_update_bytes(hash_value, sender_key_prefix)

    response_bytes = _as_bytes(response_text)
    response_len = len(response_bytes)
    normalized_response = _normalize_text(response_bytes, response_len)
    hash_value = _fnv1a_update_bytes(hash_value, normalized_response.lower())
    return hash_value


def prepend_request_token_text(message: MessageInput, response_text: str) -> str:
    """Convenience wrapper for text workflows."""
    return prepend_request_token(message, response_text).decode("utf-8", errors="strict")
