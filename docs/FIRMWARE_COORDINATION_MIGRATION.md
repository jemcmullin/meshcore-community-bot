# Firmware-Native Coordination Migration Plan

Replace the HTTP coordinator (`coordinator_client.py`) with fully decentralised,
firmware-compatible response coordination. Each bot independently computes a
deterministic delay from the inbound message fingerprint, waits, and suppresses
itself if it hears another bot respond first. No central server.

---

## Background

### Current system (coordinator-based)

1. Bot receives a channel message.
2. `MessageInterceptor` calls `POST /api/v1/coordination/should-respond` with signal
   data (SNR, RSSI, hops, path). 300 ms bidding window at the coordinator.
3. Coordinator scores all competing bids and returns `should_respond + response_delay_ms`.
4. Bot waits `response_delay_ms` then sends, or silences itself.
5. Fallback (coordinator unreachable): `200 ms + hops × 600 ms + jitter(0–150 ms)`.

### New system (firmware-native)

1. Bot receives a channel message.
2. Compute a 64-bit FNV-1a fingerprint of the message (`request_fingerprint`).
3. Derive the 16-bit request token (`request_fingerprint & 0xFFFF`).
4. Compute a total delay using the firmware formula (see constants below).
5. Add a `PendingBotResponse` entry and sleep for that delay.
6. While sleeping: observe ALL incoming channel messages. If any peer bot has
   already responded with `[xxxx] `, check whether `xxxx` matches the token.
   If so, mark the pending entry suppressed.
7. After sleep: if suppressed → silent return. If not suppressed → prepend
   `[xxxx] ` to the response content and send.
8. Record the response fingerprint in the `recently_sent` list (30 s TTL).
   Future duplicates of the same response are short-circuited immediately.

---

## Firmware constants (must not be changed)

```python
BOT_RESPONSE_DELAY_BASE_MILLIS     = 1500
BOT_RESPONSE_DELAY_JITTER_MILLIS   = 2200
BOT_RESPONSE_PENDING_TTL_MILLIS    = 95000
BOT_RESPONSE_RECENT_TTL_MILLIS     = 30000
BOT_HOP_STEP_MILLIS_DEFAULT        = 2000
BOT_HOP_GROW_MILLIS                = 400
BOT_HOP_BIAS_MAX_MILLIS            = 50000
QUEUE_DELAY_PER_ENTRY_MILLIS       = 150

# Channel kind enum (firmware values — must match exactly)
BOT_CHANNEL_DM      = 0   # bias   0 ms
BOT_CHANNEL_BOT     = 1   # bias 200 ms
BOT_CHANNEL_TESTING = 2   # bias 400 ms
# any other channel    3  # bias 800 ms

# Total delay formula:
# base + channel_bias + hop_bias + queue_bias + tie_break_bias + jitter
# hop_bias  = min(hop * 2000 + hop² * 400, 50000)   (0 for DMs)
# tie_break = (fingerprint mixing) % 900
# jitter    = random_seed % 2200
```

---

## Source modules (do not modify)

Two files from `community/standalone_request_token/` are the reference
implementation. They are validated by their own test scripts.

- `meshcore_request_token.py` — FNV-1a fingerprint, token formatting,
  `prepend_request_token`, `parse_request_token_prefix`, `response_fingerprint_for_message`
- `meshcore_response_coordinator.py` — delay formula, `PendingBotResponse`,
  `RecentBotResponse`, suppression helpers, `recently_sent`, `record_recent`

---

## Files to add

### `community/meshcore_request_token.py`

### `community/meshcore_response_coordinator.py`

**Action:** Start from `community/standalone_request_token/` and preserve
firmware-exact behavior. Byte-for-byte identity is not required as long as the
result still matches firmware bot fingerprinting and timing semantics exactly.
All other community code imports from these two copies.

---

### `community/firmware_coordinator.py` _(new file)_

Owns the bot-scoped coordination state.

```python
class FirmwareCoordinator:
    bot_identity_seed: int          # set after radio connects
    pending: list[PendingBotResponse]
    recent: list[RecentBotResponse]
```

#### `set_identity_seed(public_key_hex: str) -> None`

