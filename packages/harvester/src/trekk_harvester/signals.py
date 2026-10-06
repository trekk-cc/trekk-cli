"""The Social Signals step: every subscription (every status), every Target and the whole feed
(Signals and Insights).

Crawl `signal_subscriptions` holds the subscriptions (one unpaged answer); crawls
`signal_targets` and `signal_feed` hold the Target rows and feed items, page by page. When the
first request of a list answers 403 or 404, the workspace never used the feature: the crawl is
marked done as not used and its kinds go to the manifest's `not_used` (`features.gate`)."""

from collections.abc import Iterator
from typing import Any

from trekk_archive import ArchiveObject, Insight, Kind, Signal, SignalSubscription, Target
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.children import PAGE_SIZE
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason
from trekk_harvester.events import ObjectType, Reporter
from trekk_harvester.features import NOT_USED, gate
from trekk_harvester.paging import crawl_pages
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.report import Tally
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import TrigifyPort

STEPS = ("signal_subscriptions", "signal_targets", "signal_feed")
SUBSCRIPTIONS_STEP, TARGETS_STEP, FEED_STEP = STEPS
SUBSCRIPTIONS_CRAWL, TARGETS_CRAWL, FEED_CRAWL = STEPS
SUBSCRIPTIONS = "get_social_signals_subscriptions"
TARGETS = "get_social_signals_targets"
FEED = "get_social_signals_feed"
# crawl -> the kinds it holds
FEATURES: dict[str, tuple[Kind, ...]] = {
    SUBSCRIPTIONS_CRAWL: ("signal_subscription",),
    TARGETS_CRAWL: ("target",),
    FEED_CRAWL: ("signal", "insight"),
}


async def harvest_signals(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    await _subscriptions(runner, client, checkpoint, clock, reporter)
    checkpoint.complete(SUBSCRIPTIONS_STEP)
    await _list(runner, client, checkpoint, clock, reporter, TARGETS_CRAWL, TARGETS, "target", {})
    checkpoint.complete(TARGETS_STEP)
    feed = {"content_type": "all"}
    await _list(runner, client, checkpoint, clock, reporter, FEED_CRAWL, FEED, "feed", feed)
    checkpoint.complete(FEED_STEP)


async def _subscriptions(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    """Every subscription, whatever its status (no `status` filter): one unpaged answer."""
    if checkpoint.crawl(SUBSCRIPTIONS_CRAWL).done:
        return
    with gate(checkpoint, SUBSCRIPTIONS_CRAWL, NOT_USED):
        answer = await runner.call(getattr(client, SUBSCRIPTIONS))
        items = answer.model_dump(mode="json", by_alias=True, exclude_unset=True)["data"]
        checkpoint.commit(SUBSCRIPTIONS_CRAWL, items, cursor=None, done=True, total=len(items))
        tracker = ProgressTracker(clock, reporter, "signal_subscription", "listing", start=0)
        tracker.update(len(items), final=True)


async def _list(
    runner: Runner,
    client: TrigifyPort,
    checkpoint: Checkpoint,
    clock: Clock,
    reporter: Reporter,
    crawl: str,
    operation: str,
    object_type: ObjectType,
    params: dict[str, Any],
) -> None:
    """Page through a list to its end; a 403/404 on its first page means not used."""
    state = checkpoint.crawl(crawl)
    if state.done:
        return
    listing = ProgressTracker(clock, reporter, object_type, "listing", start=0)
    with gate(checkpoint, crawl, NOT_USED):
        await crawl_pages(
            runner,
            client,
            checkpoint,
            crawl,
            operation,
            size=PAGE_SIZE,
            params=params,
            on_page=listing.update,
        )
        listing.update(sum(1 for _ in checkpoint.records(crawl)), final=True)


def _unique(records: Iterator[Any], key: str) -> dict[str, Any]:
    """Records by `key`, first seen wins (a list can shift between pages)."""
    found: dict[str, Any] = {}
    for record in records:
        found.setdefault(record[key], record)
    return found


def _feed(checkpoint: Checkpoint) -> tuple[dict[str, Any], dict[str, Any]]:
    """Feed items as (Signals by Signal id, Insights by Insight id), first seen wins."""
    signals: dict[str, Any] = {}
    insights: dict[str, Any] = {}
    for item in checkpoint.records(FEED_CRAWL):
        match item["item_type"]:
            case "SIGNAL":
                signals.setdefault(item["signal"]["id"], item)
            case "INSIGHT":
                insights.setdefault(item["insight"]["id"], item)
            case other:
                raise ValueError(f"feed item {item['id']}: unknown item_type {other!r}")
    return signals, insights


def signal_objects(
    checkpoint: Checkpoint, stopped: ApiOffReason | None, tally: Tally
) -> Iterator[ArchiveObject]:
    """Subscriptions, Targets, Signals and Insights as Trigify returned them. A list that did
    not finish keeps its step incomplete, so the Archive stays retryable."""
    for trigify_id, item in _unique(checkpoint.records(SUBSCRIPTIONS_CRAWL), "id").items():
        yield SignalSubscription(trigify_id=trigify_id, source="api", data=item)
    tally.listed("target", checkpoint.crawl(TARGETS_CRAWL).total)
    for trigify_id, row in _unique(checkpoint.records(TARGETS_CRAWL), "target_id").items():
        yield Target(trigify_id=trigify_id, source="api", target_list_id=None, data=row)
    signals, insights = _feed(checkpoint)
    for trigify_id, item in signals.items():
        yield Signal(trigify_id=trigify_id, source="api", data=item)
    for trigify_id, item in insights.items():
        yield Insight(trigify_id=trigify_id, source="api", data=item)


def signal_types(checkpoint: Checkpoint) -> list[str]:
    """Signal types in use: those configured on harvested subscriptions and those of harvested
    Signals, sorted and unique."""
    types: set[str] = set()
    for subscription in checkpoint.records(SUBSCRIPTIONS_CRAWL):
        config = subscription.get("config")
        if isinstance(config, dict) and isinstance(config.get("signals"), list):
            types.update(
                signal["type"]
                for signal in config["signals"]
                if isinstance(signal, dict) and isinstance(signal.get("type"), str)
            )
    signals, _ = _feed(checkpoint)
    types.update(
        item["signal"]["signal_type"]
        for item in signals.values()
        if isinstance(item["signal"].get("signal_type"), str)
    )
    return sorted(types)
