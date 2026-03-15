# Best Delivery Potential Scoring

This document reflects the code to design in `community/`.

## Scope

- Applies only to community layer code.
- `meshcore-bot/` submodule is not modified.
- Main implementation files:
  - `community/message_interceptor.py`
  - `community/coordinator_client.py`
  - `community/coverage_fallback.py`
  - `community/coordinator_scoring.py`
  - `community/config.py`
- Some files may already exist if included from past attempts.
- Existing API data should be maintained for backward compatibility and all optional fields provided where possible
- Secondary files:
  - `community/web_viewer_community_page.py` for the web viewer dashboard.
  - `community/web_viewer_patch.py` for any necessary patches to the meshcore-bot web viewer.
  - `community/web_viewer_packet_stream.py` publish events for web viewer.

## Scoring Model

Delivery score is computed in `CoordinatorScoring.compute_delivery_score()`:

```python
delivery_score = (
  infrastructure * w_infrastructure
  + hop_score * w_hops
  + path_bonus * w_path_bonus
  + path_freshness * w_freshness
)
```

Default weights from `ScoringConfig`:

- `infrastructure_weight = 0.40`
- `hop_weight = 0.35`
- `path_bonus_weight = 0.15`
- `freshness_weight = 0.10`

### Path Metrics

Implemented in `CoordinatorScoring.get_path_metrics()` called from `MessageInterceptor`:

- Returns tuple `(hop_score, infrastructure, path_bonus, path_freshness)`.
- Path nodes parsed from `message.path` via `parse_path_nodes()`.
- `path_hex` for exact match is built from parsed nodes (`"".join(path_nodes).lower()`).

Hop score:

- If `message.hops` is known, compute `1 / (1 + hops)`.
- If unknown, return neutral `0.5`.

Infrastructure details:

- For direct (hops == 0):
  - Use `message.snr` and `message.rssi` (normalized and blended).
  - If missing, default to `0.5` for each.
- For relayed:
  - Query fan-in per path node: `COUNT(DISTINCT from_prefix)` grouped by `to_prefix`.
  - Normalize each node: `log1p(fan_in) / log1p(max_fan_in)`.
  - Build per-path node scores; missing node rows use neutral `0.5`.
  - Combine node scores with harmonic mean.
  - If no infrastructure rows are found, value remains `None` (later treated as `0.5`).

Exact path bonus:

- For direct (hops == 0): always `0.0` (no path familiarity bonus for direct).
- For relayed: Query `observed_paths` for `LOWER(from_prefix)`, `LOWER(path_hex)`, and `packet_type='message'`. Exact row found => `1.0`, else `0.0`.

Freshness:

- Query latest `last_seen` for sender in `observed_paths` with `packet_type='message'`.
- Compute `exp(-age_hours / 24.0)`. If no observations, return `0.5`.

### Component specific details:

- `hop_score = 1 / (1 + inbound_hops)` (or `0.5` if unknown)
- `infrastructure`:
  - For direct (hops == 0): computed from SNR and RSSI:
    - SNR normalized to [0,1] from -15 to +15 dB: `(snr + 15) / 30`
    - RSSI normalized to [0,1] from -120 to -30 dBm: `(rssi + 120) / 90`
    - Blended: `infra_score = snr_score * 0.7 + rssi_score * 0.3`
  - For relayed: harmonic mean of normalized fan-in per path node (see below)
  - If unknown: `0.5`
- `path_bonus`:
  - For unknown or direct (hops == 0): always `0.0`
  - For relayed: `1.0` if exact sender+path match in `observed_paths`, else `0.0`
- `path_freshness = exp(-age_hours / 24.0)` or `0.5` if unknown

## Data Sources

Message fields:

- `message.hops`
- `message.path`
- `message.snr` (for direct)
- `message.rssi` (for direct)

DB tables:

- `mesh_connections` for infrastructure fan-in
- `observed_paths` for exact-path bonus and freshness

Not used for delivery scoring:

- `complete_contact_tracking.out_path_len`
- `observed_paths.observation_count`

## Coordination Flow

In `MessageInterceptor`, coordination is enforced for both `send_response` and `send_channel_message`:

1. Direct messages (DMs) bypass coordination and are sent immediately.
2. If coordinator is not configured, send immediately.
3. For channel messages:
   - Channel 'commands' (triggered by command prefix) are coordinated in `send_response`.
   - Channel 'keywords' (triggered by keyword match) are coordinated in `send_channel_message` as meshcore-bot shortcuts past `send_response` for these.
   - A flag is used to prevent double coordination on channel commands (so a command response does not coordinate again upon reaching `send_channel_message`).
   - For both, delivery score through path metrics are computed, and a bid sent to the coordinator (`POST /api/v1/coordination/should-respond`).
   - Coordinator will assess for the highest delivery potential bot and respond with instructions.
   - If assigned to this bot, respond.
   - If assigned to another bot, suppress local response.
   - If coordinator unreachable, use delivery score aware fallback delay.
4. All channel messages, whether triggered by commands or keywords, are subject to the same coordination and scoring logic, but only once per message.

## Fallback Behavior

In `CoverageFallback.compute_delay_ms_with_signal()`:

- Compute per-message delivery score with the same formula/weights as coordinator bid.
- Use `CoordinatorScoring` to compute delivery score for fallback as well.
- If `delivery_score < fallback_min_delivery_score`, suppress response in fallback mode.
- Convert delivery score directly to delay (`base_delay_ms`, `min_delay_ms`, `max_jitter_ms`).
- Higher delivery score = shorter delay, so higher delivery potential bot responds first.

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

## Web Viewer Community Page

### Summary

The Web Viewer Community Page provides a real-time dashboard for monitoring bot coordination, delivery scoring, and repeater significance to this bot. It lists coordination decisions and fallback events.

### Implementation Overview

## Why Full Scores Are Rare: Simple Explanation

The scoring system is strict: it only gives full marks when every node in the path is strong. If even one node is weak, the score drops a lot. This helps us avoid unreliable routes and ensures the best-connected repeaters are prioritized.

**Analogy:**
Think of the delivery score like a relay race: every runner (node) must be fast for the team to win. If even one runner is slow, the team’s time suffers. The scoring system uses the harmonic mean, which penalizes weak links much more than the arithmetic mean.

**Key Points:**

- Strict reliability: The score rewards paths where every node is strong. One weak node sharply reduces the score.
- Harmonic mean effect: Unlike a simple average, the harmonic mean is sensitive to low values. Even if most nodes are excellent, a single weak node drags the score down.
- Full score is rare: Only paths where all nodes are highly connected and recent will approach the maximum score.
- Purpose: This ensures the bot chooses the most reliable, robust paths—not just those with a few strong nodes.

- Runs as a Flask+SocketIO web UI in its own thread (see `community/web_viewer_community_page.py`).
- Receives coordination and DM events published from `MessageInterceptor`.
- Top Repeaters section shows bots with highest delivery potential to this bot, based on meshcore-bot monitoring data and estimated score through this infrastructure.
- Displays live scoring breakdowns, bot assignments, and fallback/suppression events.
- Integrates with the meshcore-bot web viewer for unified channel and bot status.
- Shows per-message delivery score components (infrastructure, hop, path bonus, freshness).
- Allows community members to audit coordination decisions and delivery potential in real time.

## Future Improvement Option (not initial implementation): Bidirectional Bonus Scoring

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
