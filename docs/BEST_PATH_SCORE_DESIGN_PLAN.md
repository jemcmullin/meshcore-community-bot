# Design Plan: Best-Path Delivery Score

## Goals

**Goal 1 — Submodule data only.** All scoring inputs come exclusively from tables already tracked and persisted by the `meshcore-bot` submodule. No community-side learning, no new tables, no background observers.

**Goal 2 — 0-1 delivery score.** Each bot computes a single `delivery_score ∈ [0, 1]` that represents the likelihood of a response from _this bot_ successfully reaching the sender, given known mesh topology and this bot's proximity to that sender. That score is sent to the coordinator as the primary bidding field. The bot with the highest score responds; others suppress.

**Goal 3 — Prevent local feeder bias.** A bot sitting next to a busy private repeater should not outbid a more distant bot with better backbone connectivity. Infrastructure quality must balance raw hop count.

**Goal 4 — Prevent score inflation over time.** As the network grows and data accumulates, scores should remain discriminating. A local feeder that serves 3 nodes should not eventually score as high as backbone infrastructure simply because observation counts increase.

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
- Normalized against total distinct nodes in the network to prevent inflation.
- Provides: `infrastructure_score`.

### Live message fields (not DB)

- **`message.hops`** — inbound hop count on the arriving packet. Used as a fallback when `out_path_len` is unknown and as a cross-check when both are available.
- **`message.path`** — the literal path string (e.g. `"98,11,A4 (2 hops via Flood)"`). Parsed into node prefixes for the `mesh_connections` fan-in query.

---

## Scoring Formula

```python
# Component 1 — proximity (hop count)
best_hops  = min(inbound_hops, outbound_hops)  # use whichever is available; min if both
hop_score  = max(0.0, 1.0 - best_hops * 0.35)  # 0 hops=1.0, 3 hops=0.0, saturates at 0

# Component 2 — infrastructure quality of inbound path
# For each node X in message.path, count distinct upstream nodes in mesh_connections.
# Normalize using log1p against total_nodes to prevent inflation as network grows.
# Log scale compresses power-law distribution so "good" nodes score meaningfully.
total_nodes      = COUNT(DISTINCT from_prefix) FROM mesh_connections
fan_in           = COUNT(DISTINCT from_prefix) WHERE to_prefix = X
infra_score_X    = log1p(fan_in) / log1p(total_nodes)
infrastructure   = mean(infra_score_X for X in path nodes)

# Component 3 — path reliability (confirmed observations to this sender)
path_reliability = min(1.0, observation_count / 20.0)  # saturates at 20

# Component 4 — path freshness (how recently was this path confirmed)
path_freshness   = exp(-age_hours / 6.0)  # half-life ~4h; ~5% at 18h; ~0 at 36h+

# Final score (unknown components default to 0.5 neutral)
delivery_score = hop_score        * 0.35
              + infrastructure    * 0.30
              + path_reliability  * 0.20
              + path_freshness    * 0.15
```

**Unknown inputs default to `0.5` (neutral) — missing data neither helps nor hurts.** Weights sum to 1.0. Path quality (infrastructure + reliability + freshness) collectively outweighs raw proximity (0.65 vs 0.35), ensuring that a close bot with poor infrastructure can be outbid by a more distant bot with confirmed backbone paths.

---

## Score Interpretation

| Score range | Meaning                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------- |
| 0.75 – 1.0  | Excellent: close proximity through well-connected backbone, confirmed and recently verified |
| 0.50 – 0.75 | Good: moderate distance with decent infrastructure, or close but unconfirmed/stale          |
| 0.25 – 0.50 | Poor: distant, stale, or through local/private feeders with low fan-in                      |
| 0.0 – 0.25  | Very poor: many hops through weak infrastructure with no confirmed path data                |

A score of exactly `0.5` means the bot has no data at all for this sender — pure neutral. Scores below 0.5 indicate known-bad paths (local feeder, stale, unreliable).

---

## Example Scenarios

### Scenario A: Backbone bot, 2 hops, confirmed recent path

- `best_hops = 2` → hop_score = `1 - 2×0.35` = **0.30**
- Path via A2(15/20 nodes) + 7C(14/20 nodes):
  - `log1p(15)/log1p(20) = 0.91`, `log1p(14)/log1p(20) = 0.89`
  - infrastructure = **0.90**
- `observation_count = 18` → reliability = `18/20` = **0.90**
- `age = 0.5h` → freshness = `exp(-0.5/6)` = **0.92**
- **Score: `0.30×0.35 + 0.90×0.30 + 0.90×0.20 + 0.92×0.15 = 0.71`**

### Scenario B: Edge bot, 1 hop through local feeder

- `best_hops = 1` → hop_score = `1 - 1×0.35` = **0.65**
- Path via F4(3/20 nodes):
  - `log1p(3)/log1p(20) = 0.46`
  - infrastructure = **0.46**
