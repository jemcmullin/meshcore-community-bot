# Scoring Scenarios in Mesh Coordination

This document illustrates how the delivery scoring system works in a mesh network and why it is effective for coordinated message delivery.

---

## Scoring Formula and Design Choices

Delivery score is computed as a weighted sum of four components:

- **Infrastructure:** Quality of radio link or relay connections/fan-in (SNR/RSSI for direct, mesh_connections for relayed, with signal quality threshold downgrade)
- **Hop score:** Favors fewer hops ($1/(1+\text{hops})$)
- **Path bonus:** 1.0 if sender+path is known, 0.0 otherwise
- **Freshness:** Recency decay from observed paths ($\exp(-\text{age}_\text{hours}/24)$)

**Weights (default):**

- Infrastructure: 0.40
- Hop: 0.35
- Path bonus: 0.15
- Freshness: 0.10

**Key influences:**

- Signal quality minimum threshold: Bots with poor SNR/RSSI on first hop are downgraded (infra score halved)
- Path fan-in: Harmonic mean rewards consistently well-connected relays, not just one highly connected/fan-in node
- Ambiguous prefix handling: Handling of mixed byte prefix data and overlapping.
- Fallback: If signal quality is below threshold, infrastructure is reduced
- Freshness: Sender recency decay, capped and grouped to avoid rapid activity bias
- Path bonus: Only exact sender+path matches in history

$$
	ext{delivery\_score} = \text{infrastructure} \times 0.40 + \text{hop\_score} \times 0.35 + \text{path\_bonus} \times 0.15 + \text{freshness} \times 0.10
$$

---

## Summary

- Direct bots are prioritized for best delivery, but signal quality threshold can downgrade poor links.
- Well-connected relays with proven paths are rewarded, but less than direct.
- Poorly-connected relays are deprioritized, preventing low-quality or redundant responses.
- Signal quality scores direct channel messages and is used to downrank relayed bots with potentially poor first-hop signal.
- Freshness decay ensures recency and reliability are considered.
- The scoring system ensures optimal message delivery, minimizes noise, and leverages mesh reliability.

---

## Scenario 1: Direct-hearing bot (Good Signal)

**Message:** Direct path, sender is nearby.
**SNR:** -5 (poor), **RSSI:** -110 (weak)
**Hops:** 0
**Path:** "Direct"
**Path bonus:** 0.0
**Freshness:** 0.5
**Note:** For direct paths, the min_signal_score threshold is not used. Signal quality is blended directly.
**Scores:**

- Infrastructure: $((12+15)/30) = 0.9$ (SNR normalized), $((-45+120)/90) = 0.83$ (RSSI normalized), blended: $0.9*0.7 + 0.83*0.3 = 0.89$
- Hop score: $1/(1+0) = 1.0$
- Path bonus: $0.0$
- Freshness: $0.5$

**Delivery score:**

$$
0.89 \times 0.40 + 1.0 \times 0.35 + 0.0 \times 0.15 + 0.5 \times 0.10 = 0.356 + 0.35 + 0 + 0.05 = 0.756
$$

**Benefit:** Direct bots are strongly favored, ensuring the best possible delivery for messages heard firsthand.

---

## Scenario 2: Direct-hearing bot (Poor Signal, Threshold Downgrade)

- **Message:** Direct path, sender is nearby.
- **SNR:** -5 (poor), **RSSI:** -110 (weak)
- **Hops:** 0
- **Path:** "Direct"
- **Path bonus:** 0.0
- **Freshness:** 0.5

**Scores:**

- Infrastructure: SNR normalized: $((-5+15)/30) = 0.33$, RSSI normalized: $((-110+120)/90) = 0.11$, blended: $0.33 \times 0.7 + 0.11 \times 0.3 = 0.26$
- Hop score: $1.0$
- Path bonus: $0.0$
- Freshness: $0.5$

**Delivery score:**

$$
0.26 \times 0.40 + 1.0 \times 0.35 + 0.0 \times 0.15 + 0.5 \times 0.10 = 0.104 + 0.35 + 0 + 0.05 = 0.504
$$

**Benefit:** Even though the bot hears the message directly, poor signal quality reduces its delivery score. Relays with better infrastructure may win coordination if their score is higher.

