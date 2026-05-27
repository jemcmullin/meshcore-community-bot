# MeshCore Request Token Notes

This folder contains a standalone Python implementation of the MeshCore request
fingerprint and response token behavior extracted from the patch series.

It also includes a response-coordination helper that mirrors the current
firmware delay and suppression logic.

## What the token is

The response prefix token is the low 16 bits of the request fingerprint.
The firmware renders it as four lowercase hexadecimal digits and prepends it
as `[abcd] ` — always exactly 7 bytes including the trailing space:

```text
[1a2b] Trace sent
```

## Required input shape

To reproduce the firmware result from a message, the caller must supply these
fields:

- `channel_kind`: integer enum value
- `channel_name`: bytes or string
- `sender_name`: bytes or string
- `sender_key_prefix`: bytes or string (raw key material, not hex)
- `sender_key_prefix_len`: integer
- `sender_timestamp`: integer (the timestamp embedded in the packet, not wall clock)
- `text`: bytes or string
- `text_len`: optional integer; if omitted, the whole `text` value is used
- `path_hash_count`: integer (number of hops, used for timing only)

The implementation accepts either a mapping (`dict`-like) or an object with
attributes of the same names.

## FNV-1a 64-bit hash algorithm

All fingerprints use FNV-1a 64-bit with these constants:

```
offset_basis = 1469598103934665603
prime        = 1099511628211
```

Each byte is fed as:

```
hash = ((hash XOR byte) * prime) mod 2^64
```

Start from `offset_basis`. Feed bytes in field order. The result is a
64-bit unsigned integer.

## Text normalization rules

`normalizeText()` in the firmware maps input bytes to a normalized output
by the following exact rules:

1. **NUL terminates.** Stop at the first `0x00` byte; do not include it.
2. **Control/space bytes are collapsed.** A byte is treated as whitespace if
   it equals `0x09` (tab), `0x0A` (LF), `0x0D` (CR), `0x20` (space), is less
   than `0x20`, or equals `0x7F` (DEL). Run any number of consecutive
   whitespace bytes into a single `0x20` space in the output.
3. **Leading whitespace is stripped.** A pending space is only flushed when a
   non-space byte follows _and_ at least one output byte has already been
   written.
4. **Trailing whitespace is stripped.** A pending space is never flushed at
   the end of input.
5. **Output is capped at 160 bytes.** If the output would reach 160 bytes
   from flushing a pending space, the pending space is written (reaching 160)
   but the following non-space byte is discarded. The loop continues to drain
   remaining input in case there is a NUL to honor.

The normalized output is then lowercased before hashing. Lowercasing is
applied byte-by-byte (`byte | 0x20` for ASCII alpha, i.e. Python `.lower()`
on the ASCII range).

## Request fingerprint field order

Feed fields to FNV-1a in this exact order:

1. `channel_kind` — 1 byte, the raw integer value
2. `channel_name` — read as a C string (stop at NUL), bounded at 24 bytes,
   strip a single leading `#`, lowercase, feed the resulting bytes (no NUL
   terminator fed into the hash)
3. `sender_key_prefix` — always exactly 6 bytes; truncate if longer, zero-pad
   if shorter
4. `sender_name` — read as a C string (stop at NUL), bounded at 32 bytes,
   lowercase, feed bytes (no NUL terminator)
5. `sender_timestamp` — 4 bytes, little-endian uint32
6. normalized text — apply `normalizeText()` with `input_len` as the bound,
   then lowercase, feed bytes

The `channel_name` bound is `BOT_MAX_CHANNEL_NAME_LEN + 1 = 24` to include
the NUL in the read window, then the NUL itself is not hashed.
The `sender_name` bound is `BOT_MAX_SENDER_NAME_LEN + 1 = 32` for the same
reason.

## Response fingerprint field order

The response fingerprint is a separate FNV-1a hash over:

1. `channel_kind` — 1 byte
2. `channel_name` — same bounded-C-string treatment as request fingerprint
3. **DM channels only:** `sender_key_prefix_len` as 1 byte, then the first
   `sender_key_prefix_len` bytes of `sender_key_prefix` (not padded to 6)
4. normalized response text — apply `normalizeText()` on the response string,
   then lowercase, feed bytes

