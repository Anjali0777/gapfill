from hypothesis import given, settings
from hypothesis import strategies as st

from gapfill.intervals import IntervalSet

op = st.tuples(st.sampled_from(["add", "discard"]), st.integers(0, 200), st.integers(0, 30))


@settings(max_examples=500)
@given(st.lists(op, max_size=60), st.integers(0, 250))
def test_matches_a_plain_python_set(ops, probe):
    """Every operation sequence leaves the IntervalSet equal to a set model,
    with ranges sorted, disjoint and non-adjacent (fully merged)."""
    iv, model = IntervalSet(), set()
    for kind, a, length in ops:
        b = a + length
        if kind == "add":
            iv.add(a, b)
            model |= set(range(a, b + 1))
        else:
            iv.discard(a, b)
            model -= set(range(a, b + 1))
        expanded = {x for s, e in iv for x in range(s, e + 1)}
        assert expanded == model
        rs = list(iv)
        assert all(s <= e for s, e in rs)
        assert all(rs[i][1] + 1 < rs[i + 1][0] for i in range(len(rs) - 1))
        assert iv.total() == len(model)
    assert (probe in iv) == (probe in model)


def test_minus_intersect_and_discard_upto():
    a = IntervalSet([(1, 10), (20, 30)])
    b = IntervalSet([(5, 22)])
    assert list(a.minus(b)) == [(1, 4), (23, 30)]
    assert list(a.intersect(b)) == [(5, 10), (20, 22)]
    a.discard_upto(25)
    assert list(a) == [(26, 30)]
    assert a.first() == 26


def test_empty_and_inverted_ranges_are_ignored():
    s = IntervalSet()
    s.add(5, 4)
    s.discard(9, 1)
    assert not s and s.total() == 0 and s.first() is None
