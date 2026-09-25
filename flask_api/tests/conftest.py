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
        base = dict(gate_patterns_file=gate_file, thresholds=Thresholds(0.3, 0.85),
                    model_backend="stub", max_chars=500, admin_token="secret")
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
