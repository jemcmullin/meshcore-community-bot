# Design Plan: Best-Path Delivery Score

## Goals

**Goal 1 — Submodule data only.** All scoring inputs come exclusively from tables already tracked and persisted by the `meshcore-bot` submodule. No community-side learning, no new tables, no background observers.

**Goal 2 — 0-1 delivery score.** Each bot computes a single `delivery_score ∈ [0, 1]` that represents the likelihood of a response from _this bot_ successfully reaching the sender, given known mesh topology and this bot's proximity to that sender. That score is sent to the coordinator as the primary bidding field. The bot with the highest score responds; others suppress.

All changes are confined to `community/`. The `meshcore-bot/` submodule is **not touched**.

---

## Data Sources (meshcore-bot submodule only)

All four scoring inputs read from tables created and populated by the submodule. No writes, no new tables.

### `complete_contact_tracking`

- **`out_path_len`** — stored outbound hop count to this sender (set when the bot last sent to them).
- **`public_key`** — match by 8-char prefix of `message.sender_pubkey`.
- Provides: `outbound_hops`.

### `observed_paths`

- **`observation_count`** — how many times a message path from this sender has been independently confirmed.
- **`last_seen`** — timestamp of the most recent confirmed path, used to compute path age.
- **`from_prefix`** — 2-char prefix of the sender node; matched against sender pubkey prefix.
- **`packet_type`** — filter to `!= 'ADVERT'` (command/message paths only).
- Provides: `path_reliability`, `path_freshness`.

### `mesh_connections`

- **`to_prefix`** — a repeater node in the inbound path.
- **`from_prefix`** — an upstream node that routes through `to_prefix` (distinct count = fan-in).
- Fan-in of each node in `message.path` reveals whether it is private infrastructure (few upstream sources) or shared backbone (many upstream sources).
- Provides: `infrastructure_score`.

### Live message fields (not DB)

- **`message.hops`** — inbound hop count on the arriving packet. Used as a fallback when `out_path_len` is unknown and as a cross-check when both are available.
- **`message.path`** — the literal path string (e.g. `"98,11,A4 (2 hops via Flood)"`). Parsed into node prefixes for the `mesh_connections` fan-in query.

---

## Scoring Formula

```
# Component 1 — proximity (hop count)
best_hops  = min(inbound_hops, outbound_hops)  -- use whichever is available; min if both
hop_score  = max(0.0, 1.0 - best_hops * 0.25) -- 0 hops=1.0, 4 hops=0.0, saturates at 0

# Component 2 — infrastructure quality of inbound path
#   For each node X in message.path, count distinct upstream nodes in mesh_connections
fan_in           = COUNT(DISTINCT from_prefix) WHERE to_prefix = X
infra_score_X    = min(1.0, fan_in / 10.0)    -- saturates at 10 distinct feeders
infrastructure   = mean(infra_score_X for X in path nodes)

# Component 3 — path reliability (confirmed observations to this sender)
path_reliability = min(1.0, observation_count / 20.0)  -- saturates at 20

# Component 4 — path freshness (how recently was this path confirmed)
path_freshness   = exp(-age_hours / 6.0)               -- half-life ~4h; ~5% at 18h; ~0 at 36h+

# Final score
delivery_score = hop_score        * 0.50
              + infrastructure    * 0.25
              + path_reliability  * 0.15
              + path_freshness    * 0.10
```

**Unknown inputs default to `0.5` (neutral) — missing data never penalises a bot.** Weights sum to 1.0. Hop count is the dominant term when data is sparse; the topology components sharpen the decision as history accumulates.

---

## Score Interpretation

| Score range | Meaning                                                                                |
| ----------- | -------------------------------------------------------------------------------------- |
| 0.75 – 1.0  | Excellent: close, well-confirmed path through shared infrastructure, recently verified |
| 0.50 – 0.75 | Good: moderate distance or moderate confidence                                         |
| 0.25 – 0.50 | Marginal: far, or stale, or only private-feeder paths observed                         |
| 0.0 – 0.25  | Poor: many hops + no confirmed path data                                               |

A score of exactly `0.5` means the bot has no data at all for this sender — pure neutral.

---

## What Changes (File by File)

### Files Removed vs. Current Branch

| File                                    | Status       | Reason                                                            |
| --------------------------------------- | ------------ | ----------------------------------------------------------------- |
| `community/network_observer.py`         | **Remove**   | Replaced entirely by `mesh_connections` fan-in query              |
| `community/scoring_observer_config.ini` | **Simplify** | Remove `[NetworkObserver]` section; keep `[Scoring]` weights only |

