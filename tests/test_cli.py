from __future__ import annotations

import subprocess
import sys

import pytest

import local_workflow


def test_local_workflow_main_delegates_to_collect(monkeypatch):
    received: list[object] = []

    def run(argv=None):
        received.append(argv)
        return 7

    monkeypatch.setattr(local_workflow, "run_collection", lambda **kw: None)
    monkeypatch.setattr(local_workflow, "main", lambda argv=None: run(argv))

    assert local_workflow.main(["collect"]) == 7
    assert received == [["collect"]]


@pytest.mark.parametrize(
    "command",
    (
        [sys.executable, "local_workflow.py", "--help"],
    ),
)
def test_cli_help_exposes_only_objective_commands(command):
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    help_text = result.stdout.lower()
    assert "collect" in help_text
    assert "fulfill-request" in help_text
    assert "--data-root" in help_text
    assert not any(
        term in help_text
        for term in (
            "screen",
            "prefilter",
            "afternoon",
            "rank",
            "strategy",
            "serve",
            "evaluate",
        )
    )
