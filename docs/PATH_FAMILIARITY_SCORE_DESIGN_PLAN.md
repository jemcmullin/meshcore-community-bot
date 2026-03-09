# Path-Familiarity Delivery Scoring (Implemented)

This document reflects the code currently running in `community/`.

## Scope

- Applies only to community layer code.
- `meshcore-bot/` submodule is not modified.
- Main implementation files:
  - `community/message_interceptor.py`
  - `community/coordinator_client.py`
  - `community/coverage_fallback.py`
  - `community/config.py`

## Live Scoring Model

Delivery score is computed in `CoordinatorClient.compute_delivery_score()`:

```python
delivery_score = (
    infrastructure * w_infrastructure
    + hop_score * w_hops
    + path_bonus * w_path_bonus
    + path_freshness * w_freshness
)
```

Component values:

- `hop_score = 1 / (1 + inbound_hops)` (or `0.5` if unknown)
- `infrastructure = computed` or `0.5` if unknown
- `path_bonus = 1.0` if exact sender+path match, else `0.0` (default `0.0` when unknown)
- `path_freshness = exp(-age_hours / 24.0)` or `0.5` if unknown

Default weights from `ScoringConfig`:

- `infrastructure_weight = 0.40`
- `hop_weight = 0.35`
- `path_bonus_weight = 0.15`
- `freshness_weight = 0.10`

## Data Sources

Message fields:

- `message.hops`
- `message.path`

DB tables:

- `mesh_connections` for infrastructure fan-in
- `observed_paths` for exact-path bonus and freshness

Not used for delivery scoring:

- `complete_contact_tracking.out_path_len`
- `observed_paths.observation_count`

## Path Metrics (Current Behavior)

Implemented in `MessageInterceptor._get_path_metrics()`:

- Returns tuple `(infrastructure, path_bonus, path_freshness)`.
- Path nodes parsed from `message.path` via `parse_path_nodes()`.
- `path_hex` for exact match is built from parsed nodes (`"".join(path_nodes).lower()`).

Infrastructure details:

- Query fan-in per path node: `COUNT(DISTINCT from_prefix)` grouped by `to_prefix`.
- Normalize each node: `log1p(fan_in) / log1p(max_fan_in)`.
- Build per-path node scores; missing node rows use neutral `0.5`.
- Combine node scores with harmonic mean.
- If no infrastructure rows are found, value remains `None` (later treated as `0.5`).

Exact path bonus:

- Query `observed_paths` for `LOWER(from_prefix)`, `LOWER(path_hex)`, and `packet_type='message'`.
- Exact row found => `1.0`, else `0.0`.

Freshness:

- Query latest `last_seen` for sender in `observed_paths` with `packet_type='message'`.
- Compute `exp(-age_hours / 24.0)`.

## Coordination Flow

In `MessageInterceptor._coordinated_send_response()`:

1. DMs bypass coordination and send immediately.
2. If coordinator is not configured, send immediately.
3. For channel replies, compute path metrics and delivery score.
4. Send bid to coordinator `POST /api/v1/coordination/should-respond` with `delivery_score`.
5. If assigned to this bot, respond.
6. If assigned to another bot, suppress local response.
7. If coordinator unreachable, use signal-aware fallback delay.

## Fallback Behavior

In `CoverageFallback.compute_delay_ms_with_signal()`:

- Compute per-message delivery score with the same formula/weights as coordinator bid.
- If `delivery_score < fallback_min_delivery_score`, suppress response in fallback mode.
- Convert delivery score directly to delay (`base_delay_ms`, `min_delay_ms`, `max_jitter_ms`).
- No blending with coordinator coverage score — old coverage API will be removed.
- Higher delivery score = shorter delay, so best-path bot responds first.

## Config Surface

`ScoringConfig` keys (from `config.ini` `[Scoring]` and `SCORING_*` env vars):

- `infrastructure_weight`
- `hop_weight`
- `path_bonus_weight`
- `freshness_weight`
- `base_delay_ms`
- `min_delay_ms`
- `max_jitter_ms`
- `fallback_min_delivery_score`
- `degrade_after_seconds`
- `degrade_target`
- `degrade_window_seconds`

## Future Improvement Option: Bidirectional Bonus Scoring

**Overview:**
Add a bidirectional bonus to the delivery scoring equation, rewarding bots whose path segments are confirmed bidirectional (i.e., both directions observed in mesh graph).

**Proposed Equation:**
delivery_score = base_score + 0.10 \* bidirectional_score
Where `bidirectional_score` is the fraction of path segments with bidirectional edges (0 to 1).

**Rationale:**

- Bots with strong two-way connectivity are more likely to deliver messages reliably.
- The bonus differentiates candidates with robust mesh links, favoring those most likely to succeed.

**Scenario Example:**

- Bot A: All path segments bidirectional → bonus = 0.10
- Bot B: Half bidirectional → bonus = 0.05
- Result: Bot A wins the bid, improving delivery reliability.

**Implementation Notes:**

- Bonus is additive; does not disrupt existing scoring structure.
- Can be tuned or blended as a weighted term if desired.

**Status:**
Not yet implemented; recommended as a future enhancement for networks with asymmetric or lossy links.
