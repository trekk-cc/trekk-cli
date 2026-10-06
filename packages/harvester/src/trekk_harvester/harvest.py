"""`harvest`: one Trigify workspace into one Archive under `--out`, resumably.

Layout: `<out>/checkpoint/state.json` + `<out>/checkpoint/<crawl>.jsonl`, then the Archive
`<out>/trekk-archive-<slug>.zip` (the slug comes from the label). While the latest Archive is
not final (it holds objects marked partial, or a harvest step never finished), every run
harvests again what is missing and, when the checkpoint gained pages since that Archive was
built, writes `trekk-archive-<slug>-2.zip`, `-3`, ... numbered on from the Archives the
checkpoint recorded (an Archive is never overwritten or deleted). Once an Archive is final, or
the Cutoff passed, the checkpoint is sealed (its pages deleted) and every later run returns that
Archive without a request. Nothing is created under `out` before the workspace lookup succeeds.
When that first request finds the API off (or the Cutoff passed), an unfinished checkpoint is
finalized only for a key an earlier answered run confirmed for its workspace; any other key is
refused with nothing written (the key alone cannot tell which workspace it opens).
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

import httpx2

from trekk_archive import (
    ArchiveObject,
    CredentialValueError,
    Kind,
    Manifest,
    ReaderLimits,
    open_archive,
    write_archive,
)
from trekk_harvester import (
    integrations,
    legacy,
    lists,
    monitors,
    organisation,
    searches,
    signals,
    topics,
    workflows,
)
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.clock import Clock
from trekk_harvester.errors import (
    ApiOffError,
    ApiOffReason,
    ArchiveRefusedError,
    ForbiddenError,
    NoSubscriptionError,
    SealedArchiveMissingError,
    StorageError,
    UnconfirmedKeyError,
    Workspace,
)
from trekk_harvester.events import Reporter, Retrying, Started
from trekk_harvester.features import not_used as features_not_used
from trekk_harvester.integrations import harvest_integrations, integration_objects
from trekk_harvester.legacy import harvest_legacy, legacy_objects
from trekk_harvester.lists import harvest_lists, list_objects
from trekk_harvester.monitors import harvest_monitors, monitor_objects
from trekk_harvester.monitors import not_listed as monitors_not_listed
from trekk_harvester.organisation import commit_org, harvest_organisation, organisation_objects
from trekk_harvester.report import KindReport, Summary, Tally
from trekk_harvester.runner import (
    DEFAULT_BUDGET,
    DEFAULT_POLICY,
    RateBudget,
    RetryPolicy,
    Runner,
)
from trekk_harvester.searches import harvest_searches, search_objects
from trekk_harvester.signals import harvest_signals, signal_objects
from trekk_harvester.topics import harvest_topics, topic_objects
from trekk_harvester.trigify import TrigifyClient, TrigifyPort
from trekk_harvester.workflows import harvest_workflows, workflow_objects

DEFAULT_BASE_URL = "https://api.trigify.io"
REQUEST_TIMEOUT_SECONDS = 30.0
ARCHIVE_PREFIX = "trekk-archive-"
SLUG_LENGTH = 60
_NOT_WORD = re.compile(r"[^\w-]+")
_SLUG_EDGES = "-_."
CHECKPOINT_DIR = "checkpoint"
STEPS = (
    *searches.STEPS,
    *topics.STEPS,
    *signals.STEPS,
    *monitors.STEPS,
    *legacy.STEPS,
    *workflows.STEPS,
    *lists.STEPS,
    *organisation.STEPS,
    *integrations.STEPS,
)
FEATURES = {
    **searches.FEATURES,
    **topics.FEATURES,
    **signals.FEATURES,
    **workflows.FEATURES,
    **lists.FEATURES,
    **organisation.FEATURES,
    **integrations.FEATURES,
}


def connect(
    api_key: str, base_url: str, *, transport: httpx2.AsyncBaseTransport | None = None
) -> TrigifyClient:
    """The Trigify client a harvest uses: the key travels only in its auth header."""
    return TrigifyClient(api_key, base_url, timeout=REQUEST_TIMEOUT_SECONDS, transport=transport)


@dataclass(frozen=True, slots=True)
class Outcome:
    """`archive`: the Archive written, or the existing one when `existing` (nothing new was
    written). `partial`: that Archive is not final (it holds an object marked partial, or a
    harvest step never finished). `summary`: what the run report file holds about it.
    `objects`: the number of objects written (None when `existing`). `api_off`: this run stopped
    because the API went off or the Cutoff passed. `previous`: the kept Archive this run
    harvested again. `report`: per-kind counts of a written Archive. `not_used`, `not_listed`,
    `not_available`, `missing`: what its manifest records as never used, without a listing
    source, not available (routes) and not reachable through the API. `redacted`: how many
    credentials its writer redacted; `integrations_redacted`: how many integrations held
    credentials (they must be reconnected)."""

    archive: Path
    partial: bool
    existing: bool
    summary: Summary
    objects: int | None = None
    api_off: bool = False
    previous: Path | None = None
    report: tuple[KindReport, ...] = ()
    not_used: tuple[Kind, ...] = ()
    not_listed: tuple[Kind, ...] = ()
    not_available: tuple[str, ...] = ()
    missing: tuple[Kind, ...] = ()
    redacted: int = 0
    integrations_redacted: int = 0


def archive_slug(label: str) -> str:
    """The label as it appears in Archive file names: NFKC-normalized, every run of characters
    other than word characters and `-` replaced by `-`, lowercased, without leading or trailing
    `-`, `_`, `.`, at most `SLUG_LENGTH` characters ('' when nothing usable is left)."""
    slug = _NOT_WORD.sub("-", unicodedata.normalize("NFKC", label)).lower().strip(_SLUG_EDGES)
    return slug[:SLUG_LENGTH].strip(_SLUG_EDGES)


def archive_name(slug: str, number: int) -> str:
    """Archive number `number` (1: `trekk-archive-<slug>.zip`, n: `trekk-archive-<slug>-n.zip`)."""
    return f"{ARCHIVE_PREFIX}{slug}.zip" if number == 1 else f"{ARCHIVE_PREFIX}{slug}-{number}.zip"


def _next_archive(out: Path, checkpoint: Checkpoint | None, slug: str) -> Path:
    """The next Archive's path: numbered on from the Archives the checkpoint recorded (whatever
    their label), skipping any name already taken in `out`."""
    number = 1 + (0 if checkpoint is None else len(checkpoint.archives))
    while (out / archive_name(slug, number)).exists():
        number += 1
    return out / archive_name(slug, number)


def _previous(out: Path, checkpoint: Checkpoint | None) -> Path | None:
    """The latest Archive the checkpoint recorded, when it is still in `out`."""
    if checkpoint is None or not checkpoint.archives:
        return None
    previous = out / checkpoint.archives[-1]
    return previous if previous.is_file() else None


async def harvest(
    client: TrigifyPort,
    out: Path,
    label: str,
    *,
    api_key: str,
    clock: Clock,
    reporter: Reporter,
    budget: RateBudget = DEFAULT_BUDGET,
    policy: RetryPolicy = DEFAULT_POLICY,
    monitors: Sequence[str] = (),
) -> Outcome:
    """`label`: the workspace's name, recorded in the Archive and slugged into its file name
    (a label without a usable character is a `ValueError`). `api_key`: the key `client` sends;
    only its salted fingerprint is recorded, as confirmed for the checkpoint's workspace.
    `monitors`: LinkedIn profile URLs of Monitors to harvest besides those Trigify's legacy
    listing names."""
    slug = archive_slug(label)
    if not slug:
        raise ValueError("the label holds no character usable in a file name")
    checkpoint_dir = out / CHECKPOINT_DIR
    found = Checkpoint.existing(checkpoint_dir)
    if found is not None and found.sealed is not None:
        sealed = out / found.sealed
        if not sealed.is_file():
            raise SealedArchiveMissingError(sealed)
        return _existing(sealed, found, None)
    previous = _previous(out, found)
    if found is not None and previous is not None:
        summary = _read_summary(previous, found)
        if not _is_partial(summary, found):
            found.seal(previous.name)  # a crash came between writing it and sealing
            return Outcome(previous, False, True, summary)
    archive = _next_archive(out, found, slug)
    sent = Checkpoint.sent_times(checkpoint_dir)
    runner = Runner(clock, reporter, budget=budget, policy=policy, sent=sent)
    try:
        org_answer = await runner.call(client.get_org)
    except ForbiddenError as error:
        raise NoSubscriptionError(error.operation) from error
    except ApiOffError as error:
        if found is None:
            raise
        if not found.confirms(api_key):
            raise UnconfirmedKeyError(checkpoint_dir, found.workspace) from error
        return _finalize(found, archive, previous, label, clock, error.reason)
    org = org_answer.data
    checkpoint = Checkpoint.open(checkpoint_dir, Workspace(org.id, org.name), sent=runner.sent)
    try:
        checkpoint.confirm(api_key)
        commit_org(checkpoint, org_answer)
        reporter.emit(Started(org.id, org.name, checkpoint_dir, checkpoint.resumed))
        if previous is not None:
            runner.once_answered(lambda: reporter.emit(Retrying(previous)))
        stopped: ApiOffReason | None = None
        try:
            await harvest_searches(runner, client, checkpoint, clock, reporter)
            await harvest_topics(runner, client, checkpoint, clock, reporter)
            await harvest_signals(runner, client, checkpoint, clock, reporter)
            await harvest_monitors(runner, client, checkpoint, clock, reporter, monitors)
            await harvest_legacy(runner, client, checkpoint, clock, reporter)
            await harvest_workflows(runner, client, checkpoint, clock, reporter)
            await harvest_lists(runner, client, checkpoint, clock, reporter)
            await harvest_organisation(runner, client, checkpoint, clock, reporter)
            await harvest_integrations(runner, client, checkpoint, clock, reporter)
        except ApiOffError as error:
            stopped = error.reason
        return _finalize(checkpoint, archive, previous, label, clock, stopped)
    except StorageError:
        raise
    except BaseException:
        checkpoint.save()  # keep the rate window's send times for the next run
        raise


def _read_summary(archive: Path, checkpoint: Checkpoint) -> Summary:
    """`_summary` of an Archive already written (read back from it)."""
    with open_archive(archive, ReaderLimits()) as reader:
        partial = Counter(obj.kind for obj in reader.objects() if obj.partial is not None)
        return _summary(archive.name, reader.manifest, partial, checkpoint)


def _summary(
    name: str, manifest: Manifest, partial: Counter[Kind], checkpoint: Checkpoint
) -> Summary:
    """The run report's facts about the Archive `name`: its manifest, its objects marked
    partial per kind, the list totals recorded when it was built and the crawls that stopped on
    a failure."""
    return Summary(
        archive_spec=manifest.spec_version,
        workspace_label=manifest.workspace_label,
        counts=dict(manifest.counts),
        listed=checkpoint.listed(name),
        partial=dict(partial),
        missing=tuple(missing.kind for missing in manifest.missing),
        not_used=tuple(manifest.not_used),
        not_available=tuple(manifest.not_available),
        errors=tuple(checkpoint.errors()),
    )


def _is_partial(summary: Summary, checkpoint: Checkpoint) -> bool:
    """An Archive is final when it holds no object marked partial and every harvest step
    finished; a step that never started keeps it retryable."""
    return bool(summary.partial) or not checkpoint.completed(STEPS)


def _existing(archive: Path, checkpoint: Checkpoint, stopped: ApiOffReason | None) -> Outcome:
    """`archive` stays the result and no new one is written (sealed, or nothing was committed
    since it was built). Sealed when final or `stopped` at the Cutoff."""
    summary = _read_summary(archive, checkpoint)
    partial = _is_partial(summary, checkpoint)
    _seal(checkpoint, archive, partial, stopped)
    return Outcome(archive, partial, True, summary, api_off=stopped is not None)


def _seal(
    checkpoint: Checkpoint, archive: Path, partial: bool, stopped: ApiOffReason | None
) -> None:
    """Seal the checkpoint with `archive` when it is final or the run stopped at the Cutoff
    (nothing more can be harvested); after "API off" its pages stay for a resume."""
    if checkpoint.sealed is None and (not partial or stopped == "cutoff"):
        checkpoint.seal(archive.name)


def _finalize(
    checkpoint: Checkpoint,
    archive: Path,
    previous: Path | None,
    label: str,
    clock: Clock,
    stopped: ApiOffReason | None,
) -> Outcome:
    """Write the Archive from the checkpoint, unless nothing was committed since `previous`
    was built; with `stopped`, what is missing is kept from its list summary and marked
    partial."""
    if previous is not None and not checkpoint.advanced_past(previous.name):
        return _existing(previous, checkpoint, stopped)
    tally = Tally()
    partial: Counter[Kind] = Counter()

    def counted() -> Iterator[ArchiveObject]:
        objects = chain(
            search_objects(checkpoint, stopped, tally),
            topic_objects(checkpoint, stopped, tally),
            signal_objects(checkpoint, stopped, tally),
            monitor_objects(checkpoint, stopped, tally),
            legacy_objects(checkpoint, stopped, tally),
            workflow_objects(checkpoint, stopped, tally),
            list_objects(checkpoint, stopped, tally),
            organisation_objects(checkpoint, stopped, tally),
            integration_objects(checkpoint, stopped, tally),
        )
        for obj in objects:
            if obj.partial is not None:
                partial[obj.kind] += 1
            yield obj

    not_used = features_not_used(checkpoint, FEATURES)
    not_listed = monitors_not_listed(checkpoint)
    not_available = [
        *legacy.not_available(checkpoint),
        *organisation.not_available(checkpoint),
        *integrations.not_available(checkpoint),
    ]
    try:
        manifest = write_archive(
            archive,
            trigify_workspace_id=checkpoint.workspace.id,
            workspace_label=label,
            written_at=clock.now(),
            objects=counted(),
            signal_types=signals.signal_types(checkpoint),
            not_used=not_used,
            not_listed=not_listed,
            not_available=not_available,
            missing=lists.missing(),
        )
    except CredentialValueError as error:
        raise ArchiveRefusedError(error.kind, error.trigify_id, error.path) from error
    except FileExistsError as error:
        # Another process wrote this name since the run started: never overwrite it.
        raise StorageError(archive.parent, f"{archive.name} already exists") from error
    except OSError as error:
        raise StorageError(archive.parent, error.strerror or str(error)) from error
    report = tally.report(manifest.counts)
    checkpoint.archived(
        archive.name, {line.kind: line.listed for line in report if line.listed is not None}
    )
    summary = _summary(archive.name, manifest, partial, checkpoint)
    is_partial = _is_partial(summary, checkpoint)
    _seal(checkpoint, archive, is_partial, stopped)
    return Outcome(
        archive,
        is_partial,
        False,
        summary,
        objects=sum(manifest.counts.values()),
        api_off=stopped is not None,
        previous=previous,
        report=report,
        not_used=tuple(not_used),
        not_listed=tuple(not_listed),
        not_available=tuple(not_available),
        missing=tuple(missing.kind for missing in manifest.missing),
        redacted=sum(len(redaction.paths) for redaction in manifest.redactions),
        integrations_redacted=sum(
            redaction.kind == "integration" for redaction in manifest.redactions
        ),
    )
