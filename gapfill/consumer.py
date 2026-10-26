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
