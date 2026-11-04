"""The spec claims things the code and config decide. Keep the claims true.

`docs/openapi.yaml` is served to callers and rendered as the Swagger UI, so a list in it that
has drifted from the service is worse than no list: it is a wrong answer with the service's
own name on it. Nothing else checks these - the spec is YAML, and the suite otherwise only
asserts that it is served at all.
"""
import io
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT.parent / "docs" / "openapi.yaml"
LEVELS = PROJECT_ROOT / "config" / "moderation_levels.yaml"


def _load(path):
    return yaml.safe_load(io.open(path, encoding="utf-8"))


def test_level_names_match_the_config():
    """`LevelName` is a dropdown of every level an admin can pick.

    It started as three - strict, medium, lenient - which is what the dashboard's buttons
    send and what the file's prose talks about. The file also ships level_1 through level_10,
    so a third of the choices were missing from the page an admin reads to find out what the
    choices are.
    """
    spec_names = set(_load(SPEC)["components"]["schemas"]["LevelName"]["enum"])
    config_names = set(_load(LEVELS)["levels"])
    assert spec_names == config_names, (
        f"openapi.yaml LevelName is out of step with moderation_levels.yaml.\n"
        f"  only in the spec:   {sorted(spec_names - config_names)}\n"
        f"  only in the config: {sorted(config_names - spec_names)}"
    )


def test_content_type_enum_matches_the_surfaces_the_service_accepts():
    """The path parameter is the list of surfaces. `ContentType` is the one that decides."""
    from app.pipeline import ContentType

    spec_types = set(_load(SPEC)["components"]["schemas"]["ContentType"]["enum"])
    real_types = {c.value for c in ContentType}
    assert spec_types == real_types, (
        f"openapi.yaml ContentType is out of step with app.pipeline.ContentType.\n"
        f"  only in the spec: {sorted(spec_types - real_types)}\n"
        f"  only in the code: {sorted(real_types - spec_types)}"
    )


def test_every_level_the_spec_offers_is_actually_accepted(make_settings, scorer):
    """The enum is a promise that each of these works. Make the service keep it.

    A name in the list that the service refuses is the same failure as a missing one, from
    the other side: the admin picks it from the page and gets a 400.
    """
    from app import create_app

    c = create_app(make_settings(admin_token="secret"), scorer=scorer).test_client()
    hdr = {"X-Admin-Token": "secret"}
    for name in _load(SPEC)["components"]["schemas"]["LevelName"]["enum"]:
        r = c.put("/v1/admin/moderation-levels/article", json={"level": name}, headers=hdr)
        assert r.status_code == 200, f"level {name!r} is in the spec but got {r.status_code}"
