"""A synthetic exchange: generates realistic order flow and serves recovery.

The order flow keeps a few thousand orders resting around a random-walking
mid price, with adds, partial cancels, executions and deletes in roughly the
proportions seen on a busy US equities feed (mostly adds and cancels).
"""

from __future__ import annotations

import random

from .book import OrderBook
from .messages import ADD, BUY, CANCEL, DELETE, EXECUTE, SELL, Msg


class OrderFlow:
    def __init__(self, seed: int = 1, target_resting: int = 3000) -> None:
        self.rng = random.Random(seed)
        self.target = target_resting
        self.live: list[int] = []  # resting order ids, for O(1) random pick
        self.pos: dict[int, int] = {}  # order id -> index in live
        self.qty: dict[int, int] = {}
        self.next_id = 1
        self.mid = 10_000  # price in ticks

    def _remove(self, oid: int) -> None:
        i = self.pos.pop(oid)
        last = self.live.pop()
        if last != oid:
            self.live[i] = last
            self.pos[last] = i
        del self.qty[oid]

    def next(self) -> Msg:
        r = self.rng.random()
        if r < 0.01:
            self.mid += self.rng.choice((-1, 1))
        fill = len(self.live) / self.target
        if not self.live or r < 0.55 - 0.2 * (fill - 1):
            oid = self.next_id
            self.next_id += 1
            side = self.rng.randrange(2)
            off = int(self.rng.expovariate(0.25)) + 1
            price = self.mid - off if side == BUY else self.mid + off
            q = self.rng.choice((100, 100, 100, 200, 300, 500, 1000))
            self.pos[oid] = len(self.live)
            self.live.append(oid)
            self.qty[oid] = q
            return (ADD, oid, side, price, q)
        oid = self.live[self.rng.randrange(len(self.live))]
        r2 = self.rng.random()
        if r2 < 0.55:
            self._remove(oid)
            return (DELETE, oid, 0, 0, 0)
        if r2 < 0.8:
            q = min(self.qty[oid], 100 * self.rng.randint(1, 3))
            kind = CANCEL
        else:
            q = min(self.qty[oid], 100 * self.rng.randint(1, 5))
            kind = EXECUTE
        self.qty[oid] -= q
        if self.qty[oid] == 0:
            self._remove(oid)
        return (kind, oid, 0, 0, q)


class Exchange:
    """Holds the full message log, and periodic snapshots for recovery."""

    def __init__(self, snapshot_every: int = 5000) -> None:
        self.log: list[Msg] = [()]  # type: ignore[list-item]  # 1-based: log[seq]
        self.book = OrderBook()
        self.snapshot_every = snapshot_every
        self.snapshots: list[tuple[int, dict[int, tuple[int, int, int]]]] = [(0, {})]

    @property
    def head(self) -> int:
        return len(self.log) - 1

    def publish(self, m: Msg) -> int:
        self.log.append(m)
        self.book.apply(m)
        seq = self.head
        if seq % self.snapshot_every == 0:
            self.snapshots.append((seq, self.book.snapshot()))
        return seq

    def range(self, first: int, last: int) -> list[Msg]:
        return self.log[first : last + 1]

    def latest_snapshot(self) -> tuple[int, dict[int, tuple[int, int, int]]]:
        return self.snapshots[-1]
