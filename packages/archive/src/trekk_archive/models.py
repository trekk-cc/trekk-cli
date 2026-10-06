"""Archive spec v1 models: one model per object kind, the discriminated `ArchiveObject` union
and the `Manifest`. Payloads stay raw (`data`): the lossless Trigify response object.

Forward compatible within major 1: every model ignores fields it does not know, and the manifest
drops kinds it does not know (from `counts`, `not_used`, `not_listed`, `missing` and
`redactions`), so a 1.x reader opens a 1.y Archive written by a newer writer."""

from datetime import UTC, datetime
from importlib.metadata import version
from typing import Annotated, Literal, get_args

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
)

SPEC_VERSION = "1.3.0"
SPEC_DATE = "2026-10-06"  # release date of SPEC_VERSION (CHANGELOG heading)


def writer_version() -> str:
    return version("trekk-archive")


Kind = Literal[
    "org",
    "integration",
    "credit_ledger_entry",
    "usage_record",
    "search",
    "search_result",
    "monitor",
    "monitor_result",
    "monitor_post_result",
    "topic",
    "topic_engagement",
    "target_list",
    "target",
    "signal_subscription",
    "signal",
    "insight",
    "workflow",
    "workflow_draft",
    "run",
    "output_row",
    "workflow_table",
    "workflow_table_row",
    "list",
    "list_member",
    "agent_memory_table",
    "agent_memory_record",
    "legacy_route",
]
KINDS: tuple[Kind, ...] = get_args(Kind)

# M4 kinds: published in the object schema, never valid as `kind` in v1.
RESERVED_KINDS: tuple[str, ...] = (
    "account_signal",
    "dynamic_list",
    "social_reaction",
)

Source = Literal["api", "fallback", "missing"]
TrigifyId = Annotated[str, Field(min_length=1)]


class Partial(BaseModel):
    """The object could not be harvested in full; `reason` says why."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    reason: str = Field(min_length=1)


class _Object(BaseModel):
    # The published schema describes what the writer emits: `kind` and `partial` always present.
    model_config = ConfigDict(
        extra="ignore", frozen=True, json_schema_serialization_defaults_required=True
    )

    trigify_id: TrigifyId
    source: Source
    partial: Partial | None = None
    data: dict[str, JsonValue]


class Org(_Object):
    kind: Literal["org"] = "org"


class Integration(_Object):
    kind: Literal["integration"] = "integration"
    mappings: dict[str, dict[str, JsonValue]]


class CreditLedgerEntry(_Object):
    kind: Literal["credit_ledger_entry"] = "credit_ledger_entry"


class UsageRecord(_Object):
    kind: Literal["usage_record"] = "usage_record"


class Search(_Object):
    kind: Literal["search"] = "search"


class SearchResult(_Object):
    kind: Literal["search_result"] = "search_result"
    search_id: TrigifyId


class Monitor(_Object):
    kind: Literal["monitor"] = "monitor"


class MonitorResult(_Object):
    kind: Literal["monitor_result"] = "monitor_result"
    monitor_id: TrigifyId


class MonitorPostResult(_Object):
    kind: Literal["monitor_post_result"] = "monitor_post_result"
    monitor_id: TrigifyId
    post_url: str = Field(min_length=1)


class Topic(_Object):
    kind: Literal["topic"] = "topic"


class TopicEngagement(_Object):
    kind: Literal["topic_engagement"] = "topic_engagement"
    topic_id: TrigifyId


class TargetList(_Object):
    kind: Literal["target_list"] = "target_list"


class Target(_Object):
    kind: Literal["target"] = "target"
    target_list_id: TrigifyId | None


class SignalSubscription(_Object):
    kind: Literal["signal_subscription"] = "signal_subscription"


class Signal(_Object):
    kind: Literal["signal"] = "signal"


class Insight(_Object):
    kind: Literal["insight"] = "insight"


class Workflow(_Object):
    kind: Literal["workflow"] = "workflow"


class WorkflowDraft(_Object):
    kind: Literal["workflow_draft"] = "workflow_draft"
    workflow_id: TrigifyId


class Run(_Object):
    kind: Literal["run"] = "run"
    workflow_id: TrigifyId


class OutputRow(_Object):
    kind: Literal["output_row"] = "output_row"
    workflow_id: TrigifyId
    run_id: TrigifyId
    parent_row_id: TrigifyId | None
    loop_index: int | None = Field(ge=0)


class WorkflowTable(_Object):
    kind: Literal["workflow_table"] = "workflow_table"
    workflow_id: TrigifyId | None


class WorkflowTableRow(_Object):
    kind: Literal["workflow_table_row"] = "workflow_table_row"
    table_id: TrigifyId


class List(_Object):
    kind: Literal["list"] = "list"


class ListMember(_Object):
    kind: Literal["list_member"] = "list_member"
    list_id: TrigifyId


class AgentMemoryTable(_Object):
    kind: Literal["agent_memory_table"] = "agent_memory_table"


class AgentMemoryRecord(_Object):
    kind: Literal["agent_memory_record"] = "agent_memory_record"
    table_id: TrigifyId


class LegacyRoute(_Object):
    kind: Literal["legacy_route"] = "legacy_route"
    route: str = Field(min_length=1)


AnyObject = (
    Org
    | Integration
    | CreditLedgerEntry
    | UsageRecord
    | Search
    | SearchResult
    | Monitor
    | MonitorResult
    | MonitorPostResult
    | Topic
    | TopicEngagement
    | TargetList
    | Target
    | SignalSubscription
    | Signal
    | Insight
    | Workflow
    | WorkflowDraft
    | Run
    | OutputRow
    | WorkflowTable
    | WorkflowTableRow
    | List
    | ListMember
    | AgentMemoryTable
    | AgentMemoryRecord
    | LegacyRoute
)
ArchiveObject = Annotated[AnyObject, Field(discriminator="kind")]


class MissingKind(BaseModel):
    """A kind the Trigify API offers no operation for: none of its objects could be harvested
    (`reason` says why); a fallback export can fill it later."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: Kind
    source: Literal["missing"]
    reason: str = Field(min_length=1)