Decode `bytes.fromhex(public_key_hex)` and interpret the first 4 bytes as a
big-endian uint32. This is the `bot_identity_seed` used in `tie_break_bias()`.
Called once from `CommunityBot` after `connect()` returns.

```python
raw = bytes.fromhex(public_key_hex)
self.bot_identity_seed = int.from_bytes(raw[:4], "big")
```

If the key is shorter than 4 bytes (should not happen) or invalid hex, default
to `0` and log a warning.

#### `observe_peer_message(text: str) -> None`

Called on every incoming channel message BEFORE command routing.
Parse `text` for a `[xxxx] ` prefix using `parse_request_token_prefix()`.
If found:

1. Call `suppress_by_request_token(self.pending, token)`.
2. Compute `response_fingerprint_for_message` is NOT available here (we don't
   have the response text from a peer). Skip step 2 — token suppression alone
   is sufficient for the peer-heard path.

#### `schedule_response(message_fp_input: dict, response_text: str, now_ms: int) -> tuple[PendingBotResponse, int] | None`

- `message_fp_input` is the normalised dict produced by `_to_fingerprint_input()`
  (see MeshMessage mapping section).
- Compute `request_fp = request_fingerprint_for_message(message_fp_input)`.
- Compute `response_fp = response_fingerprint_for_message(message_fp_input, response_text)` using the unprefixed response text.
- Check `recently_sent(self.recent, response_fp, now_ms)` → if True return `None`
  (caller silences without sending).
- Build `PendingBotResponse(request_fingerprint=request_fp, response_fingerprint=response_fp)`.
- Compute `delay_ms = response_delay_millis(message_fp_input, request_fp, self.bot_identity_seed, self.queue_depth(), jitter_seed)`.
  - `jitter_seed` = `os.urandom(4)` interpreted as uint32:
    `int.from_bytes(os.urandom(4), "big")`
- Set `entry.due_at_millis = (now_ms + delay_ms) & 0xFFFFFFFF`.
- Set `entry.expires_at_millis = (now_ms + BOT_RESPONSE_PENDING_TTL_MILLIS) & 0xFFFFFFFF`.
- Append entry to `self.pending`.
- Return `(entry, delay_ms)`.

#### `mark_sent(entry: PendingBotResponse, now_ms: int) -> None`

Set `entry.sent = True`.
Call `record_recent(self.recent, entry.response_fingerprint, now_ms)`.

#### `queue_depth() -> int`

`len([e for e in self.pending if e.active and not e.sent and not e.suppressed])`

#### `cleanup(now_ms: int) -> None`

Remove expired entries from `self.pending` (where `expires_at_millis` has passed).
Remove old entries from `self.recent` beyond `BOT_RESPONSE_RECENT_TTL_MILLIS`.
Call this periodically — e.g. once per minute — from the main loop or from
`_wrapped_process_message`.

---

## Files to rewrite

### `community/message_interceptor.py`

This is the core of the migration. Remove all `CoordinatorClient` and
`ResponseTiming` logic. Retain: Discord forwarding, `current_message_var`,
`coordinated_var`, the double-coordination guard.

#### New `__init__` signature

```python
def __init__(self, bot, firmware_coordinator: FirmwareCoordinator, reporter=None):
```

Remove `coordinator` and `timing` parameters.

#### New patch points (3 total, up from 2)

**Patch 1 — `message_handler.handle_channel_message`**

This is the new early intercept. It must fire BEFORE `process_message` so the
raw payload text is captured before the colon-split strips the sender prefix.

```python
self._original_handle_channel_message = bot.message_handler.handle_channel_message
bot.message_handler.handle_channel_message = self._wrapped_handle_channel_message
```

```python
async def _wrapped_handle_channel_message(self, event, *args, **kwargs):
    # Capture raw text BEFORE the colon-split in the original handler
    try:
        payload = event.payload if hasattr(event, "payload") else {}
        raw_text = (payload or {}).get("text", "")
    except Exception:
        raw_text = ""
    tok = raw_text_var.set(raw_text)
    try:
        return await self._original_handle_channel_message(event, *args, **kwargs)
    finally:
        raw_text_var.reset(tok)
```

`raw_text_var` is a module-level `ContextVar[str]` with default `""`.

