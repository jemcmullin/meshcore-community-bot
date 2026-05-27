#!/usr/bin/env python3
"""Live token comparison helper for firmware/Python parity debugging.

Usage examples:

1) Known-good vector
python live_token_check.py --message '{
  "channel_kind": 1,
  "channel_name": "#bot",
  "sender_key_prefix": "010203040506",
  "sender_key_prefix_len": 6,
  "sender_name": "Alice",
  "sender_timestamp": 1710000000,
  "text": "  Trace\\nSent  "
}' --observed 6617

2) Read message JSON from stdin
cat message.json | python live_token_check.py --observed "[6617]"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from meshcore_request_token import format_request_token, request_fingerprint_for_message, request_token_for_message

REQUIRED_FIELDS = (
    "channel_kind",
    "channel_name",
    "sender_key_prefix",
    "sender_key_prefix_len",
    "sender_name",
    "sender_timestamp",
    "text",
)


def _parse_observed_token(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != 4:
        raise ValueError("observed token must be 4 hex chars (example: 6617)")
    return int(text, 16)


def _load_message(message_arg: str | None) -> dict[str, Any]:
    raw = message_arg
    if not raw:
        raw = sys.stdin.read()
    if not raw or not raw.strip():
        raise ValueError("no JSON message provided; use --message or stdin")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("message JSON must be an object")
    return data


def _normalize_message_for_hashing(message: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(message)
    key_prefix = normalized.get("sender_key_prefix")
    if isinstance(key_prefix, str):
        candidate = key_prefix.strip().lower().removeprefix("0x")
        if candidate and len(candidate) % 2 == 0:
            try:
                normalized["sender_key_prefix"] = bytes.fromhex(candidate)
            except ValueError:
                pass
    return normalized


def _field_issues(message: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    missing = [k for k in REQUIRED_FIELDS if k not in message]
    if missing:
        issues.append(f"missing required fields: {', '.join(missing)}")

    channel_kind = message.get("channel_kind")
    if not isinstance(channel_kind, int):
        issues.append("channel_kind should be an integer (0 DM, 1 BOT, 2 TESTING)")

    sender_ts = message.get("sender_timestamp")
    if not isinstance(sender_ts, int):
        issues.append("sender_timestamp should be the exact packet int timestamp")
    else:
        now = int(time.time())
        if abs(now - sender_ts) < 3:
            issues.append("sender_timestamp is near wall-clock now; verify this came from the packet, not local time")

    key_prefix = message.get("sender_key_prefix")
    if isinstance(key_prefix, str):
        candidate = key_prefix.strip().lower().removeprefix("0x")
        if not candidate:
            issues.append("sender_key_prefix is empty")
        elif len(candidate) % 2 != 0:
            issues.append("sender_key_prefix string has odd hex length; expected raw bytes or even-length hex")

    key_len = message.get("sender_key_prefix_len")
    if not isinstance(key_len, int):
        issues.append("sender_key_prefix_len should be an integer")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare computed request token with observed firmware token")
    parser.add_argument("--message", help="JSON object containing request-fingerprint input fields")
    parser.add_argument("--observed", help="Observed token (examples: 6617, 0x6617, [6617])")
    args = parser.parse_args()

    try:
        message = _load_message(args.message)
        observed = _parse_observed_token(args.observed)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    message_for_hashing = _normalize_message_for_hashing(message)

    issues = _field_issues(message)
    fingerprint = request_fingerprint_for_message(message_for_hashing)
    token_int = request_token_for_message(message_for_hashing)
    token_hex = format_request_token(token_int)

    print(f"request_fingerprint: 0x{fingerprint:016x}")
    print(f"computed_token:      {token_hex}")

    if observed is not None:
        observed_hex = format_request_token(observed)
        print(f"observed_token:      {observed_hex}")
        if observed == token_int:
            print("match:               yes")
        else:
            print("match:               no")
            print("likely_causes:")
            print("- sender_timestamp differs from packet timestamp")
            print("- sender_key_prefix differs from sender public-key first 6 bytes")
            print("- channel_kind mapping differs from firmware enum")
            print("- text differs from raw packet text used by firmware")

    if issues:
        print("field_warnings:")
        for issue in issues:
            print(f"- {issue}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
