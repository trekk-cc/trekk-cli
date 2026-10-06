"""Per-parent child crawls (a Search's results, a topic's engagements, a Monitor's results):
one crawl per parent, named `<prefix>__<parent id>`, walked from its committed cursor on every
run until done.

A child crawl that Trigify answers 404 or keeps answering 5xx for is marked failed in the
checkpoint and the walk moves on to the next parent: Trigify is up, only that parent is
affected. Every other error (no answer, API off, Cutoff) stops the walk."""

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason, NotFoundError, RetriesExhaustedError
from trekk_harvester.events import ObjectType, Phase, Reporter
from trekk_harvester.paging import crawl_pages
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import TrigifyPort

PAGE_SIZE = 100  # the spec's maximum page size of every child operation
NOT_FOUND = "not_found"
SERVER_ERROR = "server_error"
SAFE_ID = re.compile(r"[a-z0-9_-]{1,64}")  # lowercase: no collisions on case-insensitive disks


def crawl_name(prefix: str, parent_id: str) -> str:
    """`<prefix>__<id>`; any other id (unsafe in a file name, or holding uppercase letters that
    a case-insensitive filesystem would fold) uses the first 32 hex of its sha256."""
    if SAFE_ID.fullmatch(parent_id):
        return f"{prefix}__{parent_id}"
    return f"{prefix}__sha256-{hashlib.sha256(parent_id.encode()).hexdigest()[:32]}"


def canonical(value: Any) -> str:
    """Canonical JSON: keys sorted, no whitespace, UTF-8 kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def result_id(item: Any) -> str:
    """The id of a record Trigify gives none: `sha256:` + the first 32 hex of the sha256 of its
    canonical JSON (stable across runs; equal records share it)."""
    return "sha256:" + hashlib.sha256(canonical(item).encode()).hexdigest()[:32]


def _record_id(record: Any) -> str:
    trigify_id: str = record["id"]
    return trigify_id


@dataclass(frozen=True, slots=True)
class Children:
    """One kind of child crawl. `history` and `records` name it in partial reasons
    (e.g. "result history", "results"); `record_id` gives a child record's id."""

    prefix: str
    operation: str
    object_type: ObjectType
    phase: Phase
    history: str
    records: str
    record_id: Callable[[Any], str] = field(default=_record_id)


async def crawl_children(
    runner: Runner,
    client: TrigifyPort,
    checkpoint: Checkpoint,
    clock: Clock,
    reporter: Reporter,
    children: Children,
    parent_ids: Sequence[str],
    *,
    params: Callable[[str], Mapping[str, Any]] | None = None,
) -> None:
    """Walk every parent's child crawl that is not done, each once, in order. Without `params`
    the parent id is the operation's path argument; with it, `params(parent_id)` gives the
    operation's other arguments (body fields of a body-paged operation) and no path."""
    names = [crawl_name(children.prefix, parent_id) for parent_id in parent_ids]
    done = sum(checkpoint.crawl(name).done for name in names)
    tracker = ProgressTracker(
        clock, reporter, children.object_type, children.phase, start=done, total=len(names)
    )
    for index, (parent_id, name) in enumerate(zip(parent_ids, names, strict=True)):
        if not checkpoint.crawl(name).done:
            try:
                await crawl_pages(
                    runner,
                    client,
                    checkpoint,
                    name,
                    children.operation,
                    size=PAGE_SIZE,
                    path=() if params else (parent_id,),
                    params=params(parent_id) if params else None,
                )
            except NotFoundError:
                checkpoint.fail(name, NOT_FOUND)
            except RetriesExhaustedError as error:
                if not error.server_error:
                    raise
                checkpoint.fail(name, SERVER_ERROR)
        tracker.update(index + 1)
    tracker.update(len(names), final=True)


def child_records(checkpoint: Checkpoint, children: Children, parent_id: str) -> Iterator[Any]:
    """The parent's committed child records, deduped by `children.record_id` (first seen
    wins)."""
    seen: set[str] = set()
    for record in checkpoint.records(crawl_name(children.prefix, parent_id)):
        record_id = children.record_id(record)
        if record_id not in seen:
            seen.add(record_id)
            yield record


def child_total(checkpoint: Checkpoint, children: Children, parent_id: str) -> int | None:
    return checkpoint.crawl(crawl_name(children.prefix, parent_id)).total


def child_partial(
    checkpoint: Checkpoint,
    children: Children,
    parent_id: str,
    harvested: int,
    stopped: ApiOffReason | None,
) -> str | None:
    """Why the parent's child history is incomplete (None when the crawl is done).
    `harvested`: child records kept; `stopped`: why this run stopped early, if it did."""
    state = checkpoint.crawl(crawl_name(children.prefix, parent_id))
    if state.done:
        return None
    page = state.pages + 1
    if state.failure == SERVER_ERROR:
        cause = f"Trigify kept failing on page {page}"
    elif state.failure == NOT_FOUND:
        cause = f"Trigify answered not found on page {page}"
    elif stopped == "api_off":
        cause = f"the Trigify API stopped answering before page {page}"
    elif stopped == "cutoff":
        cause = f"the Cutoff passed before page {page}"
    else:
        raise RuntimeError(f"{children.prefix} of {parent_id} was never crawled")
    pages = "page" if state.pages == 1 else "pages"
    return (
        f"{children.history} incomplete: {cause}; {harvested} {children.records} from"
        f" {state.pages} {pages} harvested"
    )
