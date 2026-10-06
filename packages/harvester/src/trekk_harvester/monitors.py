"""The Monitors step: list the tracked profiles (Monitors), then walk each Monitor's engagement
results and, per post those results name, the post's own results.

The spec has no Monitor listing: Monitors are the profile URLs given with `--monitors` in this
or any earlier run (crawl `monitor_inputs`, in the order first given)
followed by those the legacy tracked-profile listing names (crawl `legacy__profile_track`),
deduped. A Monitor's id is its profile URL. Results carry no id: each gets `result_id`. Crawls
`monitor_results__<url>` hold a Monitor's results; crawls `monitor_post_results__<key>` (key:
canonical JSON `[profile_url, post_url]`) hold one post's results."""

import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from trekk_archive import ArchiveObject, Kind, Monitor, MonitorPostResult, MonitorResult, Partial
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.children import (
    Children,
    canonical,
    child_partial,
    child_records,
    child_total,
    crawl_children,
    result_id,
)
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason
from trekk_harvester.events import Reporter
from trekk_harvester.features import NOT_AVAILABLE
from trekk_harvester.legacy import TRACK_CRAWL, request_route
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.report import Tally
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import TrigifyPort

STEPS = ("monitor_list", "monitor_results", "monitor_post_results")
INPUTS_CRAWL = "monitor_inputs"
LIST_STEP, RESULTS_STEP, POST_RESULTS_STEP = STEPS
NOT_LISTED: tuple[Kind, ...] = ("monitor", "monitor_result", "monitor_post_result")
RESULTS = Children(
    prefix="monitor_results",
    operation="post_profile_engagement_results",
    object_type="monitor",
    phase="results",
    history="result history",
    records="results",
    record_id=result_id,
)
POST_RESULTS = Children(
    prefix="monitor_post_results",
    operation="post_profile_engagement_post_results",
    object_type="monitor_post",
    phase="results",
    history="post result history",
    records="post results",
    record_id=result_id,
)


async def harvest_monitors(
    runner: Runner,
    client: TrigifyPort,
    checkpoint: Checkpoint,
    clock: Clock,
    reporter: Reporter,
    monitors: Sequence[str],
) -> None:
    """`monitors`: profile URLs given by the user (`--monitors`); kept in the checkpoint, so a
    later run without them still harvests and archives them."""
    known = set(checkpoint.records(INPUTS_CRAWL))
    new = list(dict.fromkeys(url for url in monitors if url not in known))
    if new:
        checkpoint.commit(INPUTS_CRAWL, new, cursor=None, done=True)
    await request_route(runner, client, checkpoint, clock, reporter, TRACK_CRAWL)
    checkpoint.complete(LIST_STEP)
    urls = list(_monitors(checkpoint))
    ProgressTracker(clock, reporter, "monitor", "listing", start=0).update(len(urls), final=True)
    await crawl_children(
        runner, client, checkpoint, clock, reporter, RESULTS, urls, params=_profile
    )
    checkpoint.complete(RESULTS_STEP)
    posts = [_post_key(url, post) for url in urls for post in _posts(checkpoint, url)]
    await crawl_children(
        runner, client, checkpoint, clock, reporter, POST_RESULTS, posts, params=_post
    )
    checkpoint.complete(POST_RESULTS_STEP)


def _profile(url: str) -> Mapping[str, Any]:
    return {"profile_url": url}


def _post_key(url: str, post: str) -> str:
    return canonical([url, post])


def _post(key: str) -> Mapping[str, Any]:
    url, post = json.loads(key)
    return {"profile_url": url, "post_url": post}


def _monitors(checkpoint: Checkpoint) -> dict[str, Any]:
    """Monitor URL -> its legacy listing item (None when only given by the user): the given
    URLs first, then the listed ones, each once."""
    found: dict[str, Any] = dict.fromkeys(checkpoint.records(INPUTS_CRAWL))
    for item in checkpoint.records(TRACK_CRAWL):
        url = item.get("profile_url") if isinstance(item, dict) else None
        if isinstance(url, str) and url and found.get(url) is None:
            found[url] = item
    return found


def _posts(checkpoint: Checkpoint, url: str) -> list[str]:
    """The distinct post URLs a Monitor's harvested results name, in result order."""
    posts = (result.get("post_url") for result in child_records(checkpoint, RESULTS, url))
    return list(dict.fromkeys(post for post in posts if isinstance(post, str) and post))


def monitor_objects(
    checkpoint: Checkpoint,
    stopped: ApiOffReason | None,
    tally: Tally,
) -> Iterator[ArchiveObject]:
    """Per Monitor, in order: its results, its posts' results, then the Monitor (`data`: its
    listing item, else `{"profile_url": url}`), marked partial when a result history is
    incomplete (`stopped`: why this run stopped early, if it did)."""
    for url, item in _monitors(checkpoint).items():
        reasons: list[str] = []
        harvested = 0
        for result in child_records(checkpoint, RESULTS, url):
            harvested += 1
            yield MonitorResult(
                trigify_id=result_id(result), source="api", monitor_id=url, data=result
            )
        tally.listed("monitor_result", child_total(checkpoint, RESULTS, url))
        reasons += _partial(checkpoint, RESULTS, url, harvested, stopped)
        for post in _posts(checkpoint, url):
            key = _post_key(url, post)
            harvested = 0
            for result in child_records(checkpoint, POST_RESULTS, key):
                harvested += 1
                yield MonitorPostResult(
                    trigify_id=result_id(result),
                    source="api",
                    monitor_id=url,
                    post_url=post,
                    data=result,
                )
            tally.listed("monitor_post_result", child_total(checkpoint, POST_RESULTS, key))
            reasons += _partial(checkpoint, POST_RESULTS, key, harvested, stopped)
        yield Monitor(
            trigify_id=url,
            source="api",
            data=item if isinstance(item, dict) else {"profile_url": url},
            partial=Partial(reason="; ".join(reasons)) if reasons else None,
        )


def _partial(
    checkpoint: Checkpoint,
    children: Children,
    parent: str,
    harvested: int,
    stopped: ApiOffReason | None,
) -> list[str]:
    reason = child_partial(checkpoint, children, parent, harvested, stopped)
    return [] if reason is None else [reason]


def not_listed(checkpoint: Checkpoint) -> list[Kind]:
    """The Monitor kinds when there was no source naming Monitors: none given and the legacy
    listing not available."""
    given = checkpoint.crawl(INPUTS_CRAWL).size > 0
    unlisted = not given and checkpoint.crawl(TRACK_CRAWL).failure == NOT_AVAILABLE
    return list(NOT_LISTED) if unlisted else []
