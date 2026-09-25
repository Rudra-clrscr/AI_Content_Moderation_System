import pytest

from app.routing import Decision, Thresholds, route

T = Thresholds(allow_below=0.3, reject_at=0.85)


@pytest.mark.parametrize("score,expected", [
    (0.0, Decision.ALLOW),
    (0.2999, Decision.ALLOW),
    (0.3, Decision.REVIEW),        # lower boundary is inclusive for review
    (0.8499, Decision.REVIEW),
    (0.85, Decision.REJECT),       # upper boundary is inclusive for reject
    (1.0, Decision.REJECT),
])
def test_boundaries(score, expected):
    assert route(score, T) is expected


def test_floor_raises_allow_to_review():
    assert route(0.01, T, floor=Decision.REVIEW) is Decision.REVIEW


def test_floor_never_lowers():
    assert route(0.99, T, floor=Decision.REVIEW) is Decision.REJECT


def test_equal_thresholds_disable_review_band():
    t = Thresholds(0.5, 0.5)
    assert route(0.49, t) is Decision.ALLOW
    assert route(0.5, t) is Decision.REJECT


@pytest.mark.parametrize("a,r", [(0.9, 0.5), (-0.1, 0.5), (0.2, 1.1)])
def test_invalid_thresholds(a, r):
    with pytest.raises(ValueError):
        Thresholds(a, r)