**Why this patch?** The fingerprint `text` field must be the raw packet text
(e.g. `"HOWL: !ping"`) not the processed content (e.g. `"!ping"`). The
message handler splits on `:` at line 2078 before constructing `MeshMessage`.
By the time `process_message` is called, `message.content` is already stripped.
The raw text is only available from `event.payload["text"]` at this earlier
stage.

**Patch 2 — `message_handler.process_message`**

Same as current: capture `current_message_var`, run peer-suppression observation,
delegate to original.

```python
async def _wrapped_process_message(self, message, *args, **kwargs):
    tok_msg = current_message_var.set(message)
    tok_coord = coordinated_var.set(False)
    try:
        await self._discord_forward_incoming(message)
        # Observe incoming message for peer suppression (channel only)
        if not message.is_dm:
            content = message.content or ""
            self.firmware_coordinator.observe_peer_message(content)
        return await self._original_process_message(message, *args, **kwargs)
    finally:
        coordinated_var.reset(tok_coord)
        current_message_var.reset(tok_msg)
```

**Note:** `observe_peer_message` is called with `message.content` (the stripped
text). This is intentional: peer bot responses begin with `[xxxx] ` and the
sender prefix is stripped by the message handler, so the stripped content IS
what starts with `[xxxx] `. When a peer bot sends `"[1a2b] Pong!"`, the raw
text is `"BotName: [1a2b] Pong!"`. After colon-split, `message.content` is
`"[1a2b] Pong!"`. The prefix parser correctly finds `[1a2b]` in the stripped
content.

**Patch 3 — `command_manager.send_response`** and `command_manager.send_channel_message`

Same patch targets as current.

---

#### `_coordinated_send_response(self, message, content: str, *args, **kwargs) -> bool`

```
1. If message.is_dm:
       result = await self._original_send_response(message, content, *args, **kwargs)
       await self._discord_forward_response(message, content)
       return result

2. Build fingerprint input dict via _to_fingerprint_input(message)

3. now_ms = int(time.time() * 1000) & 0xFFFFFFFF

4. scheduled = self.firmware_coordinator.schedule_response(fp_input, content, now_ms)
   If scheduled is None:   # recently_sent hit
       return False         # silent dedup

5. entry, delay_ms = scheduled
   coordinated_var.set(True)

6. await asyncio.sleep(delay_ms / 1000.0)

7. If entry.suppressed:
       logger.info(f"[FW] Suppressed response to {message.channel}")
       return False

8. prefixed_content = prepend_request_token_text(fp_input, content)
     result = await self._original_send_response(message, prefixed_content, *args, **kwargs)
   if result:
       now_ms2 = int(time.time() * 1000) & 0xFFFFFFFF
       self.firmware_coordinator.mark_sent(entry, now_ms2)
       await self._discord_forward_response(message, prefixed_content)
   return result
```

#### Intentional multi-chunk behavior

Multi-chunk responses keep a per-token outcome cache in `MessageInterceptor`.

- After the first chunk wins coordination for a given request token, later chunks
  for that same token are sent immediately without re-running the delay race.
- If the first chunk is suppressed, later chunks for that token are also dropped.
- This is an intentional extension beyond the minimal single-response flow so
  multi-packet bot replies stay internally consistent.
- Only the first chunk participates in `schedule_response()` and `mark_sent()`.
  The response fingerprint for that first chunk must still be computed from the
  unprefixed response text so firmware-compatible dedup remains exact.

#### `_coordinated_send_channel_message(self, channel, content, *args, **kwargs) -> bool`

Mirrors `_coordinated_send_response` but uses `current_message_var` to get the
inbound message context (same as current implementation). The double-coordination
guard (`coordinated_var`) prevents keyword triggers that also call `send_response`
from being coordinated twice.

```
1. previously_coordinated = coordinated_var.get()
2. If not previously_coordinated:
       try: message = current_message_var.get()
       except LookupError: send immediately (no context)
       run the same flow as _coordinated_send_response steps 2–8 but
       calling self._original_send_channel_message(channel, prefixed_content, ...) at step 8
3. If previously_coordinated:
       result = await self._original_send_channel_message(channel, content, *args, **kwargs)
       return result
```

---

### `community/response_timing.py`

