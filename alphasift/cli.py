"""Installed command-line entry point for the objective data workflow."""

from __future__ import annotations

from collections.abc import Sequence

from local_workflow import main as _workflow_main


def main(argv: Sequence[str] | None = None) -> int:
    """Delegate to the packaged objective workflow."""
    return _workflow_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
