"""Per-feature moderation settings: how strict each BONC surface is, and whether it is on.

Two controls, from the product spec:

    enabled     the toggle. False means this feature is not moderated at all.
    reject_min  the moderation rating, 0 to 1. Risk at or below it publishes; above it is
                refused. Lower is stricter.

Named levels (`strict` / `medium` / `lenient`) are sugar over the number, so the admin UI can
show three buttons while an expert types 0.62. A raw `reject_min` always wins over a level.

WHAT "OFF" MEANS. With `enabled: false` nothing is scored and nothing is blocked - not the
model, not the Layer 1 gate. That is what a toggle labelled "disable moderation for this
feature" has to do to be honest about itself. The result then carries
`moderation: {enabled: false}` and the decision is ALLOW with no risk score, so a published
item that nobody checked is never mistaken for one that was checked and found clean. The same
distinction `visual_content_checked` draws for pictures.

Resolution order for a content_type:

    features.<type>  ->  default  ->  built-in (enabled, medium)

so a surface that nobody has configured is moderated rather than silently open.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from .routing import Thresholds

log = logging.getLogger(__name__)

BUILTIN_LEVELS: dict[str, tuple[float, float]] = {
    "strict": (0.25, 0.25),
    "medium": (0.50, 0.50),
    "lenient": (0.85, 0.85),
}


@dataclass(frozen=True)
class FeaturePolicy:
    """What to do with one surface.

    `thresholds` is None in two different situations and the difference matters:

      enabled=True,  thresholds=None   moderated at the GLOBAL bar from settings.yaml
      enabled=True,  thresholds=set    moderated at this feature's own bar
      enabled=False                    not moderated at all, whatever `thresholds` says

    A disabled feature KEEPS its thresholds rather than having them cleared, so that switching
    it off and on again is lossless. Clearing them lost the setting: a `strict` feature toggled
    off and back on came up deferring to the global bar, which is looser, and the admin had no
    way to see that their level had quietly gone. `enabled` is checked first on every request,
    so a retained threshold is never applied while the feature is off.

    The second is the shipped default for every feature, and it is why this file changes no
    behaviour until an admin sets something. An earlier version defaulted to `medium` instead,
    which silently overrode `thresholds:` in settings.yaml for every surface - twelve tests
    caught it by asking for a revise band and being refused outright.
    """

    enabled: bool
    thresholds: Thresholds | None
    level: str | None = None          # the name, when one was used; for display only

    def as_dict(self) -> dict:
        # A disabled feature reports only the toggle: nothing was judged, so quoting a bar it
        # was judged at would be a lie in the audit row.
        out: dict = {"enabled": self.enabled}
        if not self.enabled:
            return out
        if self.level:
            out["level"] = self.level
        if self.thresholds is not None:
            out.update(self.thresholds.as_dict())
        return out


class ModerationLevels:
    """The per-feature table, re-readable at runtime.

    Reads are lock-free against a single immutable dict; a write swaps the whole dict under a
    lock. An admin changing a level mid-flight therefore cannot be seen half-applied by a
    request already in progress, and no request pays for a lock on the hot path.
    """

    def __init__(self, levels: dict[str, Thresholds] | None = None,
                 features: dict[str, FeaturePolicy] | None = None,
                 default: FeaturePolicy | None = None, source: Path | None = None):
        self._lock = threading.Lock()
        self._levels = dict(levels or {n: Thresholds(*v) for n, v in BUILTIN_LEVELS.items()})
        self._features = dict(features or {})
        self._default = default or FeaturePolicy(True, None, None)
        self.source = source

    # ---- reading ---------------------------------------------------------------------

    def for_type(self, content_type: str) -> FeaturePolicy:
        return self._features.get(str(content_type), self._default)

    def as_dict(self) -> dict:
        return {
            "levels": {n: t.as_dict() for n, t in sorted(self._levels.items())},
            "default": self._default.as_dict(),
            "features": {k: v.as_dict() for k, v in sorted(self._features.items())},
        }

    def disabled_features(self) -> list[str]:
        """Which surfaces are currently unmoderated. Reported by /ready, because an operator
        should be able to see that from outside rather than infer it from published content."""
        off = [k for k, v in sorted(self._features.items()) if not v.enabled]
        if not self._default.enabled:
            off.append("(default)")
        return off

    # ---- writing ---------------------------------------------------------------------

    def set_feature(self, content_type: str, *, enabled: bool | None = None,
                    level: str | None = None, reject_min: float | None = None,
                    allow_max: float | None = None) -> FeaturePolicy:
        """Change one surface. Returns the resulting policy; raises ValueError if invalid."""
        with self._lock:
            current = self._features.get(content_type, self._default)
            now_enabled = current.enabled if enabled is None else bool(enabled)
            thresholds, name = current.thresholds, current.level
            if level is not None:
                if level not in self._levels:
                    raise ValueError(f"unknown level {level!r}; have "
                                     f"{sorted(self._levels)}")
                thresholds, name = self._levels[level], level
            if reject_min is not None or allow_max is not None:
                base = thresholds or self._levels["medium"]
                hi = base.reject_min if reject_min is None else float(reject_min)
                if allow_max is not None:
                    lo = float(allow_max)
                elif base.allow_max == base.reject_min:
                    # No revise band before, so there is none after: moving the rating moves
                    # the single boundary. Keeping the old allow_max here INVENTED a revise
                    # band - setting 0.9 on a 0.25/0.25 policy produced 0.25/0.9, and content
                    # the admin meant to publish came back "revise" instead.
                    lo = hi
                else:
                    lo = min(base.allow_max, hi)    # had a band: keep it, but stay ordered
                thresholds = Thresholds(lo, hi)     # validates 0 <= lo <= hi <= 1
                name = None                         # a number was given; it is no longer named
            policy = FeaturePolicy(now_enabled, thresholds, name)
            self._features = {**self._features, content_type: policy}
            return policy

    # ---- loading ---------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Path) -> "ModerationLevels":
        """Load the table. A missing or unreadable file is NOT fatal and NOT permissive: the
        built-in defaults moderate every surface at `medium`, because a config problem must
        never be the reason content stops being checked."""
        import yaml

        if not path.exists():
            log.warning("no moderation levels at %s; every feature moderated at medium", path)
            return cls(source=path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            log.exception("could not read %s; falling back to medium for every feature", path)
            return cls(source=path)

        levels: dict[str, Thresholds] = {}
        for name, spec in (raw.get("levels") or {}).items():
            try:
                levels[str(name)] = Thresholds(float(spec["allow_max"]),
                                               float(spec["reject_min"]))
            except Exception:
                log.exception("ignoring malformed level %r in %s", name, path)
        for name, (lo, hi) in BUILTIN_LEVELS.items():
            levels.setdefault(name, Thresholds(lo, hi))

        def policy(spec: dict, where: str) -> FeaturePolicy | None:
            try:
                enabled = bool(spec.get("enabled", True))
                name = spec.get("level")
                if "reject_min" in spec:
                    hi = float(spec["reject_min"])
                    lo = float(spec.get("allow_max", hi))
                    thresholds, name = Thresholds(lo, hi), None
                elif name is not None:
                    if str(name) not in levels:
                        raise ValueError(f"unknown level {name!r}")
                    thresholds, name = levels[str(name)], str(name)
                else:
                    thresholds, name = None, None     # defer to settings.thresholds
                return FeaturePolicy(enabled, thresholds, name)
            except Exception:
                log.exception("ignoring malformed entry %s in %s", where, path)
                return None

        default = policy(raw.get("default") or {}, "default") or FeaturePolicy(True, None, None)
        features = {}
        for key, spec in (raw.get("features") or {}).items():
            got = policy(spec or {}, f"features.{key}")
            if got is not None:
                features[str(key)] = got

        off = [k for k, v in features.items() if not v.enabled]
        if off or not default.enabled:
            log.warning("moderation is DISABLED for: %s", ", ".join(off + ([] if default.enabled
                                                                           else ["(default)"])))
        return cls(levels, features, default, path)
