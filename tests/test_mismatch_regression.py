import pytest

from community.meshcore_request_token import (
    request_fingerprint_for_message,
    request_token_for_message,
)


CASES = [
    (
        {
            "channel_kind": 2,
            "channel_name": "#bot",
            "sender_name": "🐻MEGABEAR 730F",
            "sender_key_prefix": b"",
            "sender_key_prefix_len": 0,
            "sender_timestamp": 1779989614,
            "text": "T",
            "text_len": 1,
        },
        0x84b59642e424e570,
        0xe570,
    ),
    (
        {
            "channel_kind": 2,
            "channel_name": "#bot",
            "sender_name": "ZWatt01",
            "sender_key_prefix": b"",
            "sender_key_prefix_len": 0,
            "sender_timestamp": 1779991334,
            "text": "T",
            "text_len": 1,
        },
        0x117f2fb8c6589958,
        0x9958,
    ),
    (
        {
            "channel_kind": 2,
            "channel_name": "#bot",
            "sender_name": "Nix Mobile 3",
            "sender_key_prefix": b"",
            "sender_key_prefix_len": 0,
            "sender_timestamp": 1780000516,
            "text": "T",
            "text_len": 1,
        },
        0x1cd097bc7dcbe62f,
        0xe62f,
    ),
]


@pytest.mark.parametrize("msg,expected_fp,expected_token", CASES)
def test_regression_real_tokens(msg, expected_fp, expected_token):
    fp = request_fingerprint_for_message(msg)
    token = request_token_for_message(msg)
    assert fp == expected_fp, f"fingerprint mismatch: got 0x{fp:016x} expected 0x{expected_fp:016x}"
    assert token == expected_token, f"token mismatch: got 0x{token:04x} expected 0x{expected_token:04x}"
