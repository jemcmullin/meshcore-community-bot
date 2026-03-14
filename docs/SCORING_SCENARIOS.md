# Scoring Scenarios in Mesh Coordination

This document illustrates how the delivery scoring system works in a mesh network and why it is effective for coordinated message delivery.

---

## Scenario 1: Direct-hearing bot (Bot C)

- **Message:** Direct path, sender is nearby.
- **SNR:** 12 (good), **RSSI:** -45 (strong)
- **Hops:** 0
- **Path:** "Direct"
- **Path bonus:** 0.0 (no relayed path)
- **Freshness:** 0.5 (default for unknown)
- **Weights:** infrastructure 0.40, hop 0.35, path_bonus 0.15, freshness 0.10

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

## Scenario 2: Well-connected relay bot (Bot A)

- **Message:** Relayed path "X1,X2,X3"
- **Infrastructure:** high (fan-in, mesh_connections)
- **Hops:** 2
- **Path bonus:** 1.0 (seen sender+path before)
- **Freshness:** 0.8 (recently seen)
- **Weights:** same as above

**Scores:**

- Infrastructure: $0.8$ (high fan-in)
- Hop score: $1/(1+2) = 0.33$
- Path bonus: $1.0$
- Freshness: $0.8$

**Delivery score:**

$$
0.8 \times 0.40 + 0.33 \times 0.35 + 1.0 \times 0.15 + 0.8 \times 0.10 = 0.32 + 0.1155 + 0.15 + 0.08 = 0.6655
$$

**Benefit:** Relays with strong infrastructure and proven path reliability are rewarded, but not as much as direct bots.

---

## Scenario 3: Poorly-connected relay bot (Bot B)

- **Message:** Relayed path "Y1,Y2"
- **Infrastructure:** low (fan-in, weak mesh_connections)
- **Hops:** 2
- **Path bonus:** 0.0 (never seen sender+path)
- **Freshness:** 0.2 (not seen recently)
- **Weights:** same as above

**Scores:**

- Infrastructure: $0.3$ (low fan-in)
- Hop score: $0.33$
- Path bonus: $0.0$
- Freshness: $0.2$

**Delivery score:**

$$
0.3 \times 0.40 + 0.33 \times 0.35 + 0.0 \times 0.15 + 0.2 \times 0.10 = 0.12 + 0.1155 + 0 + 0.02 = 0.2555
$$

**Benefit:** Bots with weak infrastructure and no path history are deprioritized, reducing redundant or unreliable responses.

---

## Summary

- Direct bots are prioritized for best delivery.
- Well-connected relays with proven paths are rewarded, but less than direct.
- Poorly-connected relays are deprioritized, preventing low-quality or redundant responses.
- The scoring system ensures optimal message delivery, minimizes noise, and leverages mesh reliability.
