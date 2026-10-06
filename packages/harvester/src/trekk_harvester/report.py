"""The run report: per object kind, how many objects the Archive holds and, when Trigify gave
list totals, how many it listed; and `Summary`, the facts the CLI's report file is built from."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from trekk_archive import Kind

type ReportKind = Literal[
    "search",
    "search_result",
    "topic",
    "topic_engagement",
    "signal_subscription",
    "target",
    "signal",
    "insight",
    "monitor",
    "monitor_result",
    "monitor_post_result",
    "legacy_route",
    "workflow",
    "workflow_draft",
    "run",
    "output_row",
    "workflow_table",
    "org",
    "credit_ledger_entry",
    "usage_record",
    "integration",
]
REPORT_KINDS: tuple[ReportKind, ...] = get_args(ReportKind.__value__)


@dataclass(frozen=True, slots=True)
class KindReport:
    """`listed`: Trigify's list total (summed over child crawls), None unless every crawl of
    the kind gave one."""

    kind: ReportKind
    count: int
    listed: int | None


class Tally:
    """Collects list totals while the Archive objects are produced."""

    def __init__(self) -> None:
        self._listed: dict[ReportKind, int | None] = {}

    def listed(self, kind: ReportKind, total: int | None) -> None:
        """Add one crawl's total; a crawl without one makes the kind's total unknown."""
        known = self._listed.get(kind, 0)
        self._listed[kind] = None if known is None or total is None else known + total

    def report(self, counts: Mapping[Kind, int]) -> tuple[KindReport, ...]:
        return tuple(
            KindReport(kind, counts[kind], self._listed.get(kind)) for kind in REPORT_KINDS
        )


@dataclass(frozen=True, slots=True)
class Summary:
    """What an Archive holds, without any id or record value: its spec version and label, the
    object count per kind (`counts`), Trigify's list total per kind where it gave one
    (`listed`), the objects marked partial per kind (`partial`), the kinds the API cannot reach
    (`missing`), never used (`not_used`), the routes not available, and the (step, failure) of
    every crawl that stopped on a failure (`errors`)."""

    archive_spec: str
    workspace_label: str
    counts: Mapping[Kind, int]
    listed: Mapping[str, int]
    partial: Mapping[Kind, int]
    missing: tuple[Kind, ...]
    not_used: tuple[Kind, ...]
    not_available: tuple[str, ...]
    errors: tuple[tuple[str, str], ...]