class Redaction(BaseModel):
    """Where the writer replaced credentials in one object by the redaction marker: the paths
    only (dot-joined; a renamed key appears as the marker), never the values."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: Kind
    trigify_id: TrigifyId
    paths: list[str] = Field(min_length=1)


def _unknown(kind: object) -> bool:
    """A kind name this spec version does not know (a non-string still fails validation)."""
    return isinstance(kind, str) and kind not in KINDS


class Manifest(BaseModel):
    """`signal_types`: Signal types the workspace used; `not_used`: kinds of a feature Trigify
    answered as never used (403/404); `not_listed`: kinds the Harvester had no source listing
    for; `not_available`: Trigify routes that answered 404. All `[]` in 1.0.0 manifests.
    `missing`: kinds the API cannot reach; `redactions`: per object, where credentials were
    redacted, in write order. Both `[]` before 1.2.0. From 1.3.0 `not_available` holds any
    route that answered 403 or 404."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    spec_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    writer_version: str = Field(min_length=1)
    trigify_workspace_id: str = Field(min_length=1)
    workspace_label: str = Field(min_length=1)
    written_at: AwareDatetime = Field(json_schema_extra={"format": "date-time", "pattern": "Z$"})
    counts: dict[Kind, Annotated[int, Field(ge=0)]]
    signal_types: list[str] = []
    not_used: list[Kind] = []
    not_listed: list[Kind] = []
    not_available: list[str] = []
    missing: list[MissingKind] = []
    redactions: list[Redaction] = []

    @field_validator("counts", mode="before")
    @classmethod
    def _known_counts(cls, counts: object) -> object:
        """A kind a newer 1.y writer knows and this reader does not is ignored."""
        if not isinstance(counts, dict):
            return counts
        return {kind: count for kind, count in counts.items() if not _unknown(kind)}

    @field_validator("not_used", "not_listed", mode="before")
    @classmethod
    def _known_kinds(cls, kinds: object) -> object:
        if not isinstance(kinds, list):
            return kinds
        return [kind for kind in kinds if not _unknown(kind)]

    @field_validator("missing", "redactions", mode="before")
    @classmethod
    def _known_entries(cls, entries: object) -> object:
        if not isinstance(entries, list):
            return entries
        return [
            entry
            for entry in entries
            if not (isinstance(entry, dict) and _unknown(entry.get("kind")))
        ]

    @field_validator("counts")
    @classmethod
    def _every_kind_in_canonical_order(cls, counts: dict[Kind, int]) -> dict[Kind, int]:
        """Every kind, in canonical order; a kind an earlier 1.x writer did not know counts 0."""
        return {kind: counts.get(kind, 0) for kind in KINDS}

    @field_serializer("written_at")
    def _utc_z(self, written_at: datetime) -> str:
        return written_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
