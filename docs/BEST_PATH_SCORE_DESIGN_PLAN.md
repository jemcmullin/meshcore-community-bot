# Design Plan: Best-Path Scoring (from `best-path-score` branch)

## Overview

The `best-path-score` branch adds per-message proximity scoring so the coordinator (and fallback mode) can pick the bot with the best-known path to the _sender_, not just the highest overall coverage score. It does this by:

1. Learning which repeater nodes are local/private feeders vs. genuine shared infrastructure (**NetworkObserver**).
2. Querying the DB for the bot's stored outbound hop count to the sender (**outbound_hops**).
3. Blending hop count + path significance into a single `sender_proximity_score` sent to the coordinator.
4. Using that same blend for fallback delay when coordinator is unreachable.

All changes are confined to `community/`. The `meshcore-bot/` submodule is **not touched**.

---

## What Changes (File by File)

### New Files

#### `community/network_observer.py`

Passive observer that feeds on **every** channel message (not just commands). For each message:

- Parses the path string into a list of repeater node IDs.
- Records which nodes appear, and whether each was the _last hop_ before this bot.
- A node that is always the last hop is a co-located/private feeder (low significance).
- A node that appears at varying positions is genuine shared infrastructure (high significance).

Stores daily aggregates in a new `repeater_daily_stats` DB table (rolling window, default 7 days). Provides:

- `observe_path(path_nodes)` — called for each channel message.
- `get_node_significance(node_id) → float (0-1)` — 0=always last hop (local feeder), 1=never last hop (shared infra).
- `compute_path_significance(path_nodes) → float | None` — mean significance across all nodes in a path.

Config loaded from `scoring_observer_config.ini` `[NetworkObserver]` section or `OBSERVER_*` env vars.

#### `community/scoring_observer_config.ini`

Central config file for both scoring and repeater-learning parameters. Contains two sections:

- `[Scoring]` — `hop_weight`, `path_sig_weight`, delay timing, degradation settings.
- `[NetworkObserver]` — `window_days`, `min_observations`, cleanup/summary intervals.

All values can be overridden by `SCORING_*` and `OBSERVER_*` environment variables.

---

### Modified Files

#### `community/config.py`

**Add** a new `ScoringConfig` dataclass alongside the existing `CoordinatorConfig`:

```python
@dataclass
class ScoringConfig:
    hop_weight: float = 0.6          # weight for hop count in proximity score
    path_sig_weight: float = 0.4     # weight for path significance
    base_delay_ms: int = 2000        # max fallback delay window
    min_delay_ms: int = 100          # floor delay even for best-scored bot
    max_jitter_ms: int = 200         # random jitter to prevent ties
    degrade_after_seconds: int = 3600
    degrade_target: float = 0.5
    degrade_window_seconds: int = 86400

    @classmethod
    def from_env_and_config(cls, config) -> "ScoringConfig": ...
```

