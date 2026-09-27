import pytest

from app.routing import Decision, Thresholds, route

T = Thresholds(reject_min=0.5)


# Two outcomes only: risk >= 0.5 is rejected (safety <= 0.5), everything else is allowed.
@pytest.mark.parametrize("score,expected", [
    (0.0, Decision.ALLOW),
    (0.3, Decision.ALLOW),
    (0.4999, Decision.ALLOW),
    (0.5, Decision.REJECT),        # boundary is inclusive for reject
    (0.7, Decision.REJECT),
    (1.0, Decision.REJECT),
])
def test_boundaries(score, expected):
    assert route(score, T) is expected


def test_there_is_no_review_outcome():
    assert {d.value for d in Decision} == {"allow", "reject"}


def test_threshold_is_configurable():
    assert route(0.6, Thresholds(0.7)) is Decision.ALLOW
    assert route(0.7, Thresholds(0.7)) is Decision.REJECT


@pytest.mark.parametrize("r", [0.0, -0.1, 1.1])
def test_invalid_thresholds(r):
    with pytest.raises(ValueError):
        Thresholds(r)


def test_as_dict():
    assert T.as_dict() == {"reject_min": 0.5}