Delete entirely. Timing is now fully encapsulated in `firmware_coordinator.py`
via `response_delay_millis()`. Remove the import from `community_core.py`.

---

### `community/config.py`

Replace `CoordinatorConfig` with a smaller `CommunityConfig`:

```python
@dataclass
class CommunityConfig:
    mesh_region: str = ""

    @classmethod
    def from_env_and_config(cls, config) -> "CommunityConfig":
        return cls(
            mesh_region=os.environ.get(
                "MESH_REGION",
                config.get("Community", "mesh_region", fallback=""),
            ),
        )
```

Remove all `COORDINATOR_*` fields.

---

### `community/community_core.py`

Remove:

- `from .config import CoordinatorConfig`
- `from .coordinator_client import CoordinatorClient`
- `from .response_timing import ResponseTiming`
- `from .packet_reporter import PacketReporter`
- All `self.coordinator`, `self.coordinator_config`, `self.response_timing`,
  `self.packet_reporter` initialization.
- `_register_with_coordinator()` method.
- `_start_coordinator_tasks()` method and `self._coordinator_tasks`.
- Heartbeat loop.

Add:

- `from .config import CommunityConfig`
- `from .firmware_coordinator import FirmwareCoordinator`
- `self.community_config = CommunityConfig.from_env_and_config(self.config)`
- `self.firmware_coordinator = FirmwareCoordinator()`

Change `MessageInterceptor` init:

```python
self.message_interceptor = MessageInterceptor(
    bot=self,
    firmware_coordinator=self.firmware_coordinator,
)
```

Change `start()`: after `await super().start()` returns (radio is connected),
extract the public key and seed the coordinator:

```python
async def start(self):
    await super().start()
    # Seed bot identity from radio public key
    if self.meshcore and hasattr(self.meshcore, "self_info"):
        try:
            info = self.meshcore.self_info
            pk = (info.get("public_key", "") if isinstance(info, dict)
                  else getattr(info, "public_key", "")) or ""
            if pk:
                self.firmware_coordinator.set_identity_seed(pk)
        except Exception as e:
            logger.warning(f"Could not set firmware identity seed: {e}")
```

**Gotcha:** `super().start()` runs the event loop and connects. The identity
seed call must come AFTER `connect()` inside `start()` completes. Check whether
`super().start()` ever returns or whether it blocks indefinitely — if it does,
the seed call must be placed inside the `connect()` flow instead, or hooked via
the existing `setup_message_handlers` path.

Actually, a safer placement: override `connect()` and call
`set_identity_seed` after `await super().connect()` returns `True`.

```python
async def connect(self) -> bool:
    result = await super().connect()
    if result and self.meshcore and hasattr(self.meshcore, "self_info"):
        try:
            info = self.meshcore.self_info
            pk = (info.get("public_key", "") if isinstance(info, dict)
                  else getattr(info, "public_key", "")) or ""
            if pk:
                self.firmware_coordinator.set_identity_seed(pk)
                logger.info(f"Firmware identity seed set from pubkey {pk[:8]}...")
        except Exception as e:
            logger.warning(f"Could not set firmware identity seed: {e}")
    return result
```

Change `stop()`: remove coordinator task cancellation and `coordinator.close()`.
Keep Discord close. Keep `message_interceptor.restore()` if it exists — the
interceptor now has 3 patch points and must restore all 3.

---

## Files to delete

- `community/coordinator_client.py`
- `community/packet_reporter.py`
- `community/response_timing.py`

---

## Files with smaller updates

### `community/commands/botstatus_command.py`

New output format:

```
Mode: firmware-native
Pending: N  Recent: M
Seed: xxxx  Uptime: Hh Mm
Base: 1500ms  Jitter: 2200ms
```

Access `self.bot.firmware_coordinator` instead of `self.bot.coordinator`.

```python
fc = getattr(self.bot, "firmware_coordinator", None)
if not fc:
    await self.send_response(message, "Firmware coordinator not initialised")
    return True

seed_hex = f"{fc.bot_identity_seed:08x}"[-4:]  # last 4 hex of seed
pending = fc.queue_depth()
recent = len(fc.recent)
uptime = int(time.time() - self.bot.start_time)
hours, mins = uptime // 3600, (uptime % 3600) // 60

lines = [
    "Mode: firmware-native",
    f"Pending: {pending}  Recent: {recent}",
    f"Seed: ...{seed_hex}  Uptime: {hours}h {mins}m",
    f"Base: 1500ms  Jitter: 2200ms",
]
await self.send_response(message, "\n".join(lines))
```

