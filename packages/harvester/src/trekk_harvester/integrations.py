"""The Integrations step: the workspace's integrations and, for each connected one, the inventory
the re-binding wizard needs (channels, users, CRM fields, campaigns, ...), as id-keyed mappings.
Credentials are never asked for; any the answers carry are redacted by the Archive writer.

Crawl `integrations` holds the one unpaged `get_integrations` answer; a 403 or 404 means the
workspace never used Integrations (`features.gate`). Crawls `integration__<type>__<source>` hold
each inventory source's one answer, committed whole; a source whose route answers 403 or 404 is
marked not available (its path, with `{type}` filled, goes to the manifest's `not_available`).
Each source is requested once, unpaged: what Trigify holds beyond that answer (`has_more`)
marks the integration partial. Per-item inventory (Notion schemas, Airtable tables and fields,
Google Sheets sheets and columns, Linear states, CRM field options), integration health,
audiences and X accounts are not requested."""

from collections.abc import Iterator
from typing import Any, NamedTuple

from trekk_archive import ArchiveObject, Integration, Kind, Partial
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.children import result_id
from trekk_harvester.clock import Clock
from trekk_harvester.errors import ApiOffReason
from trekk_harvester.events import Reporter
from trekk_harvester.features import NOT_AVAILABLE, NOT_USED, gate
from trekk_harvester.paging import MORE_FIELDS
from trekk_harvester.progress import ProgressTracker
from trekk_harvester.report import Tally
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import OPERATIONS, TrigifyPort

STEPS = ("integrations",)
(INTEGRATIONS_STEP,) = STEPS
LIST_CRAWL = "integrations"
LIST_OPERATION = "get_integrations"
TYPE_PARAM = "{type}"
FEATURES: dict[str, tuple[Kind, ...]] = {LIST_CRAWL: ("integration",)}
MORE = "Trigify holds more {name} than one answer returns; the first answer is kept"
NOT_HARVESTED: dict[ApiOffReason, str] = {
    "api_off": "the Trigify API stopped answering before this integration's inventory was"
    " harvested",
    "cutoff": "the Cutoff passed before this integration's inventory was harvested",
}


class Source(NamedTuple):
    """One inventory request: `name` names its mapping(s); `params` its query arguments."""

    name: str
    operation: str
    params: tuple[tuple[str, str], ...] = ()


SLACK = (
    Source("slack_channels", "get_integrations_slack_channels"),
    Source("slack_users", "get_integrations_slack_users"),
)
CRM = (Source("crm_fields", "get_integrations_by_type_crm_fields"),)
SEQUENCER = (Source("campaigns", "get_integrations_by_type_campaigns"),)
# integration type -> its inventory sources (any other type has none)
SOURCES: dict[str, tuple[Source, ...]] = {
    "slack": SLACK,
    "hubspot": CRM,
    "attio": CRM,
    "salesforce": CRM,
    "instantly": SEQUENCER,
    "smartleads": SEQUENCER,
    "heyreach": SEQUENCER,
    "linear": (
        Source("linear_teams", "get_integrations_linear_teams"),
        Source("linear_users", "get_integrations_linear_users"),
    ),
    "notion": (
        Source("notion_databases", "get_integrations_notion_databases", (("page_size", "100"),)),
    ),
    "airtable": (Source("airtable_bases", "get_integrations_airtable_bases"),),
    "google_sheets": (
        Source("google_sheets_documents", "get_integrations_google_sheets_documents"),
    ),
}


def source_crawl(integration_type: str, source: Source) -> str:
    return f"integration__{integration_type}__{source.name}"


def _path(integration_type: str, source: Source) -> tuple[str, ...]:
    """The path arguments of the source's operation (`type` for type-scoped routes)."""
    return (integration_type,) if TYPE_PARAM in OPERATIONS[source.operation].path else ()


async def harvest_integrations(
    runner: Runner, client: TrigifyPort, checkpoint: Checkpoint, clock: Clock, reporter: Reporter
) -> None:
    """List the integrations once (unless done), then request each connected integration's
    inventory sources once (unless done)."""
    if not checkpoint.crawl(LIST_CRAWL).done:
        with gate(checkpoint, LIST_CRAWL, NOT_USED):
            answer = await runner.call(getattr(client, LIST_OPERATION))
            data = answer.model_dump(mode="json", by_alias=True, exclude_unset=True).get("data")
            checkpoint.commit(LIST_CRAWL, data or [], cursor=None, done=True)
    for integration_type in connected_types(checkpoint):
        for source in SOURCES[integration_type]:
            crawl = source_crawl(integration_type, source)
            if checkpoint.crawl(crawl).done:
                continue
            with gate(checkpoint, crawl, NOT_AVAILABLE):
                answer = await runner.call(
                    getattr(client, source.operation),
                    *_path(integration_type, source),
                    **dict(source.params),
                )
                whole = answer.model_dump(mode="json", by_alias=True, exclude_unset=True)
                checkpoint.commit(crawl, [whole], cursor=None, done=True)
    listed = sum(1 for _ in checkpoint.records(LIST_CRAWL))
    ProgressTracker(clock, reporter, "integration", "listing", start=0).update(listed, final=True)
    checkpoint.complete(INTEGRATIONS_STEP)


