# MeshCore Request Token Notes

This folder contains a standalone Python implementation of the MeshCore request
fingerprint and response token behavior extracted from the patch series.

It also now includes a separate response-coordination helper that mirrors the
current firmware delay and suppression logic.

## What the token is

The response prefix token is the low 16 bits of the request fingerprint.
The firmware renders it as four lowercase hexadecimal digits and prepends it
as:

```text
[abcd]
```

Example output:

```text
[1a2b] Trace sent
```

## Required input shape

To reproduce the firmware result from a message, the caller must supply these
message fields:

- `channel_kind`: integer enum value
- `channel_name`: bytes or string
- `sender_name`: bytes or string
- `sender_key_prefix`: bytes or string
- `sender_key_prefix_len`: integer
- `sender_timestamp`: integer
- `text`: bytes or string
- `text_len`: optional integer; if omitted, the whole `text` value is used

The implementation accepts either a mapping (`dict`-like) or an object with
attributes of the same names.

## Firmware-matching rules that matter

These details must be preserved if another program reimplements the logic.

- FNV-1a 64-bit is used with offset basis `1469598103934665603` and prime
  `1099511628211`.
- The request fingerprint hashes the message in this order:
  1. `channel_kind` as one byte
  2. `channel_name` as a bounded C string, with a leading `#` removed
  3. `sender_key_prefix` as exactly 6 bytes, padded with zeroes if shorter
  4. `sender_name` as a bounded C string, lowercased
  5. `sender_timestamp` as little-endian uint32
  6. `text` after firmware normalization, lowercased
- Text normalization collapses whitespace and control bytes into single spaces,
  trims leading/trailing whitespace, and stops at the first NUL byte.
- The response token is `request_fingerprint & 0xFFFF`.
- The token is formatted with lowercase hex, zero padded to 4 digits.
- The final prefix is ASCII and always exactly 7 bytes: `[` + 4 hex digits +
  `] `.

## Important compatibility caveats

- The fingerprint is byte-sensitive. If one program uses UTF-8 strings and
  another uses raw bytes, the results match only if the bytes are identical.
- `sender_timestamp` is part of the request fingerprint. Two otherwise identical
  messages with different timestamps will not produce the same token.
- `channel_kind` values must match the firmware enum values used in the other
  program.
- For response fingerprints, the firmware includes `sender_key_prefix_len` and
  the first `sender_key_prefix_len` bytes of `sender_key_prefix` only when the
  message is in the DM channel kind.
- The standalone module returns bytes for prefixing because that is the safest
  transport form for another program. Convert to text only if your transport is
  known to be ASCII-safe.

## Functions provided

- `request_fingerprint_for_message(message)`
- `request_token_for_message(message)`
- `format_request_token(token)`
- `prepend_request_token(message, response_text)`
- `prepend_request_token_text(message, response_text)`
- `response_fingerprint_for_message(message, response_text, dm_channel_kind=0)`
- `parse_request_token_prefix(text)`

The companion coordinator module provides:

- `channel_delay_bias(channel_kind)`
- `hop_delay_bias(channel_kind, path_hash_count, hop_step_millis=...)`
- `queue_delay_bias(queue_depth)`
- `tie_break_bias(request_fingerprint, bot_identity_seed)`
- `response_delay_millis(message, request_fingerprint, bot_identity_seed, queue_depth, jitter_seed, ...)`
- `response_due_at(now_millis, message, request_fingerprint, bot_identity_seed, queue_depth, jitter_seed, ...)`
- `suppress_by_response_fingerprint(pending, response_fingerprint)`
- `suppress_by_request_token(pending, request_token)`
- `recently_sent(recent, response_fingerprint, now_millis, ttl_millis=...)`
- `record_recent(recent, response_fingerprint, now_millis)`

## Validation helper

The folder also includes a standalone validator script:

- `validate_meshcore_request_token.py`
- `validate_meshcore_response_coordinator.py`

It runs fixed assertions against:

- a dict-based message input
- an object-based message input
- request fingerprint generation
- request token formatting
- response prefix generation
- DM response fingerprint behavior
- current firmware delay and suppression rules

An AI agent can run it directly with:

```bash
python validate_meshcore_request_token.py
python validate_meshcore_response_coordinator.py
```

The script exits nonzero if any compatibility check fails.

## Minimal usage example

```python
from meshcore_request_token import prepend_request_token_text

message = {
    "channel_kind": 0,
    "channel_name": "#bot",
    "sender_name": "alice",
    "sender_key_prefix": b"\x01\x02\x03\x04\x05\x06",
    "sender_key_prefix_len": 6,
    "sender_timestamp": 1710000000,
    "text": "!trace now",
}

print(prepend_request_token_text(message, "Trace sent"))
```

## Integration note for another AI or agent

If an AI is asked to reimplement this behavior elsewhere, it should preserve the
byte-level rules first and the convenience API second. The request token is not
an independent hash; it is only a view into the request fingerprint. If the new
program already computes the same request fingerprint for its own message model,
it should derive the token by masking with `0xFFFF` and format it as
`[xxxx] `.

Do not simplify away:

- the leading `#` removal from channel names
- the per-field lowercasing rules
- the 32-bit little-endian timestamp hashing
- the 6-byte sender key prefix padding
- the text normalization rules

For the timing path, preserve these exact firmware values:

- response base delay: `1500ms`
- jitter window: `2200ms`
- queue delay: `150ms * queue_depth`
- channel bias: `0/200/400/800ms` for DM/BOT/TESTING/other
- hop bias: `hop * 2000 + hop^2 * 400`, capped at `50000ms`
- pending TTL: `95000ms`
- recent TTL: `30000ms`

The response suppression path should match the firmware order:

1. Parse an incoming peer request token prefix like `[6617] `.
2. Suppress any matching pending response whose request fingerprint low 16 bits match that token.
3. Compute the response fingerprint and suppress any matching pending response by fingerprint.
4. Keep the recent-response check separate so duplicated echoes still short-circuit.

Those are the compatibility-critical parts.
