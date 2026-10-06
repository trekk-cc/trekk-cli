"""Legacy routes outside the published API (`spec/trigify/legacy.json`): each is one
unpaginated GET, requested once and kept as returned.

Crawl `legacy__profile_track` (requested by the Monitors step: its listing names the Monitors)
and crawl `legacy__social_signals` (requested by the legacy step) hold the items: the `data`
list's items, or the single `data` object. A route that answers 403 or 404 is marked not
available (`features.gate`); any other failure is a top-level request failure."""

from collections.abc import Iterator

from trekk_archive import ArchiveObject, LegacyRoute
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason
from trekk_harvester.events import Reporter
from trekk_harvester.features import NOT_AVAILABLE, gate
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.report import Tally
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import OPERATIONS, TrigifyPort

STEPS = ("legacy_routes",)
(ROUTES_STEP,) = STEPS
TRACK_CRAWL = "legacy__profile_track"
SIGNALS_CRAWL = "legacy__social_signals"
# crawl -> operation, in Archive order
ROUTES = {TRACK_CRAWL: "get_profile_track", SIGNALS_CRAWL: "get_social_signals"}


async def request_route(
    runner: Runner,
    client: TrigifyPort,
    checkpoint: Checkpoint,
    clock: Clock,
    reporter: Reporter,
    crawl: str,
) -> None:
    """Request the crawl's legacy route once (unless done) and commit its items."""
    if checkpoint.crawl(crawl).done:
        return
    operation = ROUTES[crawl]
    with gate(checkpoint, crawl, NOT_AVAILABLE):
        answer = await runner.call(getattr(client, operation))
        data = answer.model_dump(mode="json", by_alias=True, exclude_unset=True).get("data")
        items = data if isinstance(data, list) else [] if data is None else [data]
        checkpoint.commit(crawl, items, cursor=None, done=True)
        ProgressTracker(clock, reporter, "legacy_route", "listing", start=0).update(
            len(items), final=True
        )


async def harvest_legacy(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    await request_route(runner, client, checkpoint, clock, reporter, SIGNALS_CRAWL)
    checkpoint.complete(ROUTES_STEP)


def legacy_objects(
    checkpoint: Checkpoint, stopped: ApiOffReason | None, tally: Tally
) -> Iterator[ArchiveObject]:
    """One `legacy_route` per item, keyed by the item's `id` (else the route path); an item
    that is not a JSON object is kept under `value`. Unpaginated routes give no list total;
    a route not yet requested has no items (its step stays incomplete)."""
    for crawl, operation in ROUTES.items():
        route = OPERATIONS[operation].path
        for item in checkpoint.records(crawl):
            data = item if isinstance(item, dict) else {"value": item}
            trigify_id = data.get("id")
            yield LegacyRoute(
                trigify_id=str(trigify_id) if trigify_id not in (None, "") else route,
                source="api",
                route=route,
                data=data,
            )


def not_available(checkpoint: Checkpoint) -> list[str]:
    """Paths of the legacy routes that answered 403 or 404."""
    return [
        OPERATIONS[operation].path
        for crawl, operation in ROUTES.items()
        if checkpoint.crawl(crawl).failure == NOT_AVAILABLE
    ]
