"""Benchmark recovery strategies on the same simulated feed.

    python -m bench.run            # full run, writes results/bench.md
    python -m bench.run --quick

Scenarios (500 msgs/tick published, recovery server 1,500 msg-units/tick):

  incident   a 60-tick blackout (30,000 messages lost) mid-session, plus
             light random packet loss before and after: the shape of
             "intermittent data gaps" during a datacenter network fault.
  loss sweep only random packet loss, at increasing rates.
"""

from __future__ import annotations

import sys
from pathlib import Path

from gapfill.consumer import FullReplay
from gapfill.sim import Config, Network, run

QUICK = "--quick" in sys.argv
TICKS = 300 if QUICK else 1200

STRATEGIES = [("full replay", FullReplay)]


def fmt_ticks(t):
    return "never (within run)" if t is None else str(t)


def table_row(r, blackout=False):
    return (
        f"| {r.name} | {'yes' if r.correct else '**no**'} | {r.recovery_units:,} | "
        f"{r.applied / r.published:.2f}× | {r.in_sync_pct:.1f}% | {r.p99_staleness:,} | {r.max_staleness:,} | "
        f"{fmt_ticks(r.blackout_recovery_ticks) if blackout else '–'} | {r.cpu_s:.2f} s |"
    )


HEADER = (
    "| strategy | final book exact | recovery msgs fetched | msgs applied / published | ticks in sync | "
    "p99 staleness (msgs) | max staleness | ticks to recover after blackout | CPU |\n"
    "|---|---|---:|---:|---:|---:|---:|---:|---:|"
)


def main() -> None:
    lines: list[str] = []

    def out(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    out(f"Python {sys.version.split()[0]}, {'quick' if QUICK else 'full'} run, {TICKS} live ticks × 500 msgs")

    bo_start = TICKS // 2
    incident = Network(drop=0.001, blackout=(bo_start, bo_start + 60), seed=11)
    out(f"\n### Incident: 60-tick blackout at tick {bo_start} (30,000 msgs) + 0.1% packet loss\n")
    out(HEADER)
    for _, make in STRATEGIES:
        out(table_row(run(make, Config(ticks=TICKS, network=incident)), blackout=True))

    for p in (0.0001, 0.001, 0.01):
        out(f"\n### Random packet loss {p * 100:g}% (no blackout)\n")
        out(HEADER)
        for _, make in STRATEGIES:
            out(table_row(run(make, Config(ticks=TICKS, network=Network(drop=p, seed=5)))))

    if not QUICK:
        Path("results").mkdir(exist_ok=True)
        Path("results/bench.md").write_text("\n".join(lines) + "\n")
        print("\nWrote results/bench.md")


if __name__ == "__main__":
    main()
