"""A set of integers stored as sorted, disjoint, closed ranges.

Missing sequence numbers come in runs (a lost packet is 20 consecutive
messages; a blackout is tens of thousands), so storing ranges instead of
individual numbers keeps the bookkeeping proportional to the number of
gaps, not the number of lost messages.

    add / discard: O(log g + k), g = ranges stored, k = ranges merged or split
    contains:      O(log g)
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator


class IntervalSet:
    __slots__ = ("_starts", "_ends")

    def __init__(self, ranges: list[tuple[int, int]] | None = None) -> None:
        self._starts: list[int] = []
        self._ends: list[int] = []
        for a, b in ranges or ():
            self.add(a, b)

    def add(self, a: int, b: int) -> None:
        """Add [a, b], merging with any range it overlaps or touches."""
        if a > b:
            return
        i = bisect_left(self._ends, a - 1)  # first range that ends at/after a-1
        j = bisect_right(self._starts, b + 1)  # ranges starting after b+1 stay
        if i < j:
            a = min(a, self._starts[i])
            b = max(b, self._ends[j - 1])
        self._starts[i:j] = [a]
        self._ends[i:j] = [b]

    def discard(self, a: int, b: int) -> None:
        """Remove [a, b], splitting ranges that straddle its edges."""
        if a > b:
            return
        i = bisect_left(self._ends, a)  # first range ending at/after a
        j = bisect_right(self._starts, b)  # ranges starting after b stay
        if i >= j:
            return
        keep_s: list[int] = []
        keep_e: list[int] = []
        if self._starts[i] < a:
            keep_s.append(self._starts[i])
            keep_e.append(a - 1)
        if self._ends[j - 1] > b:
            keep_s.append(b + 1)
            keep_e.append(self._ends[j - 1])
        self._starts[i:j] = keep_s
        self._ends[i:j] = keep_e

    def discard_upto(self, x: int) -> None:
        if self._starts:
            self.discard(self._starts[0], x)

    def __contains__(self, x: int) -> bool:
        i = bisect_right(self._starts, x) - 1
        return i >= 0 and self._ends[i] >= x

    def __iter__(self) -> Iterator[tuple[int, int]]:
        return iter(zip(self._starts, self._ends))

    def __len__(self) -> int:
        return len(self._starts)

    def __bool__(self) -> bool:
        return bool(self._starts)

    def total(self) -> int:
        return sum(e - s + 1 for s, e in zip(self._starts, self._ends))

    def minus(self, other: IntervalSet) -> IntervalSet:
        out = IntervalSet()
        out._starts, out._ends = self._starts[:], self._ends[:]
        for a, b in other:
            out.discard(a, b)
        return out

    def intersect(self, other: IntervalSet) -> IntervalSet:
        out = IntervalSet()
        for a, b in self:
            for c, d in other:
                lo, hi = max(a, c), min(b, d)
                if lo <= hi:
                    out.add(lo, hi)
        return out

    def first(self) -> int | None:
        return self._starts[0] if self._starts else None

    def __repr__(self) -> str:
        return f"IntervalSet({list(self)})"
