"""The organisation steps: the workspace's org metadata, its Credit ledger and its usage.

Crawl `org` holds the `get_org` answer `harvest()` already fetched (`commit_org`); crawls
`org_account`, `org_credits_balance` and `org_list` hold the other org parts, one answer each
(`org_list`: only this workspace's entry of the key owner's organisation list). A part whose
route answers 403 or 404 is marked not available (its path goes to the manifest's
`not_available`). Crawl `credit_ledger` holds the Credit usage records of the last 365 days (the
API's maximum window), page by page; crawl `usage` holds one usage answer for Trigify's default
period. A 403 or 404 on the ledger's first page or on the usage request means the feature is not
used (`features.gate`)."""

from collections.abc import Iterator
from typing import Any

from trekk_archive import ArchiveObject, CreditLedgerEntry, Kind, Org, Partial, UsageRecord
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.children import result_id
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason
from trekk_harvester.events import Reporter
from trekk_harvester.features import NOT_AVAILABLE, NOT_USED, gate
from trekk_harvester.paging import crawl_pages
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.report import Tally
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import OPERATIONS, TrigifyPort

STEPS = ("org", "credit_ledger", "usage")
ORG_STEP, LEDGER_STEP, USAGE_STEP = STEPS
ORG_CRAWL = "org"
ACCOUNT_CRAWL = "org_account"
BALANCE_CRAWL = "org_credits_balance"
MEMBERSHIP_CRAWL = "org_list"
LEDGER_CRAWL = "credit_ledger"
USAGE_CRAWL = "usage"
# crawl -> (key in the org's `data`, operation), in `data` order after `org`
PARTS = {
    ACCOUNT_CRAWL: ("account", "get_account"),
    BALANCE_CRAWL: ("credits_balance", "get_credits_balance"),
    MEMBERSHIP_CRAWL: ("membership", "get_org_list"),
}
LEDGER = "get_credits_usage"
LEDGER_DAYS = "365"  # the API's maximum look-back
LEDGER_PAGE_SIZE = 500  # the API's maximum page size
USAGE = "get_usage"
NOT_HARVESTED: dict[ApiOffReason, str] = {
    "api_off": "the Trigify API stopped answering before this org's details were harvested",
    "cutoff": "the Cutoff passed before this org's details were harvested",
}
FEATURES: dict[str, tuple[Kind, ...]] = {
    LEDGER_CRAWL: ("credit_ledger_entry",),
    USAGE_CRAWL: ("usage_record",),
}


def _data(answer: Any) -> Any:
    return answer.model_dump(mode="json", by_alias=True, exclude_unset=True).get("data")


def commit_org(checkpoint: Checkpoint, answer: Any) -> None:
    """Keep the workspace lookup's `get_org` answer (once: a resumed run keeps the first)."""
    if not checkpoint.crawl(ORG_CRAWL).done:
        checkpoint.commit(ORG_CRAWL, [_data(answer)], cursor=None, done=True)


async def harvest_organisation(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    for crawl, (_, operation) in PARTS.items():
        if not checkpoint.crawl(crawl).done:
            with gate(checkpoint, crawl, NOT_AVAILABLE):
                data = _data(await runner.call(getattr(client, operation)))
                checkpoint.commit(crawl, _part(checkpoint, crawl, data), cursor=None, done=True)
    checkpoint.complete(ORG_STEP)
    await _ledger(runner, client, checkpoint, clock, reporter)
    checkpoint.complete(LEDGER_STEP)
    if not checkpoint.crawl(USAGE_CRAWL).done:
        with gate(checkpoint, USAGE_CRAWL, NOT_USED):
            data = _data(await runner.call(getattr(client, USAGE)))
            checkpoint.commit(USAGE_CRAWL, [data], cursor=None, done=True)
    checkpoint.complete(USAGE_STEP)


def _part(checkpoint: Checkpoint, crawl: str, data: Any) -> list[Any]:
    """The records an org part keeps: the answer, or (organisation list) this workspace's
    entry only (none when the list has no entry for it)."""
    if crawl != MEMBERSHIP_CRAWL:
        return [data]
    items = data.get("items") if isinstance(data, dict) else None
    workspace = checkpoint.workspace.id
    return [item for item in items or [] if isinstance(item, dict) and item.get("id") == workspace][
        :1
    ]


async def _ledger(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    listing = ProgressTracker(clock, reporter, "credit_ledger_entry", "listing", start=0)
    with gate(checkpoint, LEDGER_CRAWL, NOT_USED):
        await crawl_pages(
            runner,
            client,
            checkpoint,
            LEDGER_CRAWL,
            LEDGER,
            size=LEDGER_PAGE_SIZE,
            params={"days": LEDGER_DAYS},
            on_page=listing.update,
        )
        listing.update(sum(1 for _ in checkpoint.records(LEDGER_CRAWL)), final=True)


def organisation_objects(
    checkpoint: Checkpoint, stopped: ApiOffReason | None, tally: Tally
) -> Iterator[ArchiveObject]:
    """The org (its answer plus every part harvested; partial when this run stopped before
    every part was requested), the Credit ledger entries (deduped by
    id, first seen; an entry without a usable id gets the `sha256:` id of its JSON) and the
    usage record. The ledger's listed total is Trigify's."""
    for org in checkpoint.records(ORG_CRAWL):
        data = {"org": org}
        for crawl, (key, _) in PARTS.items():
            for part in checkpoint.records(crawl):
                data[key] = part
        unfinished = not all(checkpoint.crawl(crawl).done for crawl in PARTS)
        reason = NOT_HARVESTED[stopped] if stopped is not None and unfinished else None
        yield Org(
            trigify_id=org["id"],
            source="api",
            data=data,
            partial=None if reason is None else Partial(reason=reason),
        )
    tally.listed("credit_ledger_entry", checkpoint.crawl(LEDGER_CRAWL).total)
    seen: set[str] = set()
    for entry in checkpoint.records(LEDGER_CRAWL):
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        trigify_id = entry_id if isinstance(entry_id, str) and entry_id else result_id(entry)
        if trigify_id not in seen:
            seen.add(trigify_id)
            yield CreditLedgerEntry(trigify_id=trigify_id, source="api", data=entry)
    for usage in checkpoint.records(USAGE_CRAWL):
        yield UsageRecord(trigify_id=result_id(usage), source="api", data=usage)


def not_available(checkpoint: Checkpoint) -> list[str]:
    """Paths of the org parts whose route answered 403 or 404."""
    return [
        OPERATIONS[operation].path
        for crawl, (_, operation) in PARTS.items()
        if checkpoint.crawl(crawl).failure == NOT_AVAILABLE
    ]