### New Files

#### (none)

### Modified Files

#### `community/scoring_observer_config.ini`

Remove the `[NetworkObserver]` section entirely. The `[Scoring]` section remains as the configurable weight knobs:

```ini
[Scoring]
hop_weight          = 0.50   ; dominant — direct proximity signal
infra_weight        = 0.25   ; infrastructure quality of inbound path
reliability_weight  = 0.15   ; how many confirmed paths to this sender
freshness_weight    = 0.10   ; how recently that path was confirmed
base_delay_ms       = 2000   ; fallback: max delay window (ms)
min_delay_ms        = 100    ; fallback: floor delay even for best bot
max_jitter_ms       = 200    ; fallback: random jitter to break ties
```

All values overridable by `SCORING_*` env vars.

---

#### `community/config.py`

`ScoringConfig` gains two new weight fields to replace `path_sig_weight`:

```python
@dataclass
class ScoringConfig:
    hop_weight:          float = 0.50   # weight for hop count
    infra_weight:        float = 0.25   # weight for infrastructure score (mesh_connections fan-in)
    reliability_weight:  float = 0.15   # weight for path_reliability (observed_paths obs count)
    freshness_weight:    float = 0.10   # weight for path_freshness (observed_paths last_seen age)
    base_delay_ms:       int   = 2000
    min_delay_ms:        int   = 100
    max_jitter_ms:       int   = 200
```

`path_sig_weight` is removed. `from_env_and_config` reads the `[Scoring]` section of the bot's `config.ini` then applies `SCORING_*` env var overrides.

**Non-breaking:** `CoordinatorConfig` is unchanged.

---

#### `community/coordinator_client.py`

Two changes:

1. **Rename and expand `compute_sender_proximity_score` → `compute_delivery_score`.**

   ```python
   @staticmethod
   def compute_delivery_score(
       inbound_hops:      Optional[int],
       outbound_hops:     Optional[int],
       infrastructure:    Optional[float],   # from mesh_connections fan-in
       path_reliability:  Optional[float],   # from observed_paths obs count
       path_freshness:    Optional[float],   # from observed_paths age
       w_hops:            float = 0.50,
       w_infra:           float = 0.25,
       w_reliability:     float = 0.15,
       w_freshness:       float = 0.10,
   ) -> float:
       best_hops = min(h for h in [inbound_hops, outbound_hops] if h is not None) \
                   if any(h is not None for h in [inbound_hops, outbound_hops]) else None
       hop_score = max(0.0, 1.0 - best_hops * 0.25) if best_hops is not None else 0.5
       infra     = infrastructure   if infrastructure   is not None else 0.5
       rel       = path_reliability if path_reliability is not None else 0.5
       fresh     = path_freshness   if path_freshness   is not None else 0.5
       return hop_score * w_hops + infra * w_infra + rel * w_reliability + fresh * w_freshness
   ```

   `parse_path_nodes` remains unchanged (already implemented, used for the `mesh_connections` query in `_get_path_metrics`).

2. **Extend `should_respond()` signature** — replace `path_significance` with `infrastructure`, `path_reliability`, `path_freshness` optional kwargs. The pre-computed `delivery_score` becomes the coordinator payload field (renamed from `sender_proximity_score`). SNR and RSSI are removed from the payload entirely — they are not scoring inputs.

```python
payload["delivery_score"] = self.compute_delivery_score(
    inbound_hops=receiver_hops,
    outbound_hops=outbound_hops,
    infrastructure=infrastructure,
    path_reliability=path_reliability,
    path_freshness=path_freshness,
    ...scoring_config weights...
)
```

**Non-breaking:** All new `should_respond()` params default to `None`. `delivery_score` at `0.5` is neutral — the coordinator treats it as a no-preference bid if all bots send `0.5`.

---

#### `community/coverage_fallback.py`

Update `compute_delay_ms_with_signal` and `wait_before_responding_with_signal` to accept `infrastructure`, `path_reliability`, `path_freshness` in place of `path_significance`. Internally calls `compute_delivery_score` to produce the delay factor. No structural changes.

---

#### `community/message_interceptor.py`

Three changes:

1. **Remove `network_observer` parameter and all related code.** The `handle_rf_log_data` patch is not installed. `_observing_process_message` remains for `messages_processed_count` only (counter). `restore()` only needs to restore `send_response` and `process_message`.

