"""The workflows step: list every workflow (every state: no `status` filter), fetch each one's
detail and draft, walk each workflow's Runs, then fetch each Run's detail and variables.

Crawl `workflows` holds the list pages; crawls `workflow_details`, `workflow_drafts`,
`run_details` and `run_variables` hold per-object records (`details.py`; a Run's key is the
canonical JSON `[workflow_id, run_id]`); crawls `workflow_runs__<id>` hold each workflow's Run
summaries. A workflow whose detail answered 404 gets no draft or Runs request; a Run whose
detail answered 404 gets no variables request. A draft that answers 404 means the workflow has
none.

The spec has no Output operation: a Run's Output rows are its detail's ordered steps, one row per
step (identical steps share an id and count once). Steps with a non-null `iteration` ran inside a
Loop: their `loop_index` is that iteration when it is a non-negative whole number. Trigify gives
no parent reference, so `parent_row_id` is always null."""

import json
from collections.abc import Iterator, Mapping
from typing import Any

from trekk_archive import ArchiveObject, Kind, OutputRow, Partial, Run, Workflow, WorkflowDraft
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.children import (
    NOT_FOUND,
    PAGE_SIZE,
    SERVER_ERROR,
    Children,
    canonical,
    child_partial,
    child_records,
    child_total,
    crawl_children,
    result_id,
)
from trekk_harvester.clock import Clock
from trekk_harvester.details import (
    Details,
    Reasons,
    found,
    harvest_details,
    latest_records,
    missing_reason,
)
from trekk_harvester.errors import ApiOffReason
from trekk_harvester.events import Reporter
from trekk_harvester.features import NOT_USED, gate
from trekk_harvester.paging import crawl_pages
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.report import Tally
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import TrigifyPort

STEPS = (
    "workflow_list",
    "workflow_details",
    "workflow_drafts",
    "workflow_runs",
    "run_details",
    "run_variables",
)
LIST_STEP, DETAILS_STEP, DRAFTS_STEP, RUNS_STEP, RUN_DETAILS_STEP, VARIABLES_STEP = STEPS
LIST_CRAWL = "workflows"
FEATURES: dict[str, tuple[Kind, ...]] = {
    LIST_CRAWL: ("workflow", "workflow_draft", "run", "output_row")
}
LIST_OPERATION = "get_workflows"


def _run_id(summary: Any) -> str:
    run_id: str = summary["run_id"]
    return run_id


def run_key(workflow_id: str, run_id: str) -> str:
    return canonical([workflow_id, run_id])


def _run_path(key: str) -> tuple[str, ...]:
    return tuple(json.loads(key))


DETAILS = Details(
    crawl="workflow_details",
    operation="get_workflows_by_id",
    object_type="workflow",
    phase="details",
)
DRAFTS = Details(
    crawl="workflow_drafts",
    operation="get_workflows_by_id_draft",
    object_type="workflow",
    phase="drafts",
)
RUNS = Children(
    prefix="workflow_runs",
    operation="get_workflows_by_id_executions",
    object_type="workflow",
    phase="runs",
    history="Run history",
    records="Runs",
    record_id=_run_id,
)
RUN_DETAILS = Details(
    crawl="run_details",
    operation="get_workflows_by_id_executions_by_runid",
    object_type="run",
    phase="details",
    path=_run_path,
)
VARIABLES = Details(
    crawl="run_variables",
    operation="get_workflows_by_id_executions_by_runid_variables",
    object_type="run",
    phase="variables",
    path=_run_path,
)

DETAIL_REASONS = Reasons(
    empty="Trigify returned no detail for this workflow; the list item is kept",
    failed={
        NOT_FOUND: "Trigify answered not found for this workflow's detail (deleted after"
        " listing); the list item is kept",
        SERVER_ERROR: "Trigify kept failing on this workflow's detail; the list item is kept",
    },
    stopped={
        "api_off": "the Trigify API stopped answering before this workflow's detail was harvested",
        "cutoff": "the Cutoff passed before this workflow's detail was harvested",
    },
)
DRAFT_REASONS = Reasons(
    empty="draft unavailable: Trigify returned none",
    failed={SERVER_ERROR: "draft unavailable: Trigify kept failing"},
    stopped={
        "api_off": "draft unavailable: the Trigify API stopped answering before it was harvested",
        "cutoff": "draft unavailable: the Cutoff passed before it was harvested",
    },
)
RUN_DETAIL_REASONS = Reasons(
    empty="Trigify returned no detail for this Run; the list summary is kept",
    failed={
        NOT_FOUND: "Trigify answered not found for this Run's detail; the list summary is kept",
        SERVER_ERROR: "Trigify kept failing on this Run's detail; the list summary is kept",
    },
    stopped={
        "api_off": "the Trigify API stopped answering before this Run's detail was harvested",
        "cutoff": "the Cutoff passed before this Run's detail was harvested",
    },
)
VARIABLE_REASONS = Reasons(
    empty="variables unavailable: Trigify returned none",
    failed={
        NOT_FOUND: "variables unavailable",
        SERVER_ERROR: "variables unavailable: Trigify kept failing",
    },
    stopped={
        "api_off": "variables unavailable: the Trigify API stopped answering before they were"
        " harvested",
        "cutoff": "variables unavailable: the Cutoff passed before they were harvested",
    },
)