---

### `community/web_viewer_packet_stream.py`

Replace `publish_web_viewer_coordination_event` with a firmware-coordination
variant. Remove `winner_name`, `winner_score`, `reason`, `delay_ms` parameters
(coordinator-specific). Add `stage` values: `fw_pending`, `fw_suppressed`,
`fw_sent`. Keep the DM event function unchanged.

New signature:

```python
async def publish_web_viewer_fw_event(
    bot,
    message,
    stage: str,           # "fw_pending" | "fw_suppressed" | "fw_sent"
    delay_ms: int = 0,
    command: str = "",
    token_hex: str = "",
) -> None:
```

Old callers in `message_interceptor.py` must be updated to use the new function
or removed if the information is no longer meaningful.

---

### `community/web_viewer_patch.py`

Search for any references to `CoordinatorClient`, `coordinator`, or
`PacketReporter` and remove them. This file wires the community web page into
the submodule web viewer; it should not need significant changes otherwise.

---

### `community/web_viewer_community_page.py`

Remove coordinator-specific dashboard widgets (bid results, winner scores,
coordinator URL/status). Add firmware coordination widgets based on firmware
event stream data. Event-derived aggregates are acceptable for now; direct
reads from `bot.firmware_coordinator` are optional rather than required.

---

## MeshMessage → fingerprint input mapping

**Critical:** The fingerprint `text` field must be the raw packet text
(`raw_text_var.get()`), NOT `message.content`.

The raw text is captured by the `handle_channel_message` patch and stored in
`raw_text_var` (a `contextvars.ContextVar`). By the time `send_response` is
called, the context is still active and `raw_text_var.get()` returns the correct
value.

```python
def _to_fingerprint_input(message: MeshMessage) -> dict:
    # Decode sender public key to bytes (always 64-char hex, per design decision)
    try:
        pubkey_bytes = bytes.fromhex(message.sender_pubkey or "")
    except ValueError:
        pubkey_bytes = b""
    key_prefix = pubkey_bytes[:6].ljust(6, b"\x00")

    # channel_kind mapping (firmware enum convention)
    if message.is_dm:
        channel_kind = 0   # BOT_CHANNEL_DM
    elif message.channel == "#bot":
        channel_kind = 1   # BOT_CHANNEL_BOT
    elif message.channel == "#testing":
        channel_kind = 2   # BOT_CHANNEL_TESTING
    else:
        channel_kind = 3   # other — 800 ms channel bias

    # raw_text_var holds the full "SENDER: content" string captured before
    # the colon-split.  Fall back to message.content only if the context var
    # was not set (should not happen in normal flow).
    raw_text = raw_text_var.get(message.content or "")

    return {
        "channel_kind":        channel_kind,
        "channel_name":        message.channel or "",
        "sender_name":         message.sender_id or "",
        "sender_key_prefix":   key_prefix,
        "sender_key_prefix_len": 6,
        "sender_timestamp":    message.timestamp or 0,
        "text":                raw_text,
        "path_hash_count":     message.hops or 0,
    }
```

### Why `path_hash_count = message.hops`

The firmware's `path_hash_count` is the number of intermediate relay hashes
in the packet path. `MeshMessage.hops` is set from `packet_info["path_len"]`
which is also the relay node count. They are equivalent.

### `channel_kind` interoperability caveat

The mapping above is by channel name convention. If the firmware determines
`channel_kind` by channel configuration type (not name), fingerprints will
not match between firmware bots and Python bots for channels other than `#bot`
and `#testing`. However:

- All Python bots in the network use the same mapping → suppression between
  Python bots is correct regardless.
- Cross-suppression with actual firmware-patched bots requires the mapping to
  be correct, which it likely is for `#bot`/`#testing`.

---

## Context variables (module-level in `message_interceptor.py`)

```python
from contextvars import ContextVar

current_message_var: ContextVar = ContextVar('current_message')
coordinated_var:     ContextVar = ContextVar('coordinated', default=False)
raw_text_var:        ContextVar[str] = ContextVar('raw_text', default='')
```

