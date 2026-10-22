import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.config import Settings  # noqa: E402
from app.model import ScoreResult  # noqa: E402
from app.routing import Thresholds  # noqa: E402
from app.sinks import MemorySink  # noqa: E402

GATE_YAML = r"""
version: test-1
rules:
  - id: test.block_term
    category: hate
    action: block
    terms: ["badword", "two words"]
  - id: test.scam
    category: fraud
    action: block
    patterns: ['\bdouble\s+your\s+money\b']
  - id: test.flag_shortener
    category: spam
    action: flag
    patterns: ['\bbit\.ly/\S+']
  - id: test.revise_payment
    category: fraud
    action: revise
    patterns: ['\bpay (?:in|by) crypto\b']
"""


class FixedScorer:
    version = "fixed-1"

    def __init__(self, risk: float = 0.1):
        self.risk = risk
        self.calls = 0

    def score(self, text):
        self.calls += 1
        return ScoreResult(self.risk, "violation" if self.risk >= 0.5 else "safe",
                           {"safe": 1 - self.risk, "violation": self.risk}, self.version, 1.0)


@pytest.fixture
def gate_file(tmp_path):
    p = tmp_path / "config" / "gate.yaml"
    p.parent.mkdir()
    p.write_text(GATE_YAML, encoding="utf-8")
    return p


@pytest.fixture
def make_settings(gate_file):
    def _make(**overrides):
        base = dict(gate_patterns_file=gate_file, thresholds=Thresholds(0.3, 0.7),
                    model_backend="stub", max_chars=500, admin_token="secret",
                    result_sink="log")
        base.update(overrides)
        return Settings(**base)
    return _make


@pytest.fixture
def scorer():
    return FixedScorer()


@pytest.fixture
def sink():
    return MemorySink()


@pytest.fixture
def client(make_settings, scorer, sink):
    app = create_app(make_settings(), scorer=scorer, sink=sink)
    return app.test_client()


@pytest.fixture(autouse=True)
def _throwaway_admin_db(monkeypatch):
    """Tests must never touch the real data/admin.db."""
    monkeypatch.setenv("ADMIN_DB_PATH", ":memory:")


# The small, WRITABLE files of the shipped model, plus the policy they are built from.
# Everything else in models/img_v1 is the encoder and is only ever read.
_GUARDED = [
    Path(__file__).resolve().parent.parent / "models" / "img_v1" / name
    for name in ("probe.npz", "exemplars.npz", "model_meta.json", "prompts.npz")
] + [Path(__file__).resolve().parent.parent / "config" / "visual_policy.yaml"]


@pytest.fixture(scope="session")
def _shipped_model_snapshot():
    return {p: p.read_bytes() for p in _GUARDED if p.exists()}


@pytest.fixture(autouse=True)
def _dont_retrain_the_shipped_model(_shipped_model_snapshot):
    """Tests must never leave the shipped image model changed.

    `_trainer()` builds an ImageTrainer with no model_dir, so it defaults to
    flask_api/models/img_v1 - the directory the service actually serves. Any test that
    reaches a retraining endpoint therefore refits the live probe and writes it over the real
    probe.npz, with weights fitted on a freshly seeded throwaway database.

    That was invisible while probe.npz held nothing but weights: the test database seeds the
    same way, so the bytes came out the same and nobody noticed. It stopped being invisible
    when the probe started carrying which categories an admin has withdrawn - a test run has
    withdrawn nothing, so running the suite silently gave an admin back a category they had
    taken away.

    Restoring is keyed on mtime and size so the common case costs two stat calls.
    """
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in _GUARDED if p.exists()}
    yield
    for p, was in before.items():
        if not p.exists():
            continue
        if (p.stat().st_mtime_ns, p.stat().st_size) == was:
            continue
        original = _shipped_model_snapshot.get(p)
        if original is not None and p.read_bytes() != original:
            p.write_bytes(original)
