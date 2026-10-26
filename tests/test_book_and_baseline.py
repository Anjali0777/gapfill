import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gapfill.book import OrderBook
from gapfill.consumer import FullReplay, GapFill
from gapfill.feed import Exchange, OrderFlow
from gapfill.messages import ADD, BUY, CANCEL, DELETE, EXECUTE, SELL, packetize
from gapfill.sim import Config, Network, run

CONSUMERS = [FullReplay, GapFill, lambda: GapFill(snapshots=False)]


def test_book_add_cancel_execute_delete():
    b = OrderBook()
    b.apply((ADD, 1, BUY, 99, 300))
    b.apply((ADD, 2, BUY, 99, 100))
    b.apply((ADD, 3, SELL, 101, 200))
    assert b.levels[(BUY, 99)] == 400 and b.best() == (99, 101)
    b.apply((CANCEL, 1, 0, 0, 100))
    b.apply((EXECUTE, 1, 0, 0, 500))  # over-execute clamps to what's left
    assert 1 not in b.orders and b.levels[(BUY, 99)] == 100
    b.apply((DELETE, 2, 0, 0, 0))
    assert (BUY, 99) not in b.levels and b.best() == (None, 101)


def test_book_rejects_impossible_sequences():
    b = OrderBook()
    b.apply((ADD, 1, BUY, 99, 100))
    with pytest.raises(ValueError):
        b.apply((ADD, 1, BUY, 99, 100))
    with pytest.raises(KeyError):
        b.apply((CANCEL, 42, 0, 0, 100))  # a missed add shows up like this


def test_snapshot_round_trip():
    f, x = OrderFlow(3), Exchange()
    for _ in range(20_000):
        x.publish(f.next())
    b = OrderBook.from_snapshot(x.book.snapshot())
    assert b.state() == x.book.state() and b.levels == x.book.levels


def test_packetize_is_contiguous():
    ps = packetize(10, [(ADD, i, BUY, 1, 1) for i in range(45)], 20)
    assert [(p.seq, p.last) for p in ps] == [(10, 29), (30, 49), (50, 54)]


@pytest.mark.parametrize("make", CONSUMERS)
def test_clean_network_stays_in_sync(make):
    r = run(make, Config(ticks=50, rate=200))
    assert r.correct and r.recovery_units == 0 and r.in_sync_pct == 100.0


@pytest.mark.parametrize("make", CONSUMERS)
def test_recovers_from_blackout(make):
    r = run(make, Config(ticks=120, rate=200, network=Network(blackout=(40, 50))))
    assert r.correct and r.blackout_recovery_ticks is not None


@pytest.mark.parametrize("make", CONSUMERS)
def test_tail_loss_is_detected_by_heartbeat(make):
    # The last packets of the session are lost; nothing live follows them.
    r = run(make, Config(ticks=60, rate=200, network=Network(blackout=(55, 60))))
    assert r.correct


net_strategy = st.builds(
    Network,
    drop=st.floats(0, 0.2),
    duplicate=st.floats(0, 0.2),
    reorder=st.floats(0, 0.3),
    blackout=st.one_of(st.none(), st.tuples(st.integers(0, 30), st.integers(1, 15)).map(lambda t: (t[0], t[0] + t[1]))),
    seed=st.integers(0, 10_000),
)


@pytest.mark.parametrize("make", CONSUMERS)
@settings(max_examples=60, deadline=None)
@given(net=net_strategy, flow_seed=st.integers(0, 1000), bw=st.integers(200, 3000))
def test_any_network_fault_ends_with_exact_book(make, net, flow_seed, bw):
    """Property: whatever the network does, the book ends identical to the
    exchange's, and every message was applied once, in order. Allowed
    exceptions: a full-replay reset starts again at 1, and a snapshot restore
    at seq S skips straight to S+1."""
    traced: list[int] = []
    made = []

    def factory():
        c = make()
        c.trace = traced
        made.append(c)
        return c

    r = run(factory, Config(ticks=40, rate=100, packet_size=10, recovery_bandwidth=bw,
                            snapshot_every=500, flow_seed=flow_seed, network=net, drain_ticks=5000))
    assert r.correct
    restores = set(getattr(made[0], "restored_at", []))
    last_reset = max((i for i, s in enumerate(traced) if s == 1), default=0)
    run_ = traced[last_reset:]
    assert run_ and run_[0] == 1 or (run_ and run_[0] - 1 in restores)
    for a, b in zip(run_, run_[1:]):
        assert b == a + 1 or (b > a and b - 1 in restores), f"jump {a} -> {b} not explained by a snapshot"
    assert run_[-1] == r.published