- `observation_count = 8` → reliability = `8/20` = **0.40**
- `age = 4h` → freshness = `exp(-4/6)` = **0.51**
- **Score: `0.65×0.35 + 0.46×0.30 + 0.40×0.20 + 0.51×0.15 = 0.49`**

**Winner: Scenario A (0.71 vs 0.49)** — the backbone bot at 2 hops beats the edge bot at 1 hop due to superior infrastructure quality.

### Scenario C: Unknown bot (new deployment, only hop count known)

- `best_hops = 1` → hop_score = **0.65**
- infrastructure = **0.5** (unknown)
- reliability = **0.5** (unknown)
- freshness = **0.5** (unknown)
- **Score: `0.65×0.35 + 0.5×0.30 + 0.5×0.20 + 0.5×0.15 = 0.55`**

**Result:** Unknown bot (0.55) scores higher than known-bad bot (0.49) but lower than known-good bot (0.71). Neutral defaults allow graceful degradation.

---

## What Changes (File by File)

### Files Removed

| File                            | Reason                                                                                            |
| ------------------------------- | ------------------------------------------------------------------------------------------------- |
| `community/network_observer.py` | Replaced entirely by direct `mesh_connections` queries. No in-memory observation tracking needed. |

### Modified Files

#### `community/scoring_observer_config.ini`

Remove the `[NetworkObserver]` section entirely. The `[Scoring]` section contains configurable weight knobs:

```ini
[Scoring]
# Weight for hop-count component of delivery score
hop_weight = 0.35

# Weight for infrastructure quality of inbound path (mesh_connections fan-in)
infra_weight = 0.30

# Weight for path reliability (confirmed observations to this sender)
reliability_weight = 0.20

# Weight for path freshness (how recently the path was confirmed)
freshness_weight = 0.15

# Fallback parameters when coordinator is unreachable
base_delay_ms = 2000   # max delay window (ms)
min_delay_ms = 100     # floor delay even for best bot
max_jitter_ms = 200    # random jitter to break ties
```

All values overridable by `SCORING_*` env vars.

---

#### `community/config.py`

`ScoringConfig` updated with new weights:

```python
@dataclass
class ScoringConfig:
    """Configuration for delivery scoring and fallback timing."""
    hop_weight: float = 0.35         # proximity signal
    infra_weight: float = 0.30       # infrastructure quality of inbound path
    reliability_weight: float = 0.20  # how many confirmed paths to this sender
    freshness_weight: float = 0.15   # how recently the path was confirmed
    base_delay_ms: int = 2000
    min_delay_ms: int = 100
    max_jitter_ms: int = 200
    degrade_after_seconds: int = 3600
    degrade_target: float = 0.5
    degrade_window_seconds: int = 86400

    @classmethod
    def from_env_and_config(cls, config) -> "ScoringConfig":
        # Reads [Scoring] section + SCORING_* env var overrides
        ...
```

**Non-breaking:** `CoordinatorConfig` is unchanged.

---

#### `community/coordinator_client.py`

Renamed and expanded `compute_sender_proximity_score` → `compute_delivery_score`:

```python
@staticmethod
def compute_delivery_score(
    inbound_hops: Optional[int],
    outbound_hops: Optional[int],
    infrastructure: Optional[float],
    path_reliability: Optional[float],
    path_freshness: Optional[float],
    w_hops: float = 0.35,
    w_infra: float = 0.30,
    w_reliability: float = 0.20,
    w_freshness: float = 0.15,
) -> float:
    """Compute a delivery score ∈ [0, 1] for coordinator bidding.

    Unknown components default to 0.5 (neutral).
    hop_score = max(0, 1 - best_hops * 0.35)  [0 hops=1.0, 3 hops=0.0]
    """
    hops = [h for h in (inbound_hops, outbound_hops) if h is not None]
    hop_score = max(0.0, 1.0 - min(hops) * 0.35) if hops else 0.5

    infra = infrastructure   if infrastructure   is not None else 0.5
    rel   = path_reliability if path_reliability is not None else 0.5
    fresh = path_freshness   if path_freshness   is not None else 0.5

    return hop_score * w_hops + infra * w_infra + rel * w_reliability + fresh * w_freshness
```

`should_respond()` updated to accept 4 new params (`infrastructure`, `path_reliability`, `path_freshness`, weight params) and send `delivery_score` in the payload instead of `sender_proximity_score`. SNR and RSSI removed from payload — not used in scoring.

**Non-breaking:** All new params default to `None`.

---

#### `community/coverage_fallback.py`

Updated `compute_delay_ms_with_signal` and `wait_before_responding_with_signal` to accept `infrastructure`, `path_reliability`, `path_freshness` (replacing `path_significance`). Internally calls `compute_delivery_score` with the updated weights.

---

#### `community/message_interceptor.py`

Major changes:

1. **Removed `network_observer` parameter** and `handle_rf_log_data` patch. No in-memory path observation.