`sender_name` and `sender_timestamp` are **not** included in the response
fingerprint. For non-DM channels the sender key is also excluded.

## Token format and parsing

- Token = `request_fingerprint & 0xFFFF`
- Formatted as four lowercase hex digits, zero-padded: `format(token, "04x")`
- Prefix = `[` + 4 hex digits + `] ` = exactly 7 ASCII bytes

Parsing rules for `[xxxx] ` (0-indexed):

- byte 0 must be `[` (0x5B)
- bytes 1–4 must each be a valid lowercase or uppercase hex digit (`0-9`, `a-f`,
  `A-F`)
- byte 5 must be `]` (0x5D)
- byte 6 must be ` ` (0x20)
- Input shorter than 7 bytes is rejected

## Response timing formulas

All values are in milliseconds.

```
delay = base
      + channel_bias(channel_kind)
      + hop_bias(channel_kind, hop_count)
      + queue_bias(queue_depth)
      + tie_break_bias(request_fingerprint, bot_identity_seed)
      + (jitter_seed % jitter_window)
```

| Component      | Formula                                              |
| -------------- | ---------------------------------------------------- |
| base           | 1500                                                 |
| jitter window  | 2200                                                 |
| channel bias   | 0 (DM), 200 (BOT), 400 (TESTING), 800 (other)        |
| hop bias       | `hop * 2000 + hop² * 400`, capped at 50000; 0 for DM |
| queue bias     | `queue_depth * 150`                                  |
| tie-break bias | see below                                            |

`response_due_at` = `(now_millis + delay) mod 2^32` (wraps as uint32).

### Tie-break bias formula

```
lo32   = request_fingerprint & 0xFFFFFFFF
hi32   = (request_fingerprint >> 32) & 0xFFFFFFFF
seed32 = bot_identity_seed & 0xFFFFFFFF

mixed  = (lo32 ^ hi32 ^ seed32) & 0xFFFFFFFF
mixed  = (mixed ^ (mixed >> 16)) & 0xFFFFFFFF
mixed  = (mixed * 0x7FEB352D) & 0xFFFFFFFF
mixed  = (mixed ^ (mixed >> 15)) & 0xFFFFFFFF
result = mixed % 900
```

Result is in the range `[0, 899]`.

## Response suppression rules

Token suppression: scan pending entries; for each entry where
`(entry.request_fingerprint & 0xFFFF) == request_token`, mark it suppressed.
Skip entries whose `request_fingerprint == 0`. Skip entirely if
`request_token == 0`.

Fingerprint suppression: scan pending entries; for each entry where
`entry.response_fingerprint == response_fingerprint`, mark it suppressed.
Skip entirely if `response_fingerprint == 0`.

Recent-response check: a response with the same fingerprint observed within
the last `30000ms` short-circuits sending. Use `(now - observed) < ttl` (not
`<=`).

Firmware suppression order:

1. Parse an incoming peer token prefix like `[6617] `.
2. Suppress pending entries by request token.
3. Compute the response fingerprint; suppress pending entries by fingerprint.
4. Check the recent-response table separately before enqueuing or sending.

## Concrete test vectors

These vectors are fixed inputs with known-good expected outputs. Any correct
reimplementation must produce the same results.

### Non-DM request fingerprint (channel_kind = 1 = BOT)

```
channel_kind        = 1
channel_name        = "#bot"     (# is stripped → "bot")
sender_key_prefix   = 01 02 03 04 05 06  (6 bytes)
sender_key_prefix_len = 6
sender_name         = "Alice"
sender_timestamp    = 1710000000  (0x65E5C380, LE bytes: 80 C3 E5 65)
text                = "  Trace\nSent  "
```

Text normalization of `"  Trace\nSent  "`:

- leading spaces stripped, `\n` collapsed, trailing spaces stripped
- result: `"Trace Sent"` → lowercased to `"trace sent"`

Expected outputs:

| Value                          | Expected              |
| ------------------------------ | --------------------- |
| request fingerprint            | `0x6714C4F4C3006617`  |
| request token                  | `0x6617`              |
| token string                   | `"6617"`              |
| prefixed response "Trace sent" | `"[6617] Trace sent"` |
| response fingerprint (non-DM)  | `0xC6D333E42C1FD502`  |

