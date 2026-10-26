"""python -m examples.demo

Replays a short session with a 20-tick blackout and prints, tick by tick,
how far behind each recovery strategy is (staleness, in messages).
"""

from gapfill.consumer import FullReplay, GapFill
from gapfill.sim import Config, Network, run

cfg = dict(ticks=120, rate=500, network=Network(drop=0.001, blackout=(60, 80), seed=3))

for name, make in [("full replay", FullReplay), ("range gap-fill", lambda: GapFill(snapshots=False)),
                   ("gap-fill + snapshot", GapFill)]:
    r = run(make, Config(**cfg))
    print(f"{name:20s} exact={r.correct}  recovery msgs={r.recovery_units:>8,}  "
          f"in sync {r.in_sync_pct:5.1f}% of ticks  back in sync {r.blackout_recovery_ticks} ticks after the blackout")
