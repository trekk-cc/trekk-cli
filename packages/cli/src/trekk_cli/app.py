"""`trekk harvest --label TEXT --out DIR [--base-url URL] [--monitors FILE] [--report FILE]`:
the Harvester command; `trekk --version`."""

import asyncio
import ipaddress
import os
from dataclasses import dataclass, field
from datetime import tzinfo
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx2
import typer

from trekk_cli.exit_codes import ExitCode
from trekk_cli.messages import CATALOGUE, ask_secret, clock_time, say, text
from trekk_cli.report_file import cli_version, write_report
from trekk_harvester.clock import Clock, SystemClock
from trekk_harvester.errors import (
    ApiOffError,
    ArchiveRefusedError,
    CheckpointFormatError,
    KeyRejectedError,
    NoSubscriptionError,
    RequestFailedError,
    RetriesExhaustedError,
    SealedArchiveMissingError,
    StorageError,
    UnconfirmedKeyError,
    WorkspaceMismatchError,
)
from trekk_harvester.events import Event, Paused, Progress, Retrying, Started
from trekk_harvester.harvest import DEFAULT_BASE_URL, Outcome, archive_slug, connect, harvest
from trekk_harvester.runner import DEFAULT_POLICY, RetryPolicy

KEY_ENV = "TRIGIFY_API_KEY"


@dataclass(frozen=True, slots=True)
class Deps:
    """What the command runs against; tests inject a fake clock, time zone and transport."""

    clock: Clock = field(default_factory=SystemClock)
    tz: tzinfo | None = None
    transport: httpx2.AsyncBaseTransport | None = None
    policy: RetryPolicy = DEFAULT_POLICY


class Renderer:
    """Renders harvest events, each through one catalogue key."""

    def __init__(self, tz: tzinfo | None) -> None:
        self._tz = tz

    def emit(self, event: Event) -> None:
        match event:
            case Started(workspace_id, workspace_name, checkpoint, resumed):
                say("harvest.workspace", name=workspace_name, id=workspace_id)
                if resumed:
                    say("harvest.resuming", directory=checkpoint)
            case Retrying(previous):
                say("harvest.retrying", previous=previous)
            case Progress(object_type, phase, completed, total, eta):
                kind = text(f"kind.{object_type}")
                if total is None:
                    say("progress.listing", kind=kind, completed=completed)
                elif eta is None:
                    say(f"progress.{phase}", kind=kind, completed=completed, total=total)
                else:
                    eta_text = clock_time(eta, self._tz)
                    say(
                        f"progress.{phase}.eta",
                        kind=kind,
                        completed=completed,
                        total=total,
                        eta=eta_text,
                    )
            case Paused(until):
                say("harvest.paused", time=clock_time(until, self._tz))


def _resolve_key() -> str | None:
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    try:
        return ask_secret("prompt.key").strip() or None
    except typer.Abort:
        return None