async def harvest_workflows(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    listing = ProgressTracker(clock, reporter, "workflow", "listing", start=0)
    with gate(checkpoint, LIST_CRAWL, NOT_USED):
        await crawl_pages(
            runner,
            client,
            checkpoint,
            LIST_CRAWL,
            LIST_OPERATION,
            size=PAGE_SIZE,
            on_page=listing.update,
        )
    checkpoint.complete(LIST_STEP)
    ids = list(_workflows(checkpoint))
    listing.update(len(ids), final=True)
    await harvest_details(runner, client, checkpoint, clock, reporter, DETAILS, ids)
    checkpoint.complete(DETAILS_STEP)
    present = found(latest_records(checkpoint, DETAILS), ids)
    await harvest_details(runner, client, checkpoint, clock, reporter, DRAFTS, present)
    checkpoint.complete(DRAFTS_STEP)
    await crawl_children(runner, client, checkpoint, clock, reporter, RUNS, present)
    checkpoint.complete(RUNS_STEP)
    runs = [
        run_key(workflow_id, _run_id(summary))
        for workflow_id in present
        for summary in child_records(checkpoint, RUNS, workflow_id)
    ]
    await harvest_details(runner, client, checkpoint, clock, reporter, RUN_DETAILS, runs)
    checkpoint.complete(RUN_DETAILS_STEP)
    detailed = found(latest_records(checkpoint, RUN_DETAILS), runs)
    await harvest_details(runner, client, checkpoint, clock, reporter, VARIABLES, detailed)
    checkpoint.complete(VARIABLES_STEP)


def _workflows(checkpoint: Checkpoint) -> dict[str, Any]:
    """Listed workflows by id, in list order; a workflow listed twice counts once (first
    seen)."""
    workflows: dict[str, Any] = {}
    for item in checkpoint.records(LIST_CRAWL):
        workflows.setdefault(item["id"], item)
    return workflows


def workflow_objects(
    checkpoint: Checkpoint, stopped: ApiOffReason | None, tally: Tally
) -> Iterator[ArchiveObject]:
    """Per listed workflow, in list order: each Run's Output rows then the Run, the draft, then
    the workflow (its detail plus the list-only fields, else the list item). Partial reasons
    come from the detail, the draft and the Run history (`stopped`: why this run stopped early,
    if it did)."""
    tally.listed("workflow", checkpoint.crawl(LIST_CRAWL).total)
    details = latest_records(checkpoint, DETAILS)
    drafts = latest_records(checkpoint, DRAFTS)
    run_details = latest_records(checkpoint, RUN_DETAILS)
    variables = latest_records(checkpoint, VARIABLES)
    for workflow_id, item in _workflows(checkpoint).items():
        record = details.get(workflow_id)
        detail = None if record is None else record["data"]
        reasons = (
            []
            if detail is not None
            else [missing_reason(workflow_id, record, stopped, DETAIL_REASONS)]
        )
        # A detail that answered 404 has no draft or Run history to complete; what an earlier
        # run harvested for it is kept all the same.
        gone = record is not None and record.get("failure") == NOT_FOUND
        draft_record = drafts.get(workflow_id)
        draft = None if draft_record is None else draft_record["data"]
        no_draft = draft_record is not None and draft_record.get("failure") == NOT_FOUND
        if draft is None and not no_draft and not gone:
            reasons.append(missing_reason(workflow_id, draft_record, stopped, DRAFT_REASONS))
        harvested = 0
        for summary in child_records(checkpoint, RUNS, workflow_id):
            harvested += 1
            yield from _run(workflow_id, summary, run_details, variables, stopped)
        if not gone:
            tally.listed("run", child_total(checkpoint, RUNS, workflow_id))
            history = child_partial(checkpoint, RUNS, workflow_id, harvested, stopped)
            if history is not None:
                reasons.append(history)
        if draft is not None:
            yield WorkflowDraft(
                trigify_id=draft["id"], source="api", workflow_id=workflow_id, data=draft
            )
        yield Workflow(
            trigify_id=workflow_id,
            source="api",
            data=item if detail is None else {**item, **detail},
            partial=Partial(reason="; ".join(reasons)) if reasons else None,
        )


def _run(
    workflow_id: str,
    summary: dict[str, Any],
    run_details: Mapping[str, dict[str, Any]],
    variables: Mapping[str, dict[str, Any]],
    stopped: ApiOffReason | None,
) -> Iterator[ArchiveObject]:
    """A Run's Output rows, then the Run (`data`: list summary, detail and variables)."""
    run_id = _run_id(summary)
    key = run_key(workflow_id, run_id)
    record = run_details.get(key)
    detail = None if record is None else record["data"]
    reasons = (
        [] if detail is not None else [missing_reason(key, record, stopped, RUN_DETAIL_REASONS)]
    )
    gone = record is not None and record.get("failure") == NOT_FOUND
    variables_record = variables.get(key)
    materialized = None if variables_record is None else variables_record["data"]
    if materialized is None and not gone:
        reasons.append(missing_reason(key, variables_record, stopped, VARIABLE_REASONS))
    seen: set[str] = set()
    for step in [] if detail is None else detail.get("steps") or []:
        row_id = result_id(step)
        if row_id not in seen:
            seen.add(row_id)
            yield OutputRow(
                trigify_id=row_id,
                source="api",
                workflow_id=workflow_id,
                run_id=run_id,
                parent_row_id=None,
                loop_index=loop_index(step),
                data=step,
            )
    yield Run(
        trigify_id=run_id,
        source="api",
        workflow_id=workflow_id,
        data={"summary": summary, "detail": detail, "variables": materialized},
        partial=Partial(reason="; ".join(reasons)) if reasons else None,
    )


def loop_index(step: Any) -> int | None:
    """The Loop iteration a step ran in, when it is a non-negative whole number."""
    iteration = step.get("iteration") if isinstance(step, dict) else None
    if isinstance(iteration, bool) or not isinstance(iteration, int | float):
        return None
    if iteration < 0 or not float(iteration).is_integer():
        return None
    return int(iteration)
