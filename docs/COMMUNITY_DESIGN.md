# Best Delivery Potential Scoring

This document reflects the code to design in `community/`.

## Rationale: Why Incoming Data Is Used

The scoring system is designed to select the bot most likely to transmit reliably back to the original sender. A reliable mesh-based feedback mechanism was not identified to directly assess outgoing message effectiveness on channels. While tracking heard repeats was considered, it is highly dependent on a bot radio’s location; a colocated repeater may be the only path in or out, making this metric unreliable. As a result, all scoring and analysis is based on the bot observing mesh traffic, tracking repeater links from paths, and receiving incoming messages.

Incoming data is used as a proxy for delivery potential, under the assumption that if a bot receives messages well from a sender, it likely has a good return path. While mesh topology can be asymmetric and incoming quality does not always guarantee outgoing reliability, this is the best available proxy given current mesh protocol constraints.

**Limitations:**

- Outgoing effectiveness cannot be measured directly; scoring relies on incoming path quality, signal, and activity as correlated indicators.
- Asymmetric links or mesh routing quirks may cause occasional mismatches, but the scoring logic is as optimal as possible given available data.

## Analogy: Relay Race and Harmonic Mean

Think of the delivery score like a relay race: every runner (node) must be fast for the team to win. If even one runner is slow, the team’s time suffers. The scoring system uses the harmonic mean, which penalizes weak links much more than the arithmetic mean. This ensures the bot chooses the most reliable, robust paths—not just those with a few strong nodes.

**Key Points:**

- Strict reliability: The score rewards paths where every node is strong. One weak node sharply reduces the score.
- Harmonic mean effect: Unlike a simple average, the harmonic mean is sensitive to low values. Even if most nodes are excellent, a single weak node drags the score down.
- Full score is rare: Only paths where all nodes are highly connected and recent will approach the maximum score.
- Purpose: This ensures the bot chooses the most reliable, robust paths—not just those with a few strong nodes.

## Scoring as Proxy for Delivery Potential

The scoring formula is optimized for the right variables given the constraints:

- **Infrastructure and proximity** are dominant, maximizing delivery reliability and minimizing path length.
- **Historical and recent sender contact** are secondary, rewarding familiarity and activity.
- Fallback values are balanced to avoid penalizing bots with incomplete data.

This approach is justified and the best practical method for the goal of reliable message delivery, given the lack of outgoing feedback.

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
  + freshness * w_freshness
)
```

Default weights from `ScoringConfig`:

- `infrastructure_weight = 0.40`
- `hop_weight = 0.35`
- `path_bonus_weight = 0.15`
- `freshness_weight = 0.10`

### Path Metrics (CoordinatorScoring)

Implemented in `CoordinatorScoring.get_path_metrics()` called from `MessageInterceptor`:

- Returns tuple `(hop_score, infrastructure, path_bonus, freshness)`.
- Path nodes parsed from `message.path` (CSV or 'Direct').

Hop score:

- If `message.hops` is known, compute `1 / (1 + hops)`.
- If unknown, return neutral `0.5`.

Infrastructure details:

- For direct (hops == 0):
  - Use `message.snr` and `message.rssi` (normalized and blended).
  - SNR normalized: $((snr + 15) / 30)$, RSSI normalized: $((rssi + 120) / 90)$, blended: $snr\_score * 0.7 + rssi\_score * 0.3$
  - If missing, default to `0.5` for each.
  - **No min_signal_score threshold is applied for direct paths.**
- For relayed:
  - Query fan-in per path node from `mesh_connections`.
  - Deduplicate ambiguous prefix matches to prevent data from like prefix nodes from skewing scores, handle multi-byte transition.
  - Normalize each node: $log1p(fan\_in) / log1p(max\_fan\_in)$.
  - Combine node scores with harmonic mean (penalizes weak links).
  - If signal quality (blended SNR/RSSI) is below `min_signal_score`, infrastructure score is halved.
  - If no infrastructure rows are found, value defaults to `0.5`.

Path bonus:

- For direct (hops == 0): always `0.0` (no path familiarity bonus for direct).
- For relayed: Query `message_stats` for exact sender+path match. If found, `1.0`, else `0.0`.

Freshness:

- Query latest message timestamps for sender in `message_stats` (grouped by 5-minute intervals, up to 5 messages).
- Compute $exp(-age\_hours / 24.0)$ for each, sum and scale, cap at 1.0.
- If unavailable, fallback to `complete_contact_tracking` for last heard time.
- If unknown, return `0`.

### Component specific details:

- `hop_score = 1 / (1 + inbound_hops)` (or `0.5` if unknown)
- `infrastructure`:
  - For direct (hops == 0): computed from SNR and RSSI (see above)
  - For relayed: harmonic mean of normalized fan-in per path node, with ambiguous prefix deduplication
  - If signal quality is below threshold, infrastructure is halved
  - If unknown: `0.5`
- `path_bonus`:
  - For unknown or direct (hops == 0): always `0.0`
  - For relayed: `1.0` if exact sender+path match in `message_stats`, else `0.0`
- `freshness`: sum of recency scores, capped at 1.0, fallback to contact tracking if needed

## Data Sources

Message fields:

- `message.hops`
- `message.path`
- `message.snr` (for direct)
- `message.rssi` (for direct)

DB tables:

- `mesh_connections` for infrastructure fan-in
- `message_stats` for exact sender+path match (path bonus) and sender recency (freshness)
- Fallback: `complete_contact_tracking` for freshness based on advert if `message_stats` unavailable

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
- `min_signal_score`

## Why Full Scores Are Rare: Simple Explanation

The scoring system is strict: it only gives full marks when every node in the path is strong. If even one node is weak, the score drops a lot. This helps avoid unreliable routes and ensures the best-connected repeaters are prioritized. See the relay race and harmonic mean analogy in the "Rationale: Why Incoming Data Is Used" section above for further context.

## Web Viewer Community Page

### Summary

The Web Viewer Community Page provides a real-time dashboard for monitoring bot coordination, delivery scoring, and repeater significance to this bot. It lists coordination decisions, fallback events, and suppression events (when bots are silenced due to low delivery score in fallback mode).

### Implementation Overview

- Runs as a Flask+SocketIO web UI in its own thread (see `community/web_viewer_community_page.py`).
- Receives coordination and DM events published from `MessageInterceptor`.
- Top Repeaters section shows bots with highest delivery potential to this bot, based on meshcore-bot monitoring data and estimated score through this infrastructure.
- Displays live scoring breakdowns, bot assignments, and fallback/suppression events.
- Integrates with the meshcore-bot web viewer for unified channel and bot status.
- Shows per-message delivery score components (infrastructure, hop, path bonus, freshness).
- Allows community members to audit coordination decisions and delivery potential in real time.

## Future Improvement Option (not initial implementation): Bidirectional Bonus Scoring

**Overview:**
Add a bidirectional bonus to the delivery scoring equation, rewarding bots whose path segments are bidirectional (i.e., both directions observed in mesh graph). This is a base meshcore-bot function in the mesh graph monitoring, however a bi-directional result has yet to be seen. This would enhance the scoring model by giving a boost to bots with assumed stronger two-way connectivity, which is a key indicator of reliable delivery potential.

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