def _is_safe_base_url(url: str) -> bool:
    """https anywhere; plain http only to this machine (the key travels in a header)."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    host = parts.hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _run(
    deps: Deps, key: str, base_url: str, out: Path, label: str, monitors: list[str]
) -> Outcome:
    async with connect(key, base_url, transport=deps.transport) as client:
        return await harvest(
            client,
            out,
            label,
            api_key=key,
            clock=deps.clock,
            reporter=Renderer(deps.tz),
            policy=deps.policy,
            monitors=monitors,
        )


def _read_monitors(path: Path) -> list[str] | None:
    """The profile URLs of a `--monitors` file (blank lines skipped, lines stripped); None
    after saying which line is not an https URL."""
    urls: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        url = raw.strip()
        if not url:
            continue
        if not url.startswith("https://"):
            say("usage.bad_monitor", line=number, path=path, value=url)
            return None
        urls.append(url)
    return urls


def run_harvest(
    deps: Deps,
    label: str,
    out: Path,
    base_url: str,
    monitors_file: Path | None = None,
    report_file: Path | None = None,
) -> ExitCode:
    """Harvest, print the outcome and, with `report_file`, write the report when the run ended
    with an Archive (else say it was not written)."""
    outcome = _harvest(deps, label, out, base_url, monitors_file)
    if isinstance(outcome, ExitCode):
        if report_file is not None:
            say("report.not_written", path=report_file)
        return outcome
    code = _report(outcome)
    if report_file is not None:
        try:
            write_report(report_file, outcome)
        except OSError as error:
            say("error.storage", directory=report_file.parent, reason=error.strerror or str(error))
            return ExitCode.FAILURE
    return code


def _harvest(
    deps: Deps, label: str, out: Path, base_url: str, monitors_file: Path | None
) -> Outcome | ExitCode:
    """The harvest's outcome, or the exit code of a run that ended without an Archive (after
    saying why)."""
    if not archive_slug(label):
        say("usage.no_label")
        return ExitCode.USAGE
    if not _is_safe_base_url(base_url):
        say("usage.insecure_base_url", url=base_url)
        return ExitCode.USAGE
    monitors = [] if monitors_file is None else _read_monitors(monitors_file)
    if monitors is None:
        return ExitCode.USAGE
    key = _resolve_key()
    if key is None:
        say("usage.no_key")
        return ExitCode.USAGE
    try:
        outcome = asyncio.run(_run(deps, key, base_url, out, label, monitors))
    except KeyboardInterrupt, asyncio.CancelledError:
        say("interrupted.stopped")
        say("interrupted.resume", out=out)
        return ExitCode.INTERRUPTED
    except RetriesExhaustedError as error:
        say("interrupted.retries", operation=error.operation)
        say("interrupted.resume", out=out)
        return ExitCode.INTERRUPTED
    except ApiOffError:
        say("harvest.lossless_unavailable")
        return ExitCode.FAILURE
    except KeyRejectedError:
        say("error.key_rejected")
        return ExitCode.FAILURE
    except NoSubscriptionError:
        say("error.no_subscription")
        return ExitCode.FAILURE
    except WorkspaceMismatchError as error:
        say(
            "error.workspace_mismatch",
            directory=out,
            checkpoint_name=error.checkpoint.name,
            checkpoint_id=error.checkpoint.id,
            name=error.found.name,
            id=error.found.id,
        )
        return ExitCode.FAILURE
    except UnconfirmedKeyError as error:
        say(
            "error.key_unconfirmed",
            directory=out,
            name=error.workspace.name,
            id=error.workspace.id,
        )
        return ExitCode.FAILURE
    except StorageError as error:
        say("error.storage", directory=error.directory, reason=error.reason)
        return ExitCode.FAILURE
    except CheckpointFormatError as error:
        say("error.checkpoint_format", directory=error.directory)
        return ExitCode.FAILURE
    except SealedArchiveMissingError as error:
        say("error.sealed_missing", path=error.path)
        return ExitCode.FAILURE
    except ArchiveRefusedError as error:
        say("error.archive_refused", kind=error.kind, id=error.trigify_id, field=error.field)
        return ExitCode.FAILURE
    except RequestFailedError as error:
        say("error.request_failed", operation=error.operation, detail=error.detail)
        return ExitCode.FAILURE
    return outcome


def _report(outcome: Outcome) -> ExitCode:
    if outcome.api_off:
        say("harvest.lossless_unavailable")
    if outcome.existing:
        say("harvest.exists.partial" if outcome.partial else "harvest.exists", path=outcome.archive)
        return ExitCode.PARTIAL if outcome.partial else ExitCode.OK
    key = "harvest.partial" if outcome.partial else "harvest.done"
    if outcome.previous is None:
        say(key, path=outcome.archive, objects=outcome.objects)
    else:
        say(f"{key}.new", path=outcome.archive, objects=outcome.objects, previous=outcome.previous)
    for line in outcome.report:
        kind = text(f"kind.{line.kind}")
        if line.listed is None:
            say("report.count", kind=kind, count=line.count)
        else:
            say("report.count.listed", kind=kind, count=line.count, listed=line.listed)
    if outcome.not_used:
        say("report.not_used", kinds=_kinds(outcome.not_used))
    if outcome.not_listed:
        say("report.not_listed", kinds=_kinds(outcome.not_listed))
    if outcome.not_available:
        say("report.not_available", routes=", ".join(outcome.not_available))
    if outcome.missing:
        say("report.missing", kinds=_kinds(outcome.missing))
    if outcome.redacted:
        say("report.redacted", count=outcome.redacted)
    if outcome.integrations_redacted:
        say("report.integration_credentials", count=outcome.integrations_redacted)
    return ExitCode.PARTIAL if outcome.partial else ExitCode.OK


def _kinds(kinds: tuple[str, ...]) -> str:
    return ", ".join(text(f"kind.{kind}") for kind in kinds)


def build_app(deps: Deps) -> typer.Typer:
    app = typer.Typer(
        name="trekk",
        help=CATALOGUE["help.app"],
        add_completion=False,
        no_args_is_help=True,
        pretty_exceptions_enable=False,
        rich_markup_mode=None,
    )

    def show_version(value: bool) -> None:
        if value:
            say("version", version=cli_version())
            raise typer.Exit()

    @app.callback()
    def _group(
        version: Annotated[
            bool,
            typer.Option(
                "--version", help=CATALOGUE["help.version"], is_eager=True, callback=show_version
            ),
        ] = False,
    ) -> None:
        """Keep `harvest` a named subcommand."""

    @app.command(help=CATALOGUE["help.harvest"], epilog=CATALOGUE["help.harvest.epilog"])
    def harvest(
        label: Annotated[str, typer.Option(help=CATALOGUE["help.label"], metavar="TEXT")],
        out: Annotated[
            Path, typer.Option(help=CATALOGUE["help.out"], metavar="DIR", file_okay=False)
        ],
        base_url: Annotated[
            str, typer.Option(help=CATALOGUE["help.base_url"], metavar="URL")
        ] = DEFAULT_BASE_URL,
        monitors: Annotated[
            Path | None,
            typer.Option(
                help=CATALOGUE["help.monitors"],
                metavar="FILE",
                exists=True,
                dir_okay=False,
                readable=True,
            ),
        ] = None,
        report: Annotated[
            Path | None,
            typer.Option(help=CATALOGUE["help.report"], metavar="FILE", dir_okay=False),
        ] = None,
    ) -> None:
        raise typer.Exit(run_harvest(deps, label, out, base_url, monitors, report))

    return app


app = build_app(Deps())


def main() -> None:
    app()