---

## Scenario 3: Well-connected relay bot (Fan-in, Path Bonus, Freshness)

- **Message:** Relayed path "X1,X2,X3"
- **Infrastructure:** $0.8$ (high fan-in, deduplicated)
- **Hops:** 2
- **Path bonus:** 1.0 (seen sender+path before)
- **Freshness:** 0.8 (recently seen)

**Scores:**

- Infrastructure: $0.8$
- Hop score: $1/(1+2) = 0.33$
- Path bonus: $1.0$
- Freshness: $0.8$

**Delivery score:**

$$
0.8 \times 0.40 + 0.33 \times 0.35 + 1.0 \times 0.15 + 0.8 \times 0.10 = 0.32 + 0.1155 + 0.15 + 0.08 = 0.6655
$$

**Benefit:** Relays with strong infrastructure and proven path reliability are rewarded, but not as much as direct bots.

---

## Scenario 4: Poorly-connected relay bot (Low Fan-in, No Path Bonus)

- **Message:** Relayed path "Y1,Y2"
- **Infrastructure:** $0.3$ (low fan-in)
- **Hops:** 2
- **Path bonus:** 0.0 (never seen sender+path)
- **Freshness:** 0.2 (not seen recently)

**Scores:**

- Infrastructure: $0.3$
- Hop score: $0.33$
- Path bonus: $0.0$
- Freshness: $0.2$

**Delivery score:**

$$
0.3 \times 0.40 + 0.33 \times 0.35 + 0.0 \times 0.15 + 0.2 \times 0.10 = 0.12 + 0.1155 + 0 + 0.02 = 0.2555
$$

**Benefit:** Bots with weak infrastructure and no path history are deprioritized, reducing redundant or unreliable responses.

---

## Scenario 5: Relay bot with signal quality downgrade (Threshold)

- **Message:** Relayed path "Q1,Q2"
- **Infrastructure:** $0.6$ (fan-in), signal_score $0.2$ (below threshold)
- **Hops:** 1
- **Path bonus:** 0.0
- **Freshness:** 0.5

**Scores:**

- Infrastructure: $0.6 \times 0.5 = 0.3$ (downgraded)
- Hop score: $0.5$
- Path bonus: $0.0$
- Freshness: $0.5$

**Delivery score:**

$$
0.3 \times 0.40 + 0.5 \times 0.35 + 0.0 \times 0.15 + 0.5 \times 0.10 = 0.12 + 0.175 + 0 + 0.05 = 0.345
$$

**Benefit:** Relays with poor signal quality on first hop are penalized, even if fan-in is moderate.

---

## Scenario 6: Sender freshness decay (Stale sender)

- **Message:** Relayed path "V1,V2"
- **Infrastructure:** $0.75$
- **Hops:** 1
- **Path bonus:** 1.0
- **Freshness:** 0.1 (sender last seen days ago)

**Scores:**

- Infrastructure: $0.75$
- Hop score: $0.5$
- Path bonus: $1.0$
- Freshness: $0.1$

**Delivery score:**

$$
0.75 \times 0.40 + 0.5 \times 0.35 + 1.0 \times 0.15 + 0.1 \times 0.10 = 0.3 + 0.175 + 0.15 + 0.01 = 0.635
$$

**Benefit:** Even with a known path, stale freshness reduces delivery score, favoring bots with more recent sender observations.

---

## Scenario 7: Fallback freshness (No message_stats, uses complete_contact_tracking)

- **Message:** Relayed path "F1,F2"
- **Infrastructure:** $0.5$
- **Hops:** 1
- **Path bonus:** 0.0
- **Freshness:** 0.3 (from fallback contact tracking, likely last advert)

**Scores:**

- Infrastructure: $0.5$
- Hop score: $0.5$
- Path bonus: $0.0$
- Freshness: $0.3$

**Delivery score:**

$$
0.5 \times 0.40 + 0.5 \times 0.35 + 0.0 \times 0.15 + 0.3 \times 0.10 = 0.2 + 0.175 + 0 + 0.03 = 0.405
$$

**Benefit:** Fallback ensures bots can still score freshness even if primary stats are unavailable.
