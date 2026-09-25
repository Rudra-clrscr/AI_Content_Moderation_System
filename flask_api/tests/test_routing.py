import pytest

from app.routing import Decision, Thresholds, route

T = Thresholds(allow_max=0.3, reject_min=0.7)


# Team rule on the safety scale (safety = 1 - risk):
#   safety <= 0.3 -> reject, 0.3 < safety < 0.7 -> review, safety >= 0.7 -> safe
@pytest.mark.parametrize("score,expected", [
    (0.0, Decision.ALLOW),
    (0.3, Decision.ALLOW),         # safety 0.7 is safe: allow boundary is inclusive
    (0.3001, Decision.REVIEW),
    (0.5, Decision.REVIEW),
    (0.6999, Decision.REVIEW),
    (0.7, Decision.REJECT),        # safety 0.3 is rejected: reject boundary is inclusive
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
    assert route(0.5, t) is Decision.REJECT      # reject wins when the bands touch


@pytest.mark.parametrize("a,r", [(0.9, 0.5), (-0.1, 0.5), (0.2, 1.1)])
def test_invalid_thresholds(a, r):
    with pytest.raises(ValueError):
        Thresholds(a, r)
