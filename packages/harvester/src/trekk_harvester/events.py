"""What a harvest reports while it runs. The CLI renders each event through its catalogue."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

type ObjectType = Literal[
    "search",
    "topic",
    "signal_subscription",
    "target",
    "feed",
    "monitor",
    "monitor_post",
    "legacy_route",
    "workflow",
    "run",
    "workflow_table",
    "credit_ledger_entry",
    "integration",
]
type Phase = Literal["listing", "details", "results", "engagements", "drafts", "runs", "variables"]


@dataclass(frozen=True, slots=True)
class Started:
    workspace_id: str
    workspace_name: str
    checkpoint: Path
    resumed: bool


@dataclass(frozen=True, slots=True)
class Retrying:
    """The latest Archive `previous` is not final and Trigify answered this run: what it is
    missing is harvested again into a new Archive next to it (when anything new arrives);
    `previous` is kept."""

    previous: Path


@dataclass(frozen=True, slots=True)
class Progress:
    """`total` is None while the object type is still being listed; `eta` is None until a rate
    is known (or while listing). In the `results`, `engagements` and `runs` phases `completed`
    and `total` count parents (Searches, topics, Monitors, Monitor posts, workflows) whose child
    crawl was walked; in `details`, `drafts` and `variables` the objects requested."""

    object_type: ObjectType
    phase: Phase
    completed: int
    total: int | None
    eta: datetime | None


@dataclass(frozen=True, slots=True)
class Paused:
    """Trigify rate-limited a request; the harvest resumes by itself at `until`."""

    until: datetime


type Event = Started | Retrying | Progress | Paused


class Reporter(Protocol):
    def emit(self, event: Event) -> None: ...
