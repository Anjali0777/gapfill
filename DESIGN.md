# Design: Gapfill

## Problem

An exchange publishes every order event (add, cancel, execute, delete) as a sequenced stream over UDP multicast. Each message has a sequence number; packets carry runs of consecutive messages. A trading system rebuilds the exchange's order book from that stream. The book is only correct if every message is applied exactly once, in order. One missed cancel and the book shows liquidity that isn't there.

UDP drops, duplicates and reorders packets. During an incident it can black out for seconds. On 15 October 2026, Nasdaq reported a network disruption at its Carteret facility causing intermittent data gaps across its US equities markets for about three hours. It pointed clients to its Chicago feeds and asked those who lost data to submit recovery requests.

So the engineering problem is: **detect gaps, fetch what's missing, and get back to a correct, current book as fast as possible, without flooding the shared recovery service.**

## Constraints

- **Correctness above all:** the final book must equal the exchange's, and messages must be applied in strict sequence order.
- **Freshness:** time spent stale (behind the live feed) is time you can't trade safely.
- **Shared recovery capacity:** retransmission and snapshot servers are shared by every client, so asking for data you already have hurts everyone. In the model, the server has a fixed bandwidth in messages per tick, and snapshots and retransmissions draw from the same budget.
- **Reordering is not loss:** a packet that arrives one tick late shouldn't trigger a retransmission.
- **The tail matters:** if the last packets before the feed goes quiet are lost, nothing later arrives to reveal the gap. Heartbeats carrying the latest sequence number are the only signal.

## Approaches considered

### 1. Rebuild from message 1 on any gap (implemented: `FullReplay`)

On a hole, discard the book, request the whole day's messages from the replay service, and buffer live packets until the replay catches up.

- Simple, and correct by construction: nothing from the broken state survives.
- Cost per gap is O(messages so far today), so a gap at 3 pm costs far more than one at 9:31.
- Under steady low-level loss there's always another gap by the time a rebuild finishes. The consumer spends most of the session rebuilding. At 0.1% packet loss, it's in sync 8% of the time and fetches 3.9× the day's message volume from recovery.

### 2. Range gap-fill (implemented: `GapFill(snapshots=False)`)

Request only the missing sequence ranges.

- Holes are tracked as ranges in an `IntervalSet`. A lost packet is one range of 20 messages; a blackout is one range of 30,000. Bookkeeping is proportional to the number of gaps, not the number of lost messages.
- A **reorder buffer** holds messages that arrive ahead of a hole and drains them as soon as the hole fills.
- **One-tick grace:** a hole is requested only if it's still open at the next tick, so reordered packets don't cause retransmissions.
- Adjacent holes are merged into one request, and an in-flight range is never requested again.
- Cost per gap is O(gap size). That's ideal for small gaps, but a 30,000-message blackout still means fetching 30,000 messages.

### 3. Range gap-fill plus snapshot fallback (implemented: `GapFill`, chosen)

The exchange keeps periodic book snapshots, every 5,000 messages in the model. When the outstanding holes add up to more than about 1.5× the current book size, request the latest snapshot instead.

- Restore the book from the snapshot and continue from its sequence number.
- Only the short stretch between the snapshot and buffered live data still needs range requests.
- A blackout then costs about the book size (around 4,500 resting orders) plus at most one snapshot interval, regardless of how long the blackout lasted.

### 4. Arbitrate between redundant feeds (A/B lines)

Real exchanges publish the same stream on two independent network paths, and clients take whichever copy of each packet arrives first.

- This removes most random loss for free and is standard practice.
- It doesn't help when both paths share a failure. Nasdaq's Carteret disruption hit all three of its US equities markets, which is why it redirected clients to Chicago.
- Complementary, so it isn't modelled. It would sit in front of this recovery layer.

## Decision

Range gap-fill with snapshot fallback. From `results/bench.md` (600,000 messages; a 30,000-message blackout plus 0.1% random loss):

| | Full replay | Range gap-fill | Gap-fill + snapshot |
|---|---:|---:|---:|
| Recovery messages fetched | 2,232,720 | 30,500 | **4,962** |
| Ticks in sync | 10.8% | 96.2% | **97.7%** |
| Ticks to recover after the blackout | 959 | 21 | **3** |
| p99 staleness (messages) | 446,500 | 19,500 | **380** |

## Complexity

| | Full replay | Gap-fill (+ snapshot) |
|---|---|---|
| Per gap | O(H), where H is messages so far | O(gap) for ranges, or O(B + S) with a snapshot (B = resting orders, S = snapshot interval) |
| Per message | O(1) apply + buffer | O(1) apply + buffer |
| Hole bookkeeping | none | O(log g) per update, where g is open ranges |
| Memory | buffered live packets during a rebuild, up to H | reorder buffer, bounded by the largest open gap |

## Tradeoffs accepted

- **Grace tick versus freshness.** Waiting one tick before requesting a hole means every drop costs at least one stale tick. At 1% packet loss that leaves the consumer in sync 76.5% of ticks, versus 96.8% at 0.1% loss. Without the grace period, every reordered packet would cause a needless retransmission. The right setting depends on the network's reorder profile; production systems tune it per feed, often in microseconds rather than ticks.
- **Wasted retransmissions after a snapshot.** If a snapshot lands while range requests are in flight, those ranges still arrive and get discarded. The model has no cancel message. A real implementation would send one.
- **Snapshot threshold.** Switching to a snapshot when holes exceed 1.5× the book size is a heuristic. A better rule would also count the replay stretch after the snapshot, and the server's queue depth.
- **Trust in snapshots.** A snapshot is the exchange's word for the book at sequence S. If snapshots were ever inconsistent with the stream, this design would inherit the error. Full replay never would. That's the one property the baseline has that the chosen design gives up.

## Failure modes

| Failure | Handling |
|---|---|
| Lost packets at the end of the session | Heartbeats carry the latest sequence number, so the tail gap is detected. A property test found exactly this bug in the baseline |
| Duplicate packets | Ignored: anything below `expected` or already buffered is dropped |
| Reordered packets | Buffered; no retransmission unless the hole is still open next tick |
| Snapshot older than the consumer's position | Ignored, then falls back to range requests |
| A message references an unknown order | `OrderBook.apply` raises instead of guessing. In the strategies here that can't happen; in production it would trigger a resync |
| Recovery server overloaded | Requests are coalesced and never duplicated, so the client asks for the minimum it needs |

## At 10× and 100× scale

- **10× rate (US equities peaks run to millions of messages a second):** Python is the bottleneck, at about 4 µs per message here. The same design in C++, Rust or Java with pre-allocated buffers and an array-indexed order map reaches tens of millions of messages a second. Hole tracking stays negligible because it scales with gaps, not messages.
- **Many symbols and feeds:** recovery state is per feed, so it's naturally sharded. Snapshots can be requested per symbol, so a blackout on one partition doesn't force a full-feed snapshot.
- **100× clients during an incident:** this is the "everyone asks at once" problem, so the recovery service needs admission control. Clients should add jitter to their requests and prefer snapshots for big gaps, as this design does, so the service sends B orders per client instead of the whole blackout.
