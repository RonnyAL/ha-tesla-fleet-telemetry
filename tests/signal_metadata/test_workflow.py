"""Tests for the catalog refresh workflow.

These are static checks on the YAML. They cannot prove the job runs, but
each pins a failure that was reproduced against real git and that would
otherwise only show up as a red scheduled job weeks later.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github/workflows/signal-catalog.yml"


def _steps() -> list[dict]:
    data = yaml.safe_load(WORKFLOW.read_text())
    return data["jobs"]["refresh"]["steps"]


def _script(name_fragment: str) -> str:
    for step in _steps():
        if name_fragment.lower() in step.get("name", "").lower():
            return step.get("run", "")
    raise AssertionError(f"no step whose name contains {name_fragment!r}")


def test_workflow_is_valid_yaml() -> None:
    assert _steps(), "refresh job has no steps"


def test_every_gh_pr_call_pins_the_repo() -> None:
    """In a fork, `gh` defaults to the PARENT repo. This fork must never open
    a pull request against upstream — a hard ground rule in CLAUDE.md."""
    text = WORKFLOW.read_text()
    calls = text.count("gh pr create") + text.count("gh pr edit")
    pinned = text.count('--repo "${{ github.repository }}"')
    assert calls > 0, "no gh pr invocations found"
    assert pinned == calls, (
        f"{calls} gh pr invocations but only {pinned} pass --repo; an "
        "unpinned one would target upstream"
    )


def test_push_does_not_use_a_bare_force_with_lease() -> None:
    """actions/checkout fetches only the triggering ref, so no remote-tracking
    ref exists for the refresh branch. A bare --force-with-lease then expects
    "must not exist" and is rejected as stale on every run after the first,
    breaking the update-in-place behaviour the fixed branch exists for.

    Reproduced against real git: first push succeeds, second is rejected with
    "stale info".
    """
    script = _script("pull request")
    assert "--force-with-lease origin" not in script, (
        "bare --force-with-lease is rejected on the second and every later run"
    )
    assert 'git fetch origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"' in script
    assert '--force-with-lease="$BRANCH:$EXPECT"' in script, (
        "the lease needs an explicit expected sha; --force-with-lease=$BRANCH "
        "with no value still fails, because a fresh clone has no reflog for "
        "the tracking ref"
    )


def test_generator_step_surfaces_its_stderr_on_failure() -> None:
    """The default shell is `bash -e`, so `cmd 2> f` followed by `cat f`
    never reaches the cat when cmd fails — burying the only diagnosis of why
    the discovery chain broke."""
    script = _script("regenerate")
    assert "set +e" in script and "exit $rc" in script, (
        "the generator's stderr must be printed even when it exits non-zero"
    )
    assert "cat warnings.txt >&2" in script


def test_refresh_branch_is_fixed_not_dated() -> None:
    """A per-run branch name would accumulate one pull request a week."""
    assert "BRANCH=chore/signal-catalog-refresh" in _script("pull request")
