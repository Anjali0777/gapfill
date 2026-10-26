"""Feed consumers: turn a lossy packet stream into an in-order message stream.

A consumer receives live packets (which may be dropped, duplicated or
reordered), asks recovery servers for what it missed, and applies messages to
its OrderBook strictly in sequence order, each exactly once.

Recovery requests go through a shared, bandwidth-limited server (see sim.py):
    ("range", first, last)  retransmit messages first..last
    ("snapshot",)           send the latest book snapshot
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .book import OrderBook
from .intervals import IntervalSet
from .messages import Msg

Request = tuple


class Consumer(ABC):
    name = "consumer"

    def __init__(self) -> None:
        self.book = OrderBook()
        self.expected = 1  # next sequence number to apply
        self.high_water = 0  # highest sequence number seen anywhere
        self.applied = 0  # messages applied, including re-applies after a reset
        self.trace: list[int] | None = None  # set to [] to record applied seqs

    @property
    def staleness(self) -> int:
        """How many known messages are not yet applied (0 = in sync)."""
        return max(0, self.high_water - (self.expected - 1))

    def _apply(self, seq: int, m: Msg) -> None:
        self.book.apply(m)
        self.applied += 1
        self.expected = seq + 1
        if self.trace is not None:
            self.trace.append(seq)

    @abstractmethod
    def on_messages(self, seq: int, msgs: tuple[Msg, ...] | list[Msg], live: bool) -> None:
        """Messages seq, seq+1, ... arrived (live packet or retransmission)."""

    @abstractmethod
    def on_heartbeat(self, head: int) -> None:
        """The feed is idle but says the latest sequence number is `head`."""

    def on_snapshot(self, seq: int, snap: dict) -> None:  # pragma: no cover - optional
        pass

    @abstractmethod
    def requests(self, now: int) -> list[Request]:
        """Recovery requests to send this tick."""


class FullReplay(Consumer):
    """Baseline: on any gap, throw the book away and rebuild from message 1.

    This is what you write first, and it is not unreasonable: it is simple,
    and replaying from the start of the day is the one recovery path that
    can't leave you with a subtly wrong book. Live packets that arrive during
    the rebuild are buffered and applied once the replay catches up to them.

    Cost per gap: O(messages so far today). Gaps late in the day, or several
    gaps in a row, get more and more expensive.
    """

    name = "full replay"

    def __init__(self) -> None:
        super().__init__()
        self.buffer: dict[int, Msg] = {}
        self.recovering = False
        self.replay_until = 0
        self.pending: list[Request] = []
        self.rebuilds = 0

    def on_messages(self, seq: int, msgs, live: bool) -> None:
        for i, m in enumerate(msgs):
            s = seq + i
            if s > self.high_water:
                self.high_water = s
            if s >= self.expected and s not in self.buffer:
                self.buffer[s] = m
        self._drain()

    def on_heartbeat(self, head: int) -> None:
        if head > self.high_water:
            self.high_water = head
        self._drain()

    def _drain(self) -> None:
        while self.expected in self.buffer:
            s = self.expected
            self._apply(s, self.buffer.pop(s))
        if self.recovering and self.expected > self.replay_until:
            self.recovering = False
        if not self.recovering and self.expected <= self.high_water:
            # A hole: the next message we need never arrived. Start over.
            self.rebuilds += 1
            self.recovering = True
            self.book = OrderBook()
            self.expected = 1
            self.replay_until = self.high_water
            # Keep only live messages beyond the replay range; the replay
            # covers everything up to replay_until.
            self.buffer = {s: m for s, m in self.buffer.items() if s > self.replay_until}
            self.pending.append(("range", 1, self.replay_until))

    def requests(self, now: int) -> list[Request]:
        out, self.pending = self.pending, []
        return out


class GapFill(Consumer):
    """Fetch only what is missing; jump ahead with a snapshot when that's cheaper.

    - Out-of-order and duplicate packets are absorbed by a reorder buffer.
    - Holes are tracked as ranges (IntervalSet). A hole is requested only if
      it is still open one tick after it appeared, so a packet that was merely
      reordered doesn't trigger a retransmission.
    - Adjacent holes are coalesced into as few range requests as possible, and
      a range is never requested twice while the first request is in flight.
    - If the outstanding holes add up to more than a snapshot would cost, the
      consumer asks for the exchange's latest snapshot instead, restores the
      book from it, and only fills the (small) gap between the snapshot and
      the live stream.

    Cost per gap: O(gap size), or O(book size + snapshot interval) for a huge
    gap — independent of how long the session has been running.
    """

    def __init__(self, snapshots: bool = True, snapshot_factor: float = 1.5) -> None:
        super().__init__()
        self.use_snapshots = snapshots
        self.snapshot_factor = snapshot_factor
        self.name = "gap-fill + snapshot" if snapshots else "range gap-fill"
        self.buffer: dict[int, Msg] = {}
        self.missing = IntervalSet()  # known holes in [expected, high_water]
        self.requested = IntervalSet()  # holes with a retransmission in flight
        self.prev_missing = IntervalSet()  # holes as of the previous tick
        self.snapshot_pending = False
        self.snapshots_used = 0
        self.restored_at: list[int] = []  # snapshot sequence numbers we jumped to
        self.ranges_requested = 0

    def on_messages(self, seq: int, msgs, live: bool) -> None:
        if not msgs:
            return
        last = seq + len(msgs) - 1
        if seq > self.high_water + 1:
            self.missing.add(max(self.high_water + 1, self.expected), seq - 1)
        if last > self.high_water:
            self.high_water = last
        lo = max(seq, self.expected)
        if lo <= last:
            buf = self.buffer
            for s in range(lo, last + 1):
                if s not in buf:
                    buf[s] = msgs[s - seq]
            self.missing.discard(lo, last)
            self.requested.discard(lo, last)
        self._drain()

    def on_heartbeat(self, head: int) -> None:
        if head > self.high_water:
            self.missing.add(max(self.high_water + 1, self.expected), head)
            self.high_water = head

    def _drain(self) -> None:
        buf = self.buffer
        while self.expected in buf:
            s = self.expected
            self._apply(s, buf.pop(s))

    def on_snapshot(self, seq: int, snap: dict) -> None:
        self.snapshot_pending = False
        if seq < self.expected:  # we already got past it the slow way
            return
        self.snapshots_used += 1
        self.restored_at.append(seq)
        self.book = OrderBook.from_snapshot(snap)
        self.expected = seq + 1
        self.high_water = max(self.high_water, seq)
        for s in [s for s in self.buffer if s <= seq]:
            del self.buffer[s]
        self.missing.discard_upto(seq)
        self.requested.discard_upto(seq)
        self.prev_missing.discard_upto(seq)
        self._drain()

    def requests(self, now: int) -> list[Request]:
        out: list[Request] = []
        # Holes that were already open last tick (not just reordered packets).
        stale = self.missing.intersect(self.prev_missing)
        self.prev_missing = IntervalSet(list(self.missing))
        if self.snapshot_pending:
            return out
        todo = stale.minus(self.requested)
        if not todo:
            return out
        if self.use_snapshots:
            snapshot_cost = max(len(self.book), 1)
            if todo.total() > self.snapshot_factor * snapshot_cost:
                self.snapshot_pending = True
                return [("snapshot",)]
        for a, b in todo:
            out.append(("range", a, b))
            self.requested.add(a, b)
            self.ranges_requested += 1
        return out