def _connected(item: Any) -> str | None:
    """The type of a connected integration Trekk has inventory sources for, else None."""
    if not isinstance(item, dict) or item.get("connected") is not True:
        return None
    integration_type = item.get("type")
    return (
        integration_type
        if isinstance(integration_type, str) and integration_type in SOURCES
        else None
    )


def connected_types(checkpoint: Checkpoint) -> list[str]:
    """Types of the connected integrations with inventory sources, in list order, once each."""
    types = (_connected(item) for item in checkpoint.records(LIST_CRAWL))
    return list(dict.fromkeys(t for t in types if t is not None))


def mappings_of(name: str, data: Any) -> dict[str, dict[str, Any]]:
    """The mappings one source answer's `data` gives: a list, or an object with one list, is
    the mapping `name`; an object with several lists gives `<name>_<field>` per list; an object
    without a list is the one entry of mapping `name`."""
    if isinstance(data, list):
        return {name: _keyed(data)}
    if not isinstance(data, dict):
        return {}
    lists = {field: value for field, value in data.items() if isinstance(value, list)}
    if len(lists) == 1:
        return {name: _keyed(next(iter(lists.values())))}
    if lists:
        return {f"{name}_{field}": _keyed(value) for field, value in lists.items()}
    return {name: _keyed([data])}


def _keyed(items: list[Any]) -> dict[str, Any]:
    """Items by `str(id)` when it is a non-empty string or an integer, else (or when that key
    is taken) by the `sha256:` id of the item's JSON."""
    keyed: dict[str, Any] = {}
    for item in items:
        key = _item_key(item)
        keyed[result_id(item) if key in keyed else key] = item
    return keyed


def _item_key(item: Any) -> str:
    item_id = item.get("id") if isinstance(item, dict) else None
    if isinstance(item_id, str) and item_id:
        return item_id
    if isinstance(item_id, int) and not isinstance(item_id, bool):
        return str(item_id)
    return result_id(item)


def _more(answer: Any) -> bool:
    """Whether the answer (its root or its `data`) says Trigify holds more."""
    containers = [answer, answer.get("data") if isinstance(answer, dict) else None]
    return any(
        isinstance(container, dict) and container.get(field) is True
        for container in containers
        for field in MORE_FIELDS
    )


def _inventory(
    checkpoint: Checkpoint, item: Any, stopped: ApiOffReason | None
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """A listed integration's mappings and partial reasons (none unless connected); with
    `stopped`, a source not yet requested makes it partial."""
    integration_type = _connected(item)
    mappings: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    if integration_type is None:
        return mappings, reasons
    crawls = [source_crawl(integration_type, s) for s in SOURCES[integration_type]]
    if stopped is not None and not all(checkpoint.crawl(crawl).done for crawl in crawls):
        reasons.append(NOT_HARVESTED[stopped])
    for source, crawl in zip(SOURCES[integration_type], crawls, strict=True):
        for answer in checkpoint.records(crawl):
            mappings.update(mappings_of(source.name, answer.get("data")))
            if _more(answer):
                reasons.append(MORE.format(name=source.name))
    return mappings, reasons


def integration_objects(
    checkpoint: Checkpoint, stopped: ApiOffReason | None, tally: Tally
) -> Iterator[ArchiveObject]:
    """One `integration` per listed item, `trigify_id` its `type` (the first item of that type;
    a later one, or one without a string `type`, gets the `sha256:` id of its JSON)."""
    seen: set[str] = set()
    for item in checkpoint.records(LIST_CRAWL):
        integration_type = item.get("type") if isinstance(item, dict) else None
        trigify_id = (
            integration_type
            if isinstance(integration_type, str) and integration_type not in ("", *seen)
            else result_id(item)
        )
        if trigify_id in seen:
            continue
        seen.add(trigify_id)
        mappings, reasons = _inventory(checkpoint, item, stopped)
        yield Integration(
            trigify_id=trigify_id,
            source="api",
            data=item,
            mappings=mappings,
            partial=Partial(reason="; ".join(reasons)) if reasons else None,
        )


def not_available(checkpoint: Checkpoint) -> list[str]:
    """Paths (with `{type}` filled) of the inventory sources whose route answered 403 or 404."""
    return [
        OPERATIONS[source.operation].path.replace(TYPE_PARAM, integration_type)
        for integration_type in connected_types(checkpoint)
        for source in SOURCES[integration_type]
        if checkpoint.crawl(source_crawl(integration_type, source)).failure == NOT_AVAILABLE
    ]