2. **Replace `_get_path_metrics` with a 4-output version** that reads exclusively from submodule tables:

   ```python
   async def _get_path_metrics(self, message):
       """
       Returns (outbound_hops, infrastructure, path_reliability, path_freshness).
       All values may be None; None is handled as neutral (0.5) in scoring.
       Sources: complete_contact_tracking, mesh_connections, observed_paths.
       """
       sender_pubkey  = message.sender_pubkey or ""
       sender_prefix8 = sender_pubkey[:8].upper() if sender_pubkey else ""
       sender_prefix2 = sender_pubkey[:2].lower()  if sender_pubkey else ""
       path_nodes = CoordinatorClient.parse_path_nodes(message.path)  # ["98", "11", "A4"]

       outbound_hops     = None
       infrastructure    = None
       path_reliability  = None
       path_freshness    = None

       # --- outbound_hops from complete_contact_tracking ---
       if sender_prefix8:
           rows = await self.bot.db_manager.aexecute_query(
               """SELECT out_path_len FROM complete_contact_tracking
                  WHERE public_key LIKE ? AND out_path_len IS NOT NULL
                  ORDER BY last_heard DESC LIMIT 1""",
               (sender_prefix8 + "%",), fetch=True,
           )
           if rows:
               outbound_hops = int(rows[0][0])

       # --- infrastructure from mesh_connections fan-in ---
       if path_nodes:
           node_lower = [n.lower()[:2] for n in path_nodes]
           placeholders = ",".join("?" * len(node_lower))
           rows = await self.bot.db_manager.aexecute_query(
               f"""SELECT to_prefix, COUNT(DISTINCT from_prefix) AS fan_in
                   FROM mesh_connections
                   WHERE to_prefix IN ({placeholders})
                   GROUP BY to_prefix""",
               tuple(node_lower), fetch=True,
           )
           if rows:
               scores = [min(1.0, (r[1] or 0) / 10.0) for r in rows]
               infrastructure = sum(scores) / len(scores)

       # --- path_reliability + path_freshness from observed_paths ---
       if sender_prefix2:
           rows = await self.bot.db_manager.aexecute_query(
               """SELECT observation_count,
                         CAST((julianday('now') - julianday(last_seen)) * 24 AS REAL)
                  FROM observed_paths
                  WHERE from_prefix = ? AND packet_type != 'ADVERT'
                  ORDER BY observation_count DESC LIMIT 1""",
               (sender_prefix2,), fetch=True,
           )
           if rows:
               obs_count, age_hours = rows[0]
               path_reliability = min(1.0, (obs_count or 1) / 20.0)
               path_freshness   = math.exp(-(age_hours or 999) / 6.0)

       return outbound_hops, infrastructure, path_reliability, path_freshness
   ```

   All three queries are wrapped in try/except; any failure returns `None` for that component.

3. **Update `_coordinated_send_response`** to unpack the 4-tuple and pass all four components to `compute_delivery_score` and `should_respond`.

**Non-breaking:** `network_observer` param removed (was optional; no external callers pass it outside `community_core.py`). `_get_path_metrics` return type changes from 2-tuple to 4-tuple — only called internally.

---

#### `community/community_core.py`

Three changes from current state:

1. Import `ScoringConfig` from `.config` (no `NetworkObserver` import).
2. After `super().__init__()`: `self.scoring_config = ScoringConfig.from_env_and_config(self.config)`.
3. Pass `scoring_config=self.scoring_config` to `CoverageFallback`.
4. Do **not** pass `network_observer` to `MessageInterceptor` — parameter removed.
5. Initialise `self.messages_processed_count = 0` and `self.messages_responded_count = 0`.

---

## Implementation Order (dependency-first)

| Step | File                                    | Change                                                                            |
| ---- | --------------------------------------- | --------------------------------------------------------------------------------- |
| 1    | `community/scoring_observer_config.ini` | Remove `[NetworkObserver]` section; update `[Scoring]` weights                    |
| 2    | `community/config.py`                   | Update `ScoringConfig` field names and defaults                                   |
| 3    | `community/coordinator_client.py`       | `compute_delivery_score` (4 inputs, no SNR/RSSI); update `should_respond` payload |
| 4    | `community/coverage_fallback.py`        | Update `wait_before_responding_with_signal` params                                |
| 5    | `community/message_interceptor.py`      | New `_get_path_metrics` (3 DB queries); remove NetworkObserver wiring             |
| 6    | `community/community_core.py`           | Wire `ScoringConfig`; remove `NetworkObserver` construction                       |
| 7    | `community/network_observer.py`         | **Delete file**                                                                   |

