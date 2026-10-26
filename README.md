# Gapfill

> Keeping an exchange order book correct on a market-data feed that drops packets: targeted gap-fill with snapshot fallback, measured against rebuild-from-scratch.

## What happened

On **15 October 2026**, Nasdaq reported a network disruption at its Carteret, New Jersey facility that caused **intermittent data gaps across all of its US equities markets**: Nasdaq, Nasdaq Texas and Nasdaq PSX.

- It started around 11:27–11:57 AM ET; reports differ on the exact time.
- It was resolved at about 2:25 PM ET.
- Nasdaq pointed clients to its unaffected Chicago feeds and asked anyone who lost data to submit recovery requests.

Coverage: [Reuters via Investing.com](https://www.investing.com/news/stock-market-news/nasdaq-reports-network-disruption-us-equities-data-feeds-impacted-4952331), [Financial News](https://www.financial-news.co.uk/nasdaq-network-disruption-hits-us-equities-data-feeds/), [GuruFocus](https://www.gurufocus.com/news/9126324/nasdaq-resolves-network-data-interruptions-impacting-us-equities-markets-ndaq).

This project is about what happens on the receiving end: a trading system that has to rebuild the order book from a feed with holes in it.

## The engineering problem

Exchange feeds such as Nasdaq ITCH send every order event (add, cancel, execute, delete) as sequenced messages over UDP multicast. A book built from them is only correct if **every message is applied exactly once, in order**. UDP drops, duplicates and reorders packets, and during an incident it blacks out entirely.

A consumer has to:

- **notice gaps**, including at the very end of the feed, where no later packet reveals them
- **tell reordering apart from loss**
- **fetch only what's missing** from a recovery service shared with every other client
- **get back to a correct, current book fast**

## Architecture

```
 exchange ──► packets (seq, msgs) ──► lossy network: drop / dup / reorder / blackout
     │                                         │
     │  log + snapshot every 5,000 msgs        ▼
     │                               ┌──────── consumer ────────┐
     │                               │ reorder buffer (seq→msg) │
     │                               │ holes: IntervalSet       │──► OrderBook.apply (strictly in order)
     │                               │ 1-tick grace, coalesce   │
     ▼                               └──────┬───────────────────┘
 recovery server (fixed msgs/tick) ◄────────┘ ("range", a, b) | ("snapshot",)
```

| File | What it is |
|---|---|
| `gapfill/book.py` | Market-by-order book with O(1) apply, snapshot and restore |
| `gapfill/feed.py` | Synthetic order flow and the exchange, which keeps a log and periodic snapshots |
| `gapfill/consumer.py` | `FullReplay` (baseline) and `GapFill` (range gap-fill, optionally with snapshot fallback) |
| `gapfill/intervals.py` | Missing sequence numbers stored as merged ranges |
| `gapfill/sim.py` | Tick-based simulator: lossy network, heartbeats, a bandwidth-limited recovery server, metrics |
| `bench/run.py` | Benchmark scenarios |
| `examples/demo.py` | A short session with a blackout, all three strategies |

Approaches considered, tradeoffs, failure modes and the plan at 10× and 100× scale are in **[DESIGN.md](DESIGN.md)**.

## Run it

```bash
pip install -e ".[dev]"
pytest -q                     # 19 tests, including Hypothesis property tests
python -m examples.demo       # one blackout, three strategies
python -m bench.run           # full benchmark (~40 s), writes results/bench.md
```

Requires Python 3.11 or newer. There are no runtime dependencies; pytest and Hypothesis are needed only for the tests.

## Results

Full run, from `results/bench.md`:

- 600,000 messages at 500 per tick.
- The recovery server sends 1,500 messages per tick, shared by retransmissions and snapshots.
- A snapshot costs one unit per resting order, about 4,500.

**Incident: a 60-tick blackout (30,000 messages lost) plus 0.1% random packet loss**

| strategy | final book exact | recovery msgs fetched | ticks in sync | p99 staleness (msgs) | ticks to recover after blackout | CPU |
|---|---|---:|---:|---:|---:|---:|
| full replay | yes | 2,232,720 | 10.8% | 446,500 | 959 | 4.86 s |
| range gap-fill | yes | 30,500 | 96.2% | 19,500 | 21 | 2.60 s |
| **gap-fill + snapshot** | yes | **4,962** | **97.7%** | **380** | **3** | 2.53 s |

**Steady random packet loss, no blackout**

| loss rate | strategy | recovery msgs fetched | msgs applied / published | ticks in sync |
|---|---|---:|---:|---:|
| 0.05% | full replay | 2,557,460 | 4.71× | 12.0% |
| 0.05% | gap-fill | 260 | 1.00× | 98.9% |
| 0.1% | full replay | 2,326,220 | 4.23× | 8.1% |
| 0.1% | gap-fill | 800 | 1.00× | 96.8% |
| 1% | full replay | 2,902,000 | 4.88× | 0.9% |
| 1% | gap-fill | 6,440 | 1.00× | 76.5% |

What these show:

- **Every strategy ends with an exact book.** The difference is the cost of getting there.
- **Rebuilding from scratch stops working under any steady loss.** By the time a rebuild finishes, there's another gap. It fetches 4–5× the day's messages and is almost never current.
- **The snapshot fallback is what makes the blackout cheap.** It fetches about 4,500 snapshot entries instead of 30,000 messages, and is back in sync in 3 ticks instead of 21.
- **At 1% loss, gap-fill is in sync only 76.5% of ticks.** That's the cost of the one-tick grace period, which keeps reordered packets from triggering retransmissions. DESIGN.md covers the tradeoff.

## How it's tested

- **End-to-end property test (Hypothesis):** random drop, duplicate and reorder rates, random blackouts, random recovery bandwidth and random order flow, run for every strategy. The final book must equal the exchange's, and the apply trace must be strictly sequential. The only jumps allowed are a full-replay reset to 1, or a skip to S+1 right after a snapshot restore at S.
- **Found a real bug:** that property test caught the baseline missing lost packets at the very end of a session, which is why heartbeats exist in the simulator.
- **IntervalSet property test (500 cases):** compared against a plain Python set after every operation. Ranges must stay sorted, disjoint and fully merged.
- **Mutation checks:** an off-by-one in interval merging, and an off-by-one in snapshot restore, are each caught by the tests.
- **Unit tests:** book semantics (partial cancels, over-execution, unknown orders), snapshot round trips, packetizing, a clean network, a blackout, and tail loss.

## Limitations

- **Synthetic flow and a tick-based clock.** Staleness is measured in messages and ticks, not microseconds.
- **No A/B feed arbitration.** Real deployments arbitrate between two redundant feeds first; this layer handles what both lose.
- **Ideal retransmission.** Requests are never lost, and there's no cancel for an in-flight request after a snapshot.
- **Python speed.** About 4 µs per message, which is fine for comparing strategies but not for a production feed handler. DESIGN.md has the scale plan.

## Next steps

- A/B line arbitration in front of the recovery layer.
- Adaptive grace: learn the network's reorder delay and wait just long enough.
- Port the hot path (apply plus reorder buffer) to Rust with an array-indexed order map, and benchmark messages per second.

---
Built with AI pair-programming (Claude); I reviewed the design, tests and results. MIT licensed.