### DM request fingerprint (channel_kind = 0 = DM)

```
channel_kind        = 0
channel_name        = "dm"
sender_key_prefix   = 10 20 30 40 50 60  (6 bytes)
sender_key_prefix_len = 6
sender_name         = "Bob"
sender_timestamp    = 1710000001
text                = "Pong"
```

Expected outputs:

| Value                                   | Expected             |
| --------------------------------------- | -------------------- |
| request fingerprint                     | `0x0E0E4311003CC988` |
| request token                           | `0xC988`             |
| token string                            | `"c988"`             |
| prefixed response bytes b"Pong"         | `b"[c988] Pong"`     |
| response fingerprint (DM, includes key) | `0xC51D68AD4AAAF5D2` |

### Timing test vectors

```
channel_kind      = 1 (BOT)
channel_name      = "#bot"
sender_name       = "Alice"
sender_key_prefix = 01 02 03 04 05 06
sender_timestamp  = 1710000000
text              = "Trace now"
path_hash_count   = 3
bot_identity_seed = 0x12345678
queue_depth       = 4
jitter_seed       = 9876
```

| Component                                 | Expected                |
| ----------------------------------------- | ----------------------- |
| `channel_delay_bias(1)`                   | 200                     |
| `hop_delay_bias(1, 3)`                    | 9600 (= 3×2000 + 9×400) |
| `queue_delay_bias(4)`                     | 600                     |
| `tie_break_bias(fingerprint, 0x12345678)` | 530                     |
| `jitter` (`9876 % 2200`)                  | 1076                    |
| `response_delay_millis(...)`              | 13506                   |
| `response_due_at(1000, ...)`              | 14506                   |

## Important compatibility caveats

- The fingerprint is byte-sensitive. If one program uses UTF-8 strings and
  another uses raw bytes, the results match only if the bytes are identical.
- `sender_timestamp` is the timestamp embedded in the packet, not wall clock
  time. Two otherwise identical messages with different timestamps will not
  produce the same token.
- `channel_kind` integer values must match the firmware enum:
  `DM = 0`, `BOT = 1`, `TESTING = 2`.
- For response fingerprints, sender key bytes are included only for DM
  (`channel_kind == 0` by default). The count used is `sender_key_prefix_len`,
  not the fixed 6-byte padding used in request fingerprints.
- `response_due_at` wraps at `2^32`. Comparisons must use unsigned 32-bit
  arithmetic.
- The standalone module returns `bytes` for prefixing. Convert to text only if
  the transport is known to be ASCII-safe.

## Functions provided

- `request_fingerprint_for_message(message)`
- `request_token_for_message(message)`
- `format_request_token(token)`
- `prepend_request_token(message, response_text)` → `bytes`
- `prepend_request_token_text(message, response_text)` → `str`
- `response_fingerprint_for_message(message, response_text, dm_channel_kind=0)`
- `parse_request_token_prefix(text)` → `(token, prefix_len)` or `None`

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

The folder includes validator scripts:

- `validate_meshcore_request_token.py`
- `validate_meshcore_response_coordinator.py`

Each runs fixed assertions against all of the test vectors above and exits
nonzero on any failure. Run them directly:

```bash
python validate_meshcore_request_token.py
python validate_meshcore_response_coordinator.py
```

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

Preserve byte-level rules before the convenience API. The request token is
not an independent hash — it is the low 16 bits of the request fingerprint.

Do not simplify away:

- the leading `#` removal from channel names
- the per-field lowercasing (channel name, sender name, normalized text)
- the 32-bit little-endian timestamp hashing
- the 6-byte zero-padded sender key prefix in request fingerprints
- the variable-length (non-padded) sender key in DM response fingerprints
- the text normalization cap at 160 bytes, especially the pending-space edge
  case at the boundary

For the timing path, the compatibility-critical values are:

- base: 1500ms, jitter window: 2200ms
- queue bias: 150ms × depth
- channel bias: 0 / 200 / 400 / 800ms
- hop bias: `hop × 2000 + hop² × 400`, cap 50000ms (quadratic, not linear)
- tie-break: XOR-fold fingerprint with seed, multiply by 0x7FEB352D, mod 900
- pending TTL: 95000ms, recent TTL: 30000ms