`raw_text_var` is set in `_wrapped_handle_channel_message` and consumed in
`_to_fingerprint_input`. It is reset in the `finally` block of the
`handle_channel_message` wrapper.

---

## Response prefix format

All channel responses (non-DM) are prefixed: `[xxxx] response text`.

- The `[xxxx]` token is the low 16 bits of the request fingerprint, formatted
  as 4 lowercase hex digits.
- `prepend_request_token_text(fp_input, content)` produces this string.
- DM responses are NOT prefixed. The fingerprint module handles DM channel_kind
  correctly (DM responses use a different fingerprint path), but since DMs bypass
  coordination entirely, we never call prepend for DMs.
- The prefix is shown as-is in Discord webhooks and web viewer (no stripping).
- Total prefix length is always exactly 7 bytes: `[ x x x x ]   `.

---

## DM shortcut

DMs bypass ALL firmware coordination:

- No fingerprinting.
- No `schedule_response` call.
- No delay.
- No `[xxxx] ` prefix.
- `_coordinated_send_response`: if `message.is_dm`, call original immediately.
- `_coordinated_send_channel_message`: DMs do not use this path (channel messages only).

---

## Concurrency model

- `pending` and `recent` lists are accessed only from the asyncio event loop
  (no threads). No lock needed.
- While `asyncio.sleep(delay_ms / 1000)` is awaited, other coroutines CAN run.
  If a peer response arrives during the sleep, `_wrapped_process_message` calls
  `observe_peer_message` which mutates the pending entry's `suppressed` flag.
  When sleep finishes, the flag is checked. This works correctly with asyncio's
  cooperative scheduling — no polling loop or asyncio.Event needed.
- `cleanup()` should be called infrequently (e.g. every 60 s). It can be
  triggered at the start of `_wrapped_process_message` with a simple timestamp
  gate:
  ```python
  if time.time() - self._last_cleanup > 60:
      self.firmware_coordinator.cleanup(int(time.time() * 1000) & 0xFFFFFFFF)
      self._last_cleanup = time.time()
  ```

---

## Suppression flow detail

When this bot is waiting to respond to request token `0x1a2b`:

1. Peer bot sends `"BotB: [1a2b] Pong!"` over the air.
2. Our radio receives it; meshcore SDK fires a channel message event.
3. `_wrapped_handle_channel_message` captures raw text `"BotB: [1a2b] Pong!"`.
4. `handle_channel_message` colon-splits → `message.content = "[1a2b] Pong!"`.
5. `_wrapped_process_message` calls `firmware_coordinator.observe_peer_message("[1a2b] Pong!")`.
6. `parse_request_token_prefix("[1a2b] Pong!")` returns `(0x1a2b, 7)`.
7. `suppress_by_request_token(pending, 0x1a2b)` marks our entry `suppressed=True`.
8. Our `asyncio.sleep` finishes; `entry.suppressed` is `True`; we return `False`.

**Edge case:** If the peer response arrives AFTER our sleep finishes but BEFORE
we call `_original_send_response`, we still send. This is acceptable — the
firmware has the same race. The `recently_sent` check will prevent a third bot
from double-responding.

---

## `restore()` method on `MessageInterceptor`

The current `stop()` in `community_core.py` calls `self.message_interceptor.restore()`.
This method must be added/updated to unpatch all 3 patch points:

```python
def restore(self):
    self.bot.message_handler.handle_channel_message = self._original_handle_channel_message
    self.bot.message_handler.process_message       = self._original_process_message
    self.bot.command_manager.send_response         = self._original_send_response
    self.bot.command_manager.send_channel_message  = self._original_send_channel_message
```

---

## `config.ini.example` / `.env.example` updates

Remove all `COORDINATOR_*` environment variable entries.
Keep `MESH_REGION`.
Add a comment explaining the firmware-native coordination mode requires no
external service.

---

## Docker / Makefile

`requirements.txt`: remove `httpx` if it is only used by `coordinator_client.py`
and `packet_reporter.py`. Search for other uses before removing.

---

## Gotchas and hard constraints

### 1. Raw text context var must be set before `process_message`

