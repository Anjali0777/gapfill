"""Market-by-order messages, in the spirit of Nasdaq ITCH.

Every message carries a sequence number assigned by the exchange. Messages are
sent in packets (MoldUDP64 style): a packet holds the sequence number of its
first message and a run of consecutive messages. UDP multicast can drop,
duplicate or reorder packets; the sequence numbers are how a consumer notices.

Messages are plain tuples for speed: (kind, order_id, side, price, qty).
"""

from __future__ import annotations

from typing import NamedTuple

ADD, CANCEL, EXECUTE, DELETE = 0, 1, 2, 3
BUY, SELL = 0, 1

Msg = tuple[int, int, int, int, int]  # kind, order_id, side, price (ticks), qty


class Packet(NamedTuple):
    seq: int  # sequence number of msgs[0]
    msgs: tuple[Msg, ...]

    @property
    def last(self) -> int:
        return self.seq + len(self.msgs) - 1


def packetize(first_seq: int, msgs: list[Msg], max_per_packet: int) -> list[Packet]:
    """Split consecutive messages into packets of at most max_per_packet."""
    out = []
    for i in range(0, len(msgs), max_per_packet):
        out.append(Packet(first_seq + i, tuple(msgs[i : i + max_per_packet])))
    return out
