"""The `--report` file: UTF-8 JSON about the Archive a run ended with, built from the harvest's
`Summary` only (no ids, record values, workspace name or key), written atomically. Never sent."""

import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import Any

from trekk_harvester.harvest import Outcome

DISTRIBUTION = "trekk-cli"


def cli_version() -> str:
    return version(DISTRIBUTION)


def report(outcome: Outcome) -> dict[str, Any]:
    summary = outcome.summary
    counts: dict[str, int] = {str(kind): count for kind, count in summary.counts.items()}
    return {
        "cli_version": cli_version(),
        "archive_spec": summary.archive_spec,
        "archive": outcome.archive.name,
        "workspace_label": summary.workspace_label,
        "status": "partial" if outcome.partial else "complete",
        "counts": counts,
        "mismatches": {
            kind: {"listed": listed, "archived": counts.get(kind, 0)}
            for kind, listed in summary.listed.items()
            if listed != counts.get(kind, 0)
        },
        "partial": dict(summary.partial),
        "missing": list(summary.missing),
        "not_used": list(summary.not_used),
        "not_available": list(summary.not_available),
        "errors": [{"step": step, "reason": reason} for step, reason in summary.errors],
    }


def write_report(path: Path, outcome: Outcome) -> None:
    """Write the report to `path` (a temp file next to it, then replaced; the temp file is
    removed when that fails)."""
    temp = path.with_name(f"{path.name}.tmp")
    try:
        temp.write_text(json.dumps(report(outcome), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
