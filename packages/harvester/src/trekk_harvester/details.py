"""Per-object single GETs (a Search's detail, a workflow's detail or draft, a Run's detail or
variables): one record per answer in crawl `Details.crawl`, `{"id": key, "data"[, "failure"]}`
(`data` null when Trigify returned nothing or the request failed). A later record for a key
supersedes an earlier one.

Key-set based: every run first requests each key without a record (in key order), then, once
each, every key whose latest record has null `data`; an answer equal to that record as stored
(redacted; still 404, null or failing) is not committed again. A 404 or exhausted 5xx is
recorded, not raised."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from trekk_harvester.checkpoint import Checkpoint, redacted
from trekk_harvester.children import NOT_FOUND, SERVER_ERROR
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason, NotFoundError, RetriesExhaustedError
from trekk_harvester.events import ObjectType, Phase, Reporter
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import TrigifyPort


def _own(key: str) -> tuple[str, ...]:
    return (key,)


@dataclass(frozen=True, slots=True)
class Details:
    """One kind of per-object GET: `path(key)` gives the operation's path arguments."""

    crawl: str
    operation: str
    object_type: ObjectType
    phase: Phase
    path: Callable[[str], tuple[str, ...]] = field(default=_own)


@dataclass(frozen=True, slots=True)
class Reasons:
    """Why an object's answer is missing: Trigify returned none (`empty`), answered 404 or
    kept failing (`failed`, by failure), or the run stopped before asking (`stopped`)."""

    empty: str
    failed: Mapping[str, str]
    stopped: Mapping[ApiOffReason, str]


async def harvest_details(
    runner: Runner,
    client: TrigifyPort,
    checkpoint: Checkpoint,
    clock: Clock,
    reporter: Reporter,
    details: Details,
    keys: Sequence[str],
) -> None:
    latest = latest_records(checkpoint, details)
    missing = [key for key in keys if key not in latest]
    retry = [key for key in keys if key in latest and latest[key]["data"] is None]
    done = len(keys) - len(missing)
    tracker = ProgressTracker(
        clock, reporter, details.object_type, details.phase, start=done, total=len(keys)
    )
    for index, key in enumerate(missing, done + 1):
        record = await _get(runner, client, details, key)
        checkpoint.commit(details.crawl, [record], cursor=None, done=False)
        tracker.update(index)
    tracker.update(len(keys), final=True)
    for key in retry:
        record = await _get(runner, client, details, key)
        if redacted(record) != latest[key]:
            checkpoint.commit(details.crawl, [record], cursor=None, done=False)


async def _get(runner: Runner, client: TrigifyPort, details: Details, key: str) -> dict[str, Any]:
    try:
        answer = await runner.call(getattr(client, details.operation), *details.path(key))
    except NotFoundError:
        return {"id": key, "data": None, "failure": NOT_FOUND}
    except RetriesExhaustedError as error:
        if not error.server_error:
            raise
        return {"id": key, "data": None, "failure": SERVER_ERROR}
    data = (
        None
        if answer.data is None
        else answer.data.model_dump(mode="json", by_alias=True, exclude_unset=True)
    )
    return {"id": key, "data": data}


def latest_records(checkpoint: Checkpoint, details: Details) -> dict[str, dict[str, Any]]:
    """The latest record per key."""
    return {record["id"]: record for record in checkpoint.records(details.crawl)}


def missing_reason(
    key: str, record: dict[str, Any] | None, stopped: ApiOffReason | None, reasons: Reasons
) -> str:
    """Why `key` has no answer data (`record`: its latest record, None when never asked)."""
    if record is not None:
        failure = record.get("failure")
        return reasons.empty if failure is None else reasons.failed[failure]
    if stopped is None:
        raise RuntimeError(f"{key} was listed but never requested")
    return reasons.stopped[stopped]


def found(records: Mapping[str, dict[str, Any]], keys: Sequence[str]) -> list[str]:
    """The keys whose latest answer was not a 404 (never asked counts as found)."""
    return [key for key in keys if records.get(key, {}).get("failure") != NOT_FOUND]
