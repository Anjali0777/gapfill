"""Tick-based simulation of an exchange feed, a lossy network and recovery.

Each tick the exchange publishes `rate` messages as packets. The network may
drop, duplicate or reorder them, and can black out entirely for a window (an
incident like a datacenter network fault). Consumers ask a recovery server
for missing data; the server has a fixed bandwidth in messages per tick,
shared by retransmissions and snapshots (a snapshot costs one unit per
resting order), and serves requests first come, first served.
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field

from .book import OrderBook
from .consumer import Consumer
from .feed import Exchange, OrderFlow
from .messages import packetize


@dataclass
class Network:
    drop: float = 0.0  # probability each packet is lost
    duplicate: float = 0.0  # probability each delivered packet arrives twice
    reorder: float = 0.0  # probability a packet is held back one tick
    blackout: tuple[int, int] | None = None  # ticks [start, end) with total loss
    seed: int = 7


@dataclass
class Config:
    ticks: int = 600
    rate: int = 500  # messages published per tick
    packet_size: int = 20
    recovery_bandwidth: int = 1500  # message-units the recovery server can send per tick
    snapshot_every: int = 5000
    drain_ticks: int = 2000  # extra ticks with no new traffic, to let consumers catch up
    flow_seed: int = 1
    network: Network = field(default_factory=Network)


@dataclass
class Result:
    name: str
    correct: bool  # final book identical to the exchange's
    caught_up_tick: int | None  # first tick after traffic stopped with staleness 0
    recovery_units: int  # message-units fetched from the recovery server
    applied: int  # messages applied (re-applies count)
    published: int
    in_sync_pct: float  # % of live ticks ending with staleness 0
    max_staleness: int
    p99_staleness: int
    blackout_recovery_ticks: int | None  # ticks after a blackout until back in sync
    cpu_s: float
    extra: dict = field(default_factory=dict)


class RecoveryServer:
    def __init__(self, exchange: Exchange, bandwidth: int, chunk: int) -> None:
        self.x = exchange
        self.bw = bandwidth
        self.chunk = chunk
        self.queue: deque[list] = deque()  # [consumer, request, progress]
        self.sent = 0

    def submit(self, c: Consumer, req: tuple) -> None:
        self.queue.append([c, req, 0])

    def tick(self) -> None:
        budget = self.bw
        while budget > 0 and self.queue:
            item = self.queue[0]
            c, req, done = item[0], item[1], item[2]
            if req[0] == "snapshot":
                # Snapshots stream over as many ticks as their size needs.
                # The content is fixed when sending starts.
                if len(item) == 3:
                    item.append(self.x.latest_snapshot())
                seq, snap = item[3]
                cost = max(1, len(snap))
                n = min(budget, cost - done)
                budget -= n
                self.sent += n
                item[2] += n
                if item[2] >= cost:
                    self.queue.popleft()
                    c.on_snapshot(seq, snap)
                continue
            _, first, last = req
            start = first + done
            n = min(budget, last - start + 1, self.chunk * 50)
            msgs = self.x.range(start, start + n - 1)
            for i in range(0, n, self.chunk):
                c.on_messages(start + i, msgs[i : i + self.chunk], False)
            budget -= n
            self.sent += n
            item[2] += n
            if start + n > last:
                self.queue.popleft()


def run(make: type[Consumer] | callable, cfg: Config) -> Result:
    flow = OrderFlow(cfg.flow_seed)
    x = Exchange(cfg.snapshot_every)
    c: Consumer = make()
    server = RecoveryServer(x, cfg.recovery_bandwidth, cfg.packet_size)
    net = cfg.network
    rng = random.Random(net.seed)
    held: list = []  # packets delayed by reordering
    stale: list[int] = []
    in_sync = 0
    bo_end = net.blackout[1] if net.blackout else None
    bo_recovered: int | None = None
    caught_up: int | None = None
    t0 = time.process_time()

    total_ticks = cfg.ticks + cfg.drain_ticks
    for tick in range(total_ticks):
        live = tick < cfg.ticks
        if live:
            first = x.head + 1
            msgs = [flow.next() for _ in range(cfg.rate)]
            for m in msgs:
                x.publish(m)
            packets = packetize(first, msgs, cfg.packet_size)
            deliver = held
            held = []
            dark = net.blackout and net.blackout[0] <= tick < net.blackout[1]
            for p in packets:
                if dark or rng.random() < net.drop:
                    continue
                if rng.random() < net.reorder:
                    held.append(p)
                    continue
                deliver.append(p)
                if rng.random() < net.duplicate:
                    deliver.append(p)
            for p in deliver:
                c.on_messages(p.seq, p.msgs, True)
        else:
            for p in held:
                c.on_messages(p.seq, p.msgs, True)
            held = []
            # Heartbeats carry the latest sequence number, so a consumer that
            # lost the tail of the feed still learns it is behind.
            c.on_heartbeat(x.head)

        for req in c.requests(tick):
            server.submit(c, req)
        server.tick()

        s = c.staleness
        if live:
            stale.append(s)
            in_sync += s == 0
        if bo_end is not None and bo_recovered is None and tick >= bo_end and s == 0:
            bo_recovered = tick - bo_end
        if not live and s == 0 and c.expected - 1 == x.head and caught_up is None:
            caught_up = tick
            break

    cpu = time.process_time() - t0
    truth = x.book.state()
    stale.sort()
    return Result(
        name=c.name,
        correct=c.expected - 1 == x.head and c.book.state() == truth,
        caught_up_tick=caught_up,
        recovery_units=server.sent,
        applied=c.applied,
        published=x.head,
        in_sync_pct=100 * in_sync / max(1, cfg.ticks),
        max_staleness=stale[-1] if stale else 0,
        p99_staleness=stale[int(len(stale) * 0.99)] if stale else 0,
        blackout_recovery_ticks=bo_recovered,
        cpu_s=cpu,
        extra={k: getattr(c, k) for k in ("rebuilds", "snapshots_used", "ranges_requested") if hasattr(c, k)},
    )


def replay_truth(x: Exchange) -> OrderBook:
    b = OrderBook()
    for m in x.log[1:]:
        b.apply(m)
    return b
