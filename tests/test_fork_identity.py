"""The fork must point at itself, not at the repository it was forked from.

This is not housekeeping. Home Assistant renders `documentation` and
`issue_tracker` from the manifest as the "Documentation" link and the
"Report an issue" button on the integration's page, and the README's custom
repository URL is what a user pastes into HACS. When those still name the
upstream project, a fork silently sends its own users — and its own bug
reports — somewhere else. This repository shipped exactly that until v1.0.0:
the README told people to install `johnbr/ha-tesla-fleet-telemetry`, so
anyone following it installed upstream and never saw this code at all.

Cheap to check, and the kind of thing that rots invisibly on a fork.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "custom_components" / "tesla_telemetry" / "manifest.json"
README = REPO / "README.md"
ISSUE_CONFIG = REPO / ".github" / "ISSUE_TEMPLATE" / "config.yml"

# The fork's own repository. Every user-facing link has to resolve here.
OWNER = "RonnyAL"
SLUG = f"{OWNER}/ha-tesla-fleet-telemetry"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_documentation_points_at_this_fork() -> None:
    assert _manifest()["documentation"] == f"https://github.com/{SLUG}"


def test_manifest_issue_tracker_points_at_this_fork() -> None:
    """A bug report for this code must not land on someone else's tracker."""
    assert _manifest()["issue_tracker"] == f"https://github.com/{SLUG}/issues"


def test_manifest_codeowner_is_the_fork_owner() -> None:
    assert _manifest()["codeowners"] == [f"@{OWNER}"]


def test_manifest_version_is_semver() -> None:
    """HACS sorts releases by this; a non-semver value breaks upgrades."""
    version = _manifest()["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version


def test_no_shipped_file_still_points_at_the_upstream_repository() -> None:
    """The README install URL and the issue-template links included.

    CLAUDE.md and the historical planning documents legitimately name
    upstream — they describe the fork's origin — so only files a user's
    installation or HACS actually reads are checked here.
    """
    shipped = [MANIFEST, README, ISSUE_CONFIG]
    offenders = [
        path.relative_to(REPO).as_posix()
        for path in shipped
        if "github.com/johnbr/" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"these shipped files still link to the upstream repository: "
        f"{offenders}. A fork that does this sends its own users and bug "
        f"reports upstream."
    )
