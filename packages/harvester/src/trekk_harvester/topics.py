"""The topics step: list every topic, then walk each topic's engagements.

Crawl `topics` holds the list pages (each item is the full topic: no detail request);
crawls `topic_engagements__<id>` hold each topic's engagements."""

from collections.abc import Iterator
from typing import Any

from trekk_archive import ArchiveObject, Kind, Partial, Topic, TopicEngagement
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.children import (
    PAGE_SIZE,
    Children,
    child_partial,
    child_records,
    child_total,
    crawl_children,
)
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason
from trekk_harvester.events import Reporter
from trekk_harvester.features import NOT_USED, gate
from trekk_harvester.paging import crawl_pages
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.report import Tally
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import TrigifyPort

STEPS = ("topic_list", "topic_engagements")
LIST_STEP, ENGAGEMENTS_STEP = STEPS
LIST_CRAWL = "topics"
FEATURES: dict[str, tuple[Kind, ...]] = {LIST_CRAWL: ("topic", "topic_engagement")}
LIST_OPERATION = "get_topics"
ENGAGEMENTS = Children(
    prefix="topic_engagements",
    operation="get_topics_by_id_engagements",
    object_type="topic",
    phase="engagements",
    history="engagement history",
    records="engagements",
)


async def harvest_topics(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    listing = ProgressTracker(clock, reporter, "topic", "listing", start=0)
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
    ids = list(_topics(checkpoint))
    listing.update(len(ids), final=True)
    await crawl_children(runner, client, checkpoint, clock, reporter, ENGAGEMENTS, ids)
    checkpoint.complete(ENGAGEMENTS_STEP)


def _topics(checkpoint: Checkpoint) -> dict[str, Any]:
    """Listed topics by id, in list order; a topic listed twice counts once (first seen)."""
    topics: dict[str, Any] = {}
    for topic in checkpoint.records(LIST_CRAWL):
        topics.setdefault(topic["id"], topic)
    return topics


def topic_objects(
    checkpoint: Checkpoint, stopped: ApiOffReason | None, tally: Tally
) -> Iterator[ArchiveObject]:
    """One topic per listed id, in list order, after its engagements; marked partial when its
    engagement history is incomplete (`stopped`: why this run stopped early, if it did)."""
    tally.listed("topic", checkpoint.crawl(LIST_CRAWL).total)
    for trigify_id, topic in _topics(checkpoint).items():
        harvested = 0
        for engagement in child_records(checkpoint, ENGAGEMENTS, trigify_id):
            harvested += 1
            yield TopicEngagement(
                trigify_id=engagement["id"], source="api", topic_id=trigify_id, data=engagement
            )
        tally.listed("topic_engagement", child_total(checkpoint, ENGAGEMENTS, trigify_id))
        reason = child_partial(checkpoint, ENGAGEMENTS, trigify_id, harvested, stopped)
        yield Topic(
            trigify_id=trigify_id,
            source="api",
            data=topic,
            partial=None if reason is None else Partial(reason=reason),
        )
