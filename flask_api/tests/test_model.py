import json

import pytest

from app.model import ModelMeta, ModelNotReady, ModelRegistry, StubScorer, _risk_from


def test_softmax_risk_is_one_minus_safe():
    assert _risk_from({"safe": 0.7, "spam": 0.2, "hate": 0.1}, "safe", "softmax") == pytest.approx(0.3)


def test_sigmoid_risk_is_worst_violation():
    assert _risk_from({"safe": 0.9, "spam": 0.4, "hate": 0.6}, "safe", "sigmoid") == 0.6


def test_single_logit_is_violation_probability():
    assert _risk_from({"violation": 0.42}, "violation", "sigmoid") == 0.42


def _meta(tmp_path, **kw):
    data = {"version": "v1", "labels": ["safe", "violation"], "safe_label": "safe", **kw}
    p = tmp_path / "model_meta.json"
    p.write_text(json.dumps(data))
    return p


def test_meta_loads_and_keeps_extra(tmp_path):
    m = ModelMeta.from_file(_meta(tmp_path, max_length=128, trained_on="2026-09"))
    assert m.max_length == 128 and m.extra == {"trained_on": "2026-09"}


def test_meta_rejects_unknown_safe_label(tmp_path):
    with pytest.raises(ValueError, match="safe_label"):
        ModelMeta.from_file(_meta(tmp_path, safe_label="ok"))


def test_meta_rejects_bad_activation(tmp_path):
    with pytest.raises(ValueError, match="activation"):
        ModelMeta.from_file(_meta(tmp_path, activation="relu"))


def test_registry_not_ready():
    with pytest.raises(ModelNotReady):
        ModelRegistry().score("x")


def test_registry_keeps_old_model_when_reload_fails(tmp_path):
    reg = ModelRegistry()
    reg.set(StubScorer())
    with pytest.raises(Exception):
        reg.load("onnx", tmp_path / "does-not-exist")
    assert reg.version == "stub-0" and reg.last_error


def test_stub_is_deterministic():
    assert StubScorer().score("abc").risk_score == StubScorer().score("abc").risk_score