`raw_text_var` is set in the `handle_channel_message` wrapper. The
`process_message` wrapper is called from inside `handle_channel_message`, so the
context var is already populated when `_coordinated_send_response` fires. If
for any reason `handle_channel_message` is bypassed (e.g. DM path), `raw_text_var`
falls back to `""` which is harmless since DMs skip coordination entirely.

### 2. `sender_pubkey` hex validity

Agreed to always be present, but `bytes.fromhex()` raises `ValueError` on
non-hex input. Always wrap in try/except and fall back to 6 zero bytes.

### 3. uint32 millisecond wrapping

`response_due_at()` in the firmware module wraps with `& 0xFFFFFFFF`. For
the actual wait, use `delay_ms` directly in `asyncio.sleep(delay_ms / 1000)`.
Do NOT compute `due_at - now` for the wait duration — uint32 subtraction
would produce wrong results near the wrap point (~49 days uptime).

### 4. `bot_identity_seed` default before connect

`FirmwareCoordinator` is instantiated in `__init__` before the radio connects.
`bot_identity_seed` defaults to `0`. With seed `0`, `tie_break_bias` still
returns a value (computed from fingerprint XOR 0). All bots with seed `0`
produce identical tie-break values for the same message, which degrades to
first-come-first-served on timing. This is acceptable as a startup edge case.

### 5. Double-coordination guard

`coordinated_var` prevents `send_channel_message` from re-coordinating a
command response that was already coordinated by `send_response`. This logic
is unchanged from the current implementation. When `send_response` runs, it
sets `coordinated_var = True`. When `send_channel_message` subsequently fires
for the same command, it detects `previously_coordinated = True` and passes
through immediately.

### 6. Prepend uses `fp_input`, not `message`

`prepend_request_token_text(fp_input, content)` — the first argument is the
fingerprint input DICT, not the raw `MeshMessage`. Both `fp_input` and
`message` satisfy the `MessageInput` protocol, but only `fp_input` contains
the correct `text` field (raw text). Using `message` directly would produce
a different token since `message.content` is stripped.

### 7. `send_channel_message` fingerprint context

`_coordinated_send_channel_message` must read the inbound message from
`current_message_var` to build `fp_input`. If `current_message_var` is not
set (e.g. a scheduled channel broadcast not triggered by an inbound message),
there is no request to fingerprint. In that case, send immediately without
coordination — scheduled broadcasts are not competitive.

### 8. `message_interceptor.restore()` must unpatch all 3 points

Current code only unpatches `process_message`, `send_response`, and
`send_channel_message`. The new patch on `handle_channel_message` must also
be restored in `restore()` or the bot will segfault/error on reconnect.

### 9. `httpx` dependency

`coordinator_client.py` is the only user of `httpx`. Once deleted, remove
`httpx` from `requirements.txt`. Verify no other file imports it first.

### 10. Web viewer community page

`web_viewer_community_page.py` must not depend on `bot.coordinator`. Firmware
coordination status can be rendered from emitted `fw_*` packet-stream events.

### 11. Firmware bots vs Python-only network

If there are no actual firmware bots in the network, the `channel_kind`
mapping and `raw_text` accuracy are irrelevant — all Python bots produce
the same (consistently wrong) fingerprints and suppression works correctly.
The firmware-exact fingerprint behaviour only matters for cross-suppression
with real firmware bots.

---

## Implementation order (recommended)

1. Copy `meshcore_request_token.py` and `meshcore_response_coordinator.py`
   into `community/`. Run the validators to confirm they pass.
2. Write `community/firmware_coordinator.py` and write a small test.
3. Rewrite `community/message_interceptor.py`.
4. Update `community/config.py` → `CommunityConfig`.
5. Update `community/community_core.py`.
6. Delete `coordinator_client.py`, `packet_reporter.py`, `response_timing.py`.
7. Update `community/commands/botstatus_command.py`.
8. Update `community/web_viewer_packet_stream.py` and `web_viewer_community_page.py`.
9. Update `requirements.txt` (remove `httpx`).
10. Update `.env.example` and `config.ini.example`.
11. Run `validate_meshcore_request_token.py` and `validate_meshcore_response_coordinator.py`
    to confirm no regressions in the copied modules.