---

## Non-Breaking Guarantees

| Concern                                                 | How it's handled                                                                                                                                    |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Existing commands (20+)                                 | Unchanged — `BaseCommand.send_response()` → `CommandManager.send_response()` → interceptor. No API changes.                                         |
| `CoverageFallback()` (no args)                          | `scoring_config=None` default retains current behaviour.                                                                                            |
| `should_respond()` without new kwargs                   | All new params default to `None`; score computed as `0.5` (neutral bid).                                                                            |
| `complete_contact_tracking` absent or no `out_path_len` | Query returns `None`; scoring falls back to inbound hops only.                                                                                      |
| `observed_paths` empty (new deployment)                 | Returns `None`; reliability and freshness default to neutral `0.5`.                                                                                 |
| `mesh_connections` empty or path is "Direct"            | Path nodes list is empty; `infrastructure` stays `None` → neutral `0.5`.                                                                            |
| `meshcore-bot/` submodule                               | Zero changes required.                                                                                                                              |
| Coordinator protocol                                    | `delivery_score` replaces `sender_proximity_score` — coordinator treats any unknown float field as a preference score; old name ignored gracefully. |

---

## Risk Notes

- **`aexecute_query` fetch parameter**: The existing `_get_path_metrics` uses `aexecute_query(..., fetch=True)`. Verify this matches the actual signature in `db_manager.py` — if the async wrapper doesn't accept a `fetch` kwarg, use `await asyncio.to_thread(self.bot.db_manager.execute_query, ...)` instead.
- **`observed_paths.from_prefix` is 2-char, lowercase**: Confirmed from `repeater_manager.py` schema. Use `sender_pubkey[:2].lower()` not the 8-char prefix.
- **`complete_contact_tracking.public_key` is full pubkey, not prefix**: Query must use `LIKE ? || '%'` with the 8-char prefix, or match by exact full key if available on the message object.
- **SQL injection via `path_nodes` in `mesh_connections` query**: The `IN (?,?,?)` parameterised form is safe — node prefixes are already extracted and normalised by `parse_path_nodes`. Never interpolate them directly into SQL.
- **`MethodType` for `process_message` patch**: Must use `MethodType` — `MessageHandler` calls `await self.process_message(message)` via the descriptor protocol. Plain assignment would pass `message_handler` as first arg to the wrapper.
- **`math` import in `message_interceptor.py`**: Add `import math` for `math.exp(...)` in `_get_path_metrics`.

---

## Implementation Notes

### Files that already exist and must be read before editing

- `community/config.py` — `ScoringConfig` already exists with `hop_weight`/`path_sig_weight`; update field names, do not recreate.
- `community/coordinator_client.py` — `compute_sender_proximity_score` and `parse_path_nodes` already exist; rename/expand, do not recreate. Lazy `_ensure_client` already implemented.
- `community/coverage_fallback.py` — `wait_before_responding_with_signal` already exists; update its parameter list.
- `community/message_interceptor.py` — `_get_path_metrics` already exists returning 2-tuple; expand to 4-tuple. `_observing_handle_rf_log_data` and `_observing_process_message` already exist; remove the former, keep the latter.

### `network_observer.py` and the `handle_rf_log_data` patch

Both are deleted. The `handle_rf_log_data` patch in the current branch fed `NetworkObserver` — without it there is nothing to feed, so the patch is unnecessary. The `process_message` patch (counter only) is retained.

---

## Files Summary

| File                                    | Action                                                          | Net change        |
| --------------------------------------- | --------------------------------------------------------------- | ----------------- |
| `community/scoring_observer_config.ini` | Simplify — remove `[NetworkObserver]` section                   | −20 lines         |
| `community/network_observer.py`         | **Delete**                                                      | −~300 lines       |
| `community/config.py`                   | Update `ScoringConfig` fields                                   | ~5 lines changed  |
| `community/coordinator_client.py`       | Rename/expand `compute_delivery_score`; update `should_respond` | ~20 lines changed |
| `community/coverage_fallback.py`        | Update `wait_before_responding_with_signal` params              | ~10 lines changed |
| `community/message_interceptor.py`      | New `_get_path_metrics` (3 queries); remove observer wiring     | ~40 lines changed |
| `community/community_core.py`           | Remove `NetworkObserver`; wire `ScoringConfig`                  | ~10 lines changed |
| `meshcore-bot/`                         | **No changes**                                                  | 0                 |
