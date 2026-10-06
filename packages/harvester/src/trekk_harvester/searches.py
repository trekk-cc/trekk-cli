"""The Searches step: list every Search, fetch each one's detail in list order, then walk each
Search's result history.

Crawl `searches` holds the list pages (Search summaries); crawl `search_details` holds the
detail records (`details.py`). Crawls `search_results__<id>` hold each Search's results (none
for a Search whose detail answered 404)."""

from collections.abc import Iterator
from typing import Any

from trekk_archive import ArchiveObject, Kind, Partial, Search, SearchResult
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.children import (
    NOT_FOUND,
    PAGE_SIZE,
    SERVER_ERROR,
    Children,
    child_partial,
    child_records,
    child_total,
    crawl_children,
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

STEPS = ("search_list", "search_details", "search_results")
LIST_STEP, DETAILS_STEP, RESULTS_STEP = STEPS
LIST_CRAWL = "searches"
FEATURES: dict[str, tuple[Kind, ...]] = {LIST_CRAWL: ("search", "search_result")}
LIST_OPERATION = "get_searches"
DETAILS = Details(
    crawl="search_details", operation="get_searches_by_id", object_type="search", phase="details"
)
RESULTS = Children(
    prefix="search_results",
    operation="get_searches_by_id_results",
    object_type="search",
    phase="results",
    history="result history",
    records="results",
)

NO_DETAIL = "Trigify returned no detail for this Search; the list summary is kept"
DETAIL_FAILED = {
    NOT_FOUND: "Trigify answered not found for this Search's detail (deleted after listing);"
    " the list summary is kept",
    SERVER_ERROR: "Trigify kept failing on this Search's detail; the list summary is kept",
}
NOT_DETAILED: dict[ApiOffReason, str] = {
    "api_off": "the Trigify API stopped answering before this Search's detail was harvested",
    "cutoff": "the Cutoff passed before this Search's detail was harvested",
}
DETAIL_REASONS = Reasons(empty=NO_DETAIL, failed=DETAIL_FAILED, stopped=NOT_DETAILED)


async def harvest_searches(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    listing = ProgressTracker(clock, reporter, "search", "listing", start=0)
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
    ids = listed_ids(checkpoint)
    listing.update(len(ids), final=True)
    await harvest_details(runner, client, checkpoint, clock, reporter, DETAILS, ids)
    checkpoint.complete(DETAILS_STEP)
    with_results = found(latest_records(checkpoint, DETAILS), ids)
    await crawl_children(runner, client, checkpoint, clock, reporter, RESULTS, with_results)
    checkpoint.complete(RESULTS_STEP)


def listed_ids(checkpoint: Checkpoint) -> list[str]:
    """Listed Search ids in list order. The list can shift between pages: a Search listed
    twice counts once (first seen wins)."""
    return list(dict.fromkeys(summary["id"] for summary in checkpoint.records(LIST_CRAWL)))


def search_objects(
    checkpoint: Checkpoint, stopped: ApiOffReason | None, tally: Tally
) -> Iterator[ArchiveObject]:
    """One Search per listed id, in list order, after its results: the detail when harvested,
    else the summary; marked partial when its detail or result history is incomplete
    (`stopped` says why this run stopped early, if it did). Harvested results are always kept;
    a Search whose detail answered 404 has no result history to complete."""
    latest = latest_records(checkpoint, DETAILS)
    summaries: dict[str, Any] = {}
    for summary in checkpoint.records(LIST_CRAWL):
        summaries.setdefault(summary["id"], summary)
    tally.listed("search", checkpoint.crawl(LIST_CRAWL).total)
    for trigify_id, summary in summaries.items():
        record = latest.get(trigify_id)
        detail = None if record is None else record["data"]
        reasons = (
            []
            if detail is not None
            else [missing_reason(trigify_id, record, stopped, DETAIL_REASONS)]
        )
        harvested = 0
        for result in child_records(checkpoint, RESULTS, trigify_id):
            harvested += 1
            yield SearchResult(
                trigify_id=result["id"], source="api", search_id=trigify_id, data=result
            )
        if record is None or record.get("failure") != NOT_FOUND:
            tally.listed("search_result", child_total(checkpoint, RESULTS, trigify_id))
            history = child_partial(checkpoint, RESULTS, trigify_id, harvested, stopped)
            if history is not None:
                reasons.append(history)
        yield Search(
            trigify_id=trigify_id,
            source="api",
            data=summary if detail is None else detail,
            partial=Partial(reason="; ".join(reasons)) if reasons else None,
        )
