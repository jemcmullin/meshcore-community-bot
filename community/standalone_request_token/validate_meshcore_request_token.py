"""Standalone validation for the MeshCore request token helpers.

Run this file directly to confirm the module still matches the recorded
firmware behavior for both dict-style and object-style message inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from meshcore_request_token import (
    BotMessage,
    prepend_request_token,
    prepend_request_token_text,
    request_fingerprint_for_message,
    request_token_for_message,
    format_request_token,
    response_fingerprint_for_message,
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


def _assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def validate_dict_message() -> None:
    message = {
        "channel_kind": 1,
        "channel_name": "#bot",
        "sender_name": "Alice",
        "sender_key_prefix": b"\x01\x02\x03\x04\x05\x06",
        "sender_key_prefix_len": 6,
        "sender_timestamp": 1710000000,
        "text": "  Trace\nSent  ",
    }

    _assert_equal(request_fingerprint_for_message(message), 0x6714C4F4C3006617, "dict request fingerprint")
    _assert_equal(request_token_for_message(message), 0x6617, "dict request token")
    _assert_equal(format_request_token(request_token_for_message(message)), "6617", "dict token text")
    _assert_equal(prepend_request_token_text(message, "Trace sent"), "[6617] Trace sent", "dict token prefix")
    _assert_equal(
        response_fingerprint_for_message(message, "Trace sent", dm_channel_kind=0),
        0xC6D333E42C1FD502,
        "dict response fingerprint",
    )


def validate_object_message() -> None:
    message = SampleMessage(
        channel_kind=0,
        channel_name="dm",
        sender_name="Bob",
        sender_key_prefix=b"\x10\x20\x30\x40\x50\x60",
        sender_key_prefix_len=6,
        sender_timestamp=1710000001,
        text="Pong",
    )

    _assert_equal(request_fingerprint_for_message(message), 0x0E0E4311003CC988, "object request fingerprint")
    _assert_equal(request_token_for_message(message), 0xC988, "object request token")
    _assert_equal(format_request_token(request_token_for_message(message)), "c988", "object token text")
    _assert_equal(prepend_request_token(message, b"Pong"), b"[c988] Pong", "object token prefix")
    _assert_equal(
        response_fingerprint_for_message(message, "Pong", dm_channel_kind=0),
        0xC51D68AD4AAAF5D2,
        "object response fingerprint",
    )


def main() -> int:
    validate_dict_message()
    validate_object_message()
    print("meshcore_request_token validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())