from community.meshcore_request_token import request_token_for_message, format_request_token


import pytest


@pytest.mark.parametrize(
    "msg, expected_hex",
    [
        (
            {
                "channel_kind": 2,  # #bot
                "channel_name": "#bot",
                "sender_name": "🐻MEGABEAR 730F",
                "sender_key_prefix": b"\x00\x00\x00\x00\x00\x00",
                "sender_key_prefix_len": 0,
                "sender_timestamp": 1779989614,
                "text": "T",
                "text_len": 1,
                "path_hash_count": 4,
            },
            "4ec4",
        ),
        (
            {
                "channel_kind": 2,
                "channel_name": "#bot",
                "sender_name": "ZWatt01",
                "sender_key_prefix": b"\x00\x00\x00\x00\x00\x00",
                "sender_key_prefix_len": 0,
                "sender_timestamp": 1779991334,
                "text": "T",
                "text_len": 1,
                "path_hash_count": 3,
            },
            "a86f",
        ),
        (
            {
                "channel_kind": 2,
                "channel_name": "#bot",
                "sender_name": "Nix Mobile 3",
                "sender_key_prefix": b"\x00\x00\x00\x00\x00\x00",
                "sender_key_prefix_len": 0,
                "sender_timestamp": 1780000516,
                "text": "T",
                "text_len": 1,
                "path_hash_count": 5,
            },
            "b2d6",
        ),
    ],
)
def test_real_messages_match_firmware_token(msg, expected_hex):
    token = request_token_for_message(msg) & 0xFFFF
    assert format_request_token(token) == expected_hex
