"""The Lists step: workflow tables, the one kind of this group the Trigify API can list.

Lists, List members, workflow table rows and Agent Memory have no operation in `spec/trigify/`:
`missing_kinds()` derives them at runtime from the generated `OPERATIONS` (one path pattern per
kind in `GROUP`), and the manifest records them as `missing`.

Crawl `workflow_tables` holds the one unpaged answer of `get_workflow_tables`, committed at once
(no partial page exists). A 403 or 404 means the workspace never used workflow tables: the crawl
is done, empty, and `workflow_table` goes to the manifest's `not_used`."""

import re
from collections.abc import Iterator
from typing import Any

from trekk_archive import KINDS, ArchiveObject, Kind, MissingKind, WorkflowTable
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.children import result_id
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason
from trekk_harvester.events import Reporter
from trekk_harvester.features import NOT_USED, gate
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.report import Tally
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import OPERATIONS, TrigifyPort

STEPS = ("workflow_tables",)
(TABLES_STEP,) = STEPS
TABLES_CRAWL = "workflow_tables"
TABLES_OPERATION = "get_workflow_tables"
NO_OPERATION = "no API operation"
FEATURES: dict[str, tuple[Kind, ...]] = {TABLES_CRAWL: ("workflow_table",)}

# kind -> the GET path that would list it (any API version)
GROUP: dict[Kind, re.Pattern[str]] = {
    "workflow_table": re.compile(r"/v\d+/workflow-tables"),
    "workflow_table_row": re.compile(r"/v\d+/workflow-tables/\{[^/]+\}/rows"),
    "list": re.compile(r"/v\d+/lists"),
    "list_member": re.compile(r"/v\d+/lists/\{[^/]+\}/members"),
    "agent_memory_table": re.compile(r"/v\d+/agent-memory(?:/tables)?"),
    "agent_memory_record": re.compile(r"/v\d+/agent-memory(?:/tables)?/\{[^/]+\}/records"),
}


def missing_kinds() -> list[Kind]:
    """Group kinds no GET operation of the spec lists, in canonical kind order."""
    paths = [operation.path for operation in OPERATIONS.values() if operation.method == "GET"]
    return [
        kind
        for kind in KINDS
        if kind in GROUP and not any(GROUP[kind].fullmatch(path) for path in paths)
    ]


def missing() -> list[MissingKind]:
    return [
        MissingKind(kind=kind, source="missing", reason=NO_OPERATION) for kind in missing_kinds()
    ]


async def harvest_lists(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    """Request the workflow tables once (unless done) and commit them."""
    if not checkpoint.crawl(TABLES_CRAWL).done:
        with gate(checkpoint, TABLES_CRAWL, NOT_USED):
            answer = await runner.call(getattr(client, TABLES_OPERATION))
            data = answer.model_dump(mode="json", by_alias=True, exclude_unset=True).get("data")
            checkpoint.commit(TABLES_CRAWL, data or [], cursor=None, done=True)
    tables = sum(1 for _ in checkpoint.records(TABLES_CRAWL))
    ProgressTracker(clock, reporter, "workflow_table", "listing", start=0).update(
        tables, final=True
    )
    checkpoint.complete(TABLES_STEP)


def list_objects(
    checkpoint: Checkpoint, stopped: ApiOffReason | None, tally: Tally
) -> Iterator[ArchiveObject]:
    """One `workflow_table` per listed table, deduped by id (first seen); a table without a
    usable `id` gets the `sha256:` id of its JSON. Trigify gives no workflow reference. The
    listed total is the answer's length (unknown until the tables were requested)."""
    state = checkpoint.crawl(TABLES_CRAWL)
    seen: set[str] = set()
    listed = 0
    for item in checkpoint.records(TABLES_CRAWL):
        listed += 1
        trigify_id = _table_id(item)
        if trigify_id not in seen:
            seen.add(trigify_id)
            yield WorkflowTable(trigify_id=trigify_id, source="api", workflow_id=None, data=item)
    tally.listed("workflow_table", listed if state.done else None)


def _table_id(item: Any) -> str:
    table_id = item.get("id")
    return table_id if isinstance(table_id, str) and table_id else result_id(item)
