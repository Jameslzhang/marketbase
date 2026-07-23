from __future__ import annotations

import subprocess
import sys

import pytest

from alphasift import cli


def test_package_cli_delegates_to_objective_workflow(monkeypatch):
    received: list[object] = []

    def run(argv=None):
        received.append(argv)
        return 7

    monkeypatch.setattr(cli, "_workflow_main", run)

    assert cli.main(["collect"]) == 7
    assert received == [["collect"]]


@pytest.mark.parametrize(
    "command",
    (
        [sys.executable, "-m", "alphasift.cli", "--help"],
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
