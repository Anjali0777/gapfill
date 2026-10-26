"""A market-by-order book: every resting order, plus aggregated price levels.

Applying a message is O(1). The book has no idea about sequence numbers; it
trusts its caller to feed it every message exactly once, in order. Getting
that guarantee from a lossy feed is the whole job of the recovery layer.
"""

from __future__ import annotations

from .messages import ADD, BUY, CANCEL, DELETE, EXECUTE, SELL, Msg


class OrderBook:
    __slots__ = ("orders", "levels")

    def __init__(self) -> None:
        self.orders: dict[int, list[int]] = {}  # order_id -> [side, price, qty]
        self.levels: dict[tuple[int, int], int] = {}  # (side, price) -> total qty

    def apply(self, m: Msg) -> None:
        kind, oid, side, price, qty = m
        if kind == ADD:
            if oid in self.orders:
                raise ValueError(f"duplicate add for order {oid}")
            self.orders[oid] = [side, price, qty]
            key = (side, price)
            self.levels[key] = self.levels.get(key, 0) + qty
            return
        o = self.orders.get(oid)
        if o is None:
            # Seeing a cancel for an order we never saw added means we missed
            # a message: the book is no longer trustworthy.
            raise KeyError(f"unknown order {oid}")
        if kind == DELETE:
            qty = o[2]
        elif kind not in (CANCEL, EXECUTE):
            raise ValueError(f"unknown message kind {kind}")
        dq = min(qty, o[2])
        o[2] -= dq
        key = (o[0], o[1])
        left = self.levels[key] - dq
        if left:
            self.levels[key] = left
        else:
            del self.levels[key]
        if o[2] == 0:
            del self.orders[oid]

    # --- snapshots ---------------------------------------------------------
    def snapshot(self) -> dict[int, tuple[int, int, int]]:
        return {oid: (o[0], o[1], o[2]) for oid, o in self.orders.items()}

    @classmethod
    def from_snapshot(cls, snap: dict[int, tuple[int, int, int]]) -> OrderBook:
        b = cls()
        for oid, (side, price, qty) in snap.items():
            b.orders[oid] = [side, price, qty]
            key = (side, price)
            b.levels[key] = b.levels.get(key, 0) + qty
        return b

    # --- reading -----------------------------------------------------------
    def best(self) -> tuple[int | None, int | None]:
        bids = [p for (s, p) in self.levels if s == BUY]
        asks = [p for (s, p) in self.levels if s == SELL]
        return (max(bids) if bids else None, min(asks) if asks else None)

    def state(self) -> dict[int, tuple[int, int, int]]:
        """Canonical contents, for comparing two books."""
        return self.snapshot()

    def __len__(self) -> int:
        return len(self.orders)