2. **Replaced `_get_path_metrics`** with 4-output version querying submodule tables:

```python
async def _get_path_metrics(self, message) -> tuple[Optional[int], Optional[float], Optional[float], Optional[float]]:
    """Returns (outbound_hops, infrastructure, path_reliability, path_freshness)."""

    # Query 1: outbound_hops from complete_contact_tracking
    rows = await self.bot.db_manager.aexecute_query(
        """SELECT out_path_len FROM complete_contact_tracking
           WHERE public_key LIKE ? AND out_path_len IS NOT NULL
           ORDER BY last_heard DESC LIMIT 1""",
        (sender_prefix8 + "%",), fetch=True,
    )

    # Query 2: infrastructure from mesh_connections (log1p / total_nodes)
    rows = await self.bot.db_manager.aexecute_query(
        f"""SELECT to_prefix,
                   COUNT(DISTINCT from_prefix) AS fan_in,
                   (SELECT COUNT(DISTINCT from_prefix) FROM mesh_connections) AS total_nodes
            FROM mesh_connections
            WHERE to_prefix IN ({placeholders})
            GROUP BY to_prefix""",
        tuple(node_lower), fetch=True,
    )
    if rows:
        total_nodes = max(rows[0][2] or 1, 1)
        log_total = math.log1p(total_nodes)
        scores = [math.log1p(r[1] or 0) / log_total for r in rows]
        infrastructure = sum(scores) / len(scores)

    # Query 3: reliability + freshness from observed_paths
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
        path_freshness = math.exp(-(age_hours or 999) / 6.0)

    return outbound_hops, infrastructure, path_reliability, path_freshness
```

**Key:** Infrastructure uses `log1p(fan_in) / log1p(total_nodes)` normalization. This prevents inflation — a local feeder that serves 3 nodes scores ~0.36 in a 20-node network and ~0.24 in a 100-node network. It cannot inflate to 1.0 simply because observation counts increase.

3. **Updated `_coordinated_send_response`** to unpack the 4-tuple and pass to `compute_delivery_score` and `should_respond`.

**Non-breaking:** `_get_path_metrics` only called internally.

---

#### `community/community_core.py`

- Removed `NetworkObserver` import and construction
- Wire `ScoringConfig` to `CoverageFallback`
- Do not pass `network_observer` to `MessageInterceptor`

---

#### `community/commands/scoring_command.py`

Updated to query `mesh_connections` directly with log1p normalization:

```python
rows = await self.bot.db_manager.aexecute_query(
    """SELECT to_prefix,
              COUNT(DISTINCT from_prefix) AS fan_in,
              (SELECT COUNT(DISTINCT from_prefix) FROM mesh_connections) AS total_nodes
       FROM mesh_connections
       GROUP BY to_prefix
       ORDER BY fan_in DESC LIMIT 5""",
    fetch=True,
)
total_nodes = max(rows[0][2] or 1, 1)
log_total = math.log1p(total_nodes)

for rank, (node_id, fan_in, _) in enumerate(rows, start=1):
    score = math.log1p(fan_in or 0) / log_total
    print(f"{rank}. {node_id.upper()} {score:.2f} (feeders={fan_in}/{total_nodes})")
```

Displays `feeders=15/20` style output so operators can see the raw fraction.

---

## Implementation Summary

| File                                    | Change                                                               |
| --------------------------------------- | -------------------------------------------------------------------- |
| `community/scoring_observer_config.ini` | Updated weights; removed `[NetworkObserver]` section                 |
| `community/config.py`                   | Updated `ScoringConfig` fields and defaults                          |
| `community/coordinator_client.py`       | Renamed/expanded scoring function; updated payload                   |
| `community/coverage_fallback.py`        | Updated function signatures for 4-component scoring                  |
| `community/message_interceptor.py`      | New 4-output `_get_path_metrics` with 3 DB queries; removed observer |
| `community/community_core.py`           | Removed `NetworkObserver` wiring                                     |
| `community/commands/scoring_command.py` | Query `mesh_connections` directly with log1p normalization           |
| `community/network_observer.py`         | **Deleted**                                                          |
| `meshcore-bot/`                         | **No changes**                                                       |

---

## Non-Breaking Guarantees

- All existing commands unchanged
- `should_respond()` new params default to `None`
- DB queries gracefully handle missing tables/data (return `None` → neutral 0.5 scoring)
- Coordinator protocol compatible (new `delivery_score` field; old field ignored)
- Submodule untouched

---

## Key Benefits

1. **No local feeder bias** — fan-in naturally low for private repeaters
2. **Anti-inflation** — normalization to `total_nodes` prevents saturation over time
3. **Freshness tracking** — stale paths explicitly penalized (exponential decay)
4. **Per-message accuracy** — infrastructure scored from actual inbound path, not bot-level cache
5. **Simpler** — no in-memory observers, no background threads, just 3 SQL queries per command message

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