`from_env_and_config` reads from the `[Scoring]` section of the provided config (falls back to defaults if section absent) and then checks `SCORING_*` env vars which take precedence. Since `community_core.py` passes `self.config` (the bot's `config.ini`) which doesn't have a `[Scoring]` section, defaults are used unless overridden by env vars — this is the intended behaviour for most deployers.

**Non-breaking:** `CoordinatorConfig` is unchanged. `ScoringConfig` is purely additive.

---

#### `community/coordinator_client.py`

Three changes:

1. **Lazy `httpx.AsyncClient` initialization.** The current branch creates the client eagerly in `__init__` (before the asyncio event loop is running), which can cause event loop binding issues. Replace with a `_ensure_client()` coroutine that creates it on first use. The `close()` method already handles cleanup.

   ```python
   # Before (current branch):
   self._client = httpx.AsyncClient(base_url=..., ...)

   # After:
   self._client: Optional[httpx.AsyncClient] = None

   async def _ensure_client(self) -> httpx.AsyncClient:
       if self._client is None:
           self._client = httpx.AsyncClient(base_url=..., ...)
       return self._client
   ```

   All callers switch from `self._client.post(...)` to `client = await self._ensure_client(); client.post(...)`.

2. **Add `parse_path_nodes(path_string) → list` static method.** Extracts repeater node IDs from a path string like `"98,11,a4 (2 hops via Flood)"` → `["98", "11", "A4"]`. Handles "Direct", empty/None, and variable-length (multi-byte) IDs. Used by `NetworkObserver` and `MessageInterceptor`.

3. **Add `compute_sender_proximity_score(hops, outbound_hops, path_significance, hop_weight, path_sig_weight) → float` static method** plus **extend `should_respond()` signature** with `outbound_hops`, `path_significance`, `hop_weight`, `path_sig_weight` optional kwargs. The pre-computed `sender_proximity_score` is included in the coordinator payload as the primary bidding field; SNR/RSSI remain for analytics only.

   ```
   proximity = hop_score * hop_weight + path_sig * path_sig_weight
   hop_score = max(0, 1 - best_hops * 0.25)
   best_hops = min(inbound_hops, outbound_hops) if both known
   ```

**Non-breaking:** All new `should_respond()` params are optional with safe defaults. Existing callers work unchanged.

---

#### `community/coverage_fallback.py`

Two changes:

1. **Accept `scoring_config: Optional[ScoringConfig] = None`** in `__init__`. If provided, use `self.config` attributes instead of the current module-level constants (`BASE_DELAY_MS`, etc.). If not provided, fall back to a small `_DefaultConfig` dataclass with the same values — so existing `CoverageFallback()` callsites keep working.

2. **Add two new methods:**
   - `compute_delay_ms_with_signal(hops, outbound_hops, path_significance) → int` — blends per-message proximity with the cached coverage score to compute delay. Used in fallback mode to ensure the nearest bot wins the race.
   - `wait_before_responding_with_signal(hops, outbound_hops, path_significance) → float` — awaits the signal-aware delay and returns seconds waited.

**Non-breaking:** `__init__` signature change is backward-compatible (`scoring_config=None`). Old constants remain as module-level defaults. `wait_before_responding()` is unchanged.

---

#### `community/message_interceptor.py`

Four additions (three patches + path metrics):

1. **Accept `network_observer: Optional[NetworkObserver] = None`** in `__init__` and install **two additional** `MethodType` patches on `MessageHandler`:
   - **`handle_rf_log_data` (primary observation source)** — fires on every raw RF frame. `routing_info['path_nodes']` is already decoded by the original handler before it appends to `recent_rf_data`. The wrapper records `t_before = time.time()` before calling the original, then checks `rf_list[-1].timestamp >= t_before` to identify the newly-appended entry. A length snapshot is not used because the internal cleanup inside `handle_rf_log_data` can shrink the list, so `len` can decrease even when a new entry was added. No duplicate decode and no deep copy needed — asyncio is single-threaded so the list cannot be mutated between the awaited return and the read. **DIRECT packets (overheard DMs) are skipped** — the bot is a bystander, and nearby DMs naturally route through the same local feeder, which would artificially inflate that feeder's significance score. Significance scoring is only meaningful for flood traffic. This fires for all other packet types including ADVERT and non-command TXT_MSG that never reach `process_message`.

   - **`process_message` (secondary — counter only)** — wraps the original solely to increment `self.bot.messages_processed_count`. Path observation is **not** performed here to avoid double-counting: `handle_rf_log_data` already observes the same path nodes for every packet that eventually reaches `process_message`.

   `restore()` must restore all three patched methods.

2. **Add `_observing_handle_rf_log_data(event, metadata)`** — primary observation patch (see above).

3. **Add `_observing_process_message(message)`** — increments `messages_processed_count` only; does **not** call `observe_path`.

4. **Add `_get_path_metrics(message) → (outbound_hops, path_significance)`** — queries the local DB:
   - `outbound_hops`: from `complete_contact_tracking.out_path_len` for this sender's pubkey.
   - `path_significance`: calls `network_observer.compute_path_significance(path_nodes)`.
     Both return `None` on error or missing data (handled gracefully by the scoring math). Called in `_coordinated_send_response()` before the coordinator bid.

5. **Fallback path**: `await self.fallback.wait_before_responding_with_signal(hops=..., outbound_hops=..., path_significance=...)`.

6. **Metrics**: `self.bot.messages_responded_count` incremented in `_coordinated_send_response`; `messages_processed_count` incremented in `_observing_process_message`.

**Non-breaking concern:** `network_observer=None` default is additive. When `None`, the `handle_rf_log_data` patch is not installed at all. `_observing_process_message` still runs (for the counter) but skips observation. `_get_path_metrics` returns `(None, None)`.

---

#### `community/community_core.py`

Wire everything together:

1. Import `ScoringConfig` from `.config` and `NetworkObserver` from `.network_observer`.
2. After `super().__init__()`, load `self.scoring_config = ScoringConfig.from_env_and_config(self.config)`.
3. Create `self.network_observer = NetworkObserver(self.db_manager)`.
4. Pass `scoring_config=self.scoring_config` to `CoverageFallback(...)`.
5. Pass `network_observer=self.network_observer` to `MessageInterceptor(...)`.
6. Initialize `self.messages_processed_count = 0` and `self.messages_responded_count = 0` (used by `MessageInterceptor` and heartbeat).

---

## Implementation Order (dependency-first)

| Step | File                                   | Reason                                                                                                |
| ---- | -------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 1    | `scoring_observer_config.ini` (create) | No deps; config file for steps 2–4                                                                    |
| 2    | `config.py` (add `ScoringConfig`)      | Needed by `coverage_fallback`, `coordinator_client`                                                   |
| 3    | `coordinator_client.py`                | Lazy client + static methods needed by `network_observer`, `coverage_fallback`, `message_interceptor` |
| 4    | `network_observer.py` (create)         | Needed by `message_interceptor`                                                                       |
| 5    | `coverage_fallback.py`                 | Needs `ScoringConfig` (step 2) and `coordinator_client` static method (step 3)                        |
| 6    | `message_interceptor.py`               | Needs `NetworkObserver` (step 4), updated `CoverageFallback` (step 5), `coordinator_client` (step 3)  |
| 7    | `community_core.py`                    | Wires all of the above                                                                                |

---

## Non-Breaking Guarantees

| Concern                                      | How it's handled                                                                                                                                                 |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Existing commands (20+)                      | Unchanged — they call `BaseCommand.send_response()` → `CommandManager.send_response()` → interceptor. No API changes.                                            |
| `CoverageFallback()` (no args)               | `scoring_config=None` default retains current behavior via `_DefaultConfig` with same constants.                                                                 |
| `MessageInterceptor` (no `network_observer`) | `network_observer=None` default: `_observing_process_message` passes through; `_get_path_metrics` returns `(None, None)`.                                        |
| `should_respond()` without new kwargs        | All new params have safe defaults. Coordinator payload gains `sender_proximity_score` (new field, coordinator ignores unknowns gracefully).                      |
| `scoring_observer_config.ini` absent         | `NetworkObserver` and `ScoringConfig.from_env_and_config` both use hardcoded defaults.                                                                           |
| `complete_contact_tracking` table absent     | `_get_path_metrics` wraps DB query in try/except; returns `None` on failure.                                                                                     |
| `repeater_daily_stats` table absent          | `NetworkObserver._init_tables()` creates it on startup.                                                                                                          |
| `meshcore-bot/` submodule                    | Zero changes required.                                                                                                                                           |
| Coordinator protocol                         | Existing coordinator still works: `sender_proximity_score` is a new field it can optionally use; `receiver_snr`/`receiver_rssi` remain in payload for analytics. |

---

## Risk Notes

- **`db_manager` table whitelist** ⚠️: `meshcore-bot/modules/db_manager.py` enforces an `ALLOWED_TABLES` whitelist on `create_table()` and `drop_table()`. The three tables `NetworkObserver` needs (`repeater_node_stats`, `repeater_daily_stats`, `network_observer_meta`) are **not** in the whitelist, so calling `db_manager.create_table(...)` for them will raise `ValueError` and crash on startup.

  **Fix (preferred — no submodule touch):** Replace the three `db_manager.create_table(...)` calls in `NetworkObserver._init_tables()` with raw `db_manager.execute_query("CREATE TABLE IF NOT EXISTS ...")` calls. `execute_query()` is not whitelist-gated and is already used for `CREATE INDEX` in the same method.

  Tables actually created by `NetworkObserver`: `repeater_daily_stats`, `network_observer_meta`. (`repeater_node_stats` is **not** used.)

  _Alternative:_ In `CommunityBot.__init__` (after `super().__init__()`), monkeypatch the class-level set:

  ```python
  self.db_manager.ALLOWED_TABLES |= {'repeater_node_stats', 'repeater_daily_stats', 'network_observer_meta'}
  ```

  This works but is more fragile if the whitelist implementation changes.

- **`complete_contact_tracking` table**: The `_get_path_metrics` query assumes `out_path_len` column exists. If the submodule version of meshcore-bot doesn't populate this table, `outbound_hops` will always be `None` — scoring falls back to inbound-only, which is still an improvement over SNR-based scoring. No crash risk.
- **`MethodType` patch on `process_message`**: The new branch uses `types.MethodType` to bind wrapper functions as instance methods on `command_manager` and `message_handler`. This is the correct Python pattern. The old branch's simpler function assignment for `send_response` works only because `send_response` is called with an explicit `self` argument in the original code — the new wrapper accounts for both styles.
- **Event loop and `httpx.AsyncClient`**: The lazy-init fix is important and should be included even if the rest of the changes aren't — the current eager init can fail on some Python/httpx versions when `asyncio` hasn't started yet.
- **DB write load**: `NetworkObserver.observe_path()` fires for every channel message. With daily aggregation and a `% 50` save throttle it's low overhead, but operators on very busy networks should be aware. The `cleanup_interval_seconds` and `window_days` config values can be tuned.

---

## Implementation Notes for Fresh Session

These are the concrete gotchas discovered during design review that the implementer needs to know before writing a single line of code.

### Read before writing

Always read the current file before editing. The files to modify already exist; do not recreate them:

- `community/config.py` — ends after `CoordinatorConfig`; append `ScoringConfig` after it.
- `community/coordinator_client.py` — current `__init__` eagerly creates `self._client = httpx.AsyncClient(...)` at line ~35; this is the line to replace with `self._client = None` + `_ensure_client()`.
- `community/coverage_fallback.py` — currently uses module-level constants (`BASE_DELAY_MS` etc.) not a config object; `__init__` takes no args.
- `community/message_interceptor.py` — currently patches only `send_response`, using a plain function assignment (not `MethodType`). The `restore()` method only restores `send_response`.
- `community/community_core.py` — currently has no `ScoringConfig`, no `NetworkObserver`, no metrics counters.

### Import additions required (not in current files)

| File                     | New imports needed                                                                                                            |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `coordinator_client.py`  | `import re` (for `parse_path_nodes`)                                                                                          |
| `message_interceptor.py` | `from types import MethodType`, `from .network_observer import NetworkObserver`                                               |
| `community_core.py`      | `from .config import CoordinatorConfig, ScoringConfig` (add `ScoringConfig`), `from .network_observer import NetworkObserver` |

### Two separate config-reading paths for scoring values

`ScoringConfig.from_env_and_config(config)` reads from the **bot's `config.ini`** `[Scoring]` section (which typically won't exist, so defaults + `SCORING_*` env vars apply). `NetworkObserver._load_observer_config()` reads from **`community/scoring_observer_config.ini`** directly (path relative to `network_observer.py`'s `__file__`). Both also honour `OBSERVER_*` / `SCORING_*` env vars. The `.ini` file is primarily for `NetworkObserver`; `ScoringConfig` is wired through the normal bot config object.

### Metrics counters placement

`messages_processed_count` and `messages_responded_count` must be initialised in `CommunityBot.__init__` **before** `MessageInterceptor(...)` is constructed, because the interceptor holds a reference to `self.bot` and increments these on first message. Put them right after `super().__init__()`.

### `MethodType` vs plain function assignment

The current `message_interceptor.py` uses `bot.command_manager.send_response = self._coordinated_send_response` (plain assignment — works because `CommandManager` calls it as `await self.send_response(message, content)` passing `self` explicitly in the original code). The new `process_message` patch **must** use `MethodType` because `MessageHandler` calls `await self.process_message(message)` where Python's descriptor protocol would normally pass `self` — without `MethodType` the wrapper receives `message_handler` as its first positional arg instead of `message`. Use `MethodType` for both patches for consistency.

### `repeater_node_stats` is legacy

The branch creates this table in `_init_tables()` with the comment "Legacy table kept for backward compatibility (not used anymore)". It is safe to omit it entirely — no code reads from it. Only `repeater_daily_stats` and `network_observer_meta` are actively used.

### DB table creation — use `execute_query` not `create_table`

As documented in Risk Notes: all three `db_manager.create_table(...)` calls in `NetworkObserver._init_tables()` must be replaced with `db_manager.execute_query("CREATE TABLE IF NOT EXISTS ...")`. The whitelist blocks `create_table` for any table not in `ALLOWED_TABLES`.

### Reference source

The full content of all changed files from `best-path-score` was fetched during design review from:
`https://raw.githubusercontent.com/jemcmullin/meshcore-community-bot/best-path-score/community/<filename>`

---

## Files Summary

| File                                    | Action                                                                                                                                                          | Lines changed (approx) |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `community/scoring_observer_config.ini` | **Create**                                                                                                                                                      | ~60                    |
| `community/network_observer.py`         | **Create**                                                                                                                                                      | ~250                   |
| `community/config.py`                   | **Modify** — add `ScoringConfig`                                                                                                                                | +60                    |
| `community/coordinator_client.py`       | **Modify** — lazy client, 2 static methods, extend `should_respond`                                                                                             | +120                   |
| `community/coverage_fallback.py`        | **Modify** — `scoring_config` param, 2 new methods                                                                                                              | +60                    |
| `community/message_interceptor.py`      | **Modify** — `network_observer` param, `handle_rf_log_data` patch (primary), `process_message` patch (counter only), `_get_path_metrics`, signal-aware fallback | +110                   |
| `community/community_core.py`           | **Modify** — wire `ScoringConfig`, `NetworkObserver`, metrics                                                                                                   | +15                    |
| `meshcore-bot/`                         | **No changes**                                                                                                                                                  | 0                      |
