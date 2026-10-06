"""The message catalogue: every sentence `trekk` prints or shows as help, one key each.

Output goes only through `say` (and the key prompt through `ask_secret`); placeholders are
filled with `str.format`, never concatenated. Times are printed as `HH:MM TZ`.
"""

from datetime import datetime, tzinfo

import typer

CATALOGUE: dict[str, str] = {
    "help.app": "Trekk command line tools.",
    "help.harvest": (
        "Copy one Trigify workspace, read with your own Trigify API key, into an Archive you"
        " own: the Searches with their full result history, the topics with their engagements,"
        " the Signal subscriptions, Targets, Signals and Insights, the Monitors with their"
        " results per profile and per post, what Trigify's legacy routes still answer, the"
        " workflows with their definitions, drafts, Runs and Run output, the workflow tables,"
        " the organisation's details, the Credit ledger of the last 365 days, usage, and the"
        " integrations with their channel, user, CRM field and campaign mappings (never their"
        " credentials: reconnect them in Trekk)."
        " Credentials found in the data are replaced by a marker. Run the same command again"
        " with the same --out to resume where it stopped, or to harvest again what an Archive"
        " marks partial. Trigify's API stops answering at the Cutoff, 22 Oct 2026 23:59 BST;"
        " after it, lossless harvest is no longer available."
    ),
    "help.harvest.epilog": (
        "The Trigify API key is read from the TRIGIFY_API_KEY environment variable or, when"
        " that is not set, from a hidden prompt. No option takes the key, and it is never"
        " written to disk.\n\n"
        "Exit codes: 0 OK (Archive written), 1 FAILURE (nothing more can be done with this"
        " key or directory), 2 USAGE (missing key, unusable label or bad option), 3 PARTIAL"
        " (Archive written with objects marked partial), 4 INTERRUPTED (checkpoint kept, run"
        " the same command again to resume)."
    ),
    "help.label": (
        "Your name for this workspace, recorded in the Archive and used in its file name"
        " (trekk-archive-<label>.zip)."
    ),
    "help.out": (
        "Directory for the checkpoint and the Archive (trekk-archive-<label>.zip; a later run"
        " that harvests partial objects again writes trekk-archive-<label>-2.zip, -3, ... and"
        " keeps the earlier ones). The checkpoint keeps only redacted data; its pages are"
        " deleted once the final Archive is written."
    ),
    "help.report": (
        "Also write a JSON run report to this file when the run ends with an Archive: object"
        " counts, mismatches with Trigify's lists, partial, missing and unused kinds, and"
        " errors. It holds no ids, record values or key, and is never sent anywhere."
    ),
    "help.version": "Print the version and exit.",
    "version": "trekk-cli {version}",
    "help.base_url": "Trigify API address.",
    "help.monitors": (
        "Text file with the LinkedIn profile URL of one Monitor per line (Trigify's API does not"
        " list them). Harvested together with the Monitors Trigify's legacy listing names."
    ),
    "prompt.key": "Trigify API key",
    "usage.no_key": (
        "No Trigify API key given. Set TRIGIFY_API_KEY or enter the key at the prompt."
    ),
    "usage.insecure_base_url": (
        "The Trigify API address {url} is not https. Use an https address (plain http is"
        " accepted only for this machine)."
    ),
    "usage.no_label": (
        "The label has no letter or digit. Pass a name for this workspace with --label; it also"
        " names the Archive file."
    ),
    "usage.bad_monitor": (
        "Line {line} of {path} is not a LinkedIn profile URL starting with https://: {value}"
    ),
    "kind.search": "Searches",
    "kind.search_result": "Search results",
    "kind.topic": "Topics",
    "kind.topic_engagement": "Topic engagements",
    "kind.signal_subscription": "Signal subscriptions",
    "kind.target": "Targets",
    "kind.feed": "Signal feed items",
    "kind.signal": "Signals",
    "kind.insight": "Insights",
    "kind.monitor": "Monitors",
    "kind.monitor_result": "Monitor results",
    "kind.monitor_post": "Monitor posts",
    "kind.monitor_post_result": "Monitor post results",
    "kind.legacy_route": "Legacy route items",
    "kind.workflow": "Workflows",
    "kind.workflow_draft": "Workflow drafts",
    "kind.run": "Runs",
    "kind.output_row": "Run output rows",
    "kind.workflow_table": "Workflow tables",
    "kind.workflow_table_row": "Workflow table rows",
    "kind.list": "Lists",
    "kind.list_member": "List members",
    "kind.agent_memory_table": "Agent Memory tables",
    "kind.agent_memory_record": "Agent Memory records",
    "kind.org": "Organisation details",
    "kind.credit_ledger_entry": "Credit ledger entries",
    "kind.usage_record": "Usage records",
    "kind.integration": "Integrations",
    "harvest.workspace": "Workspace: {name} ({id})",
    "harvest.resuming": "Resuming from the checkpoint in {directory}",
    "progress.listing": "{kind}: {completed} listed",
    "progress.details": "{kind}: {completed} of {total}",
    "progress.details.eta": "{kind}: {completed} of {total}, ETA {eta}",
    "progress.results": "{kind}: result history of {completed} of {total}",
    "progress.results.eta": "{kind}: result history of {completed} of {total}, ETA {eta}",
    "progress.engagements": "{kind}: engagements of {completed} of {total}",
    "progress.engagements.eta": "{kind}: engagements of {completed} of {total}, ETA {eta}",
    "progress.drafts": "{kind}: drafts of {completed} of {total}",
    "progress.drafts.eta": "{kind}: drafts of {completed} of {total}, ETA {eta}",
    "progress.runs": "{kind}: Runs of {completed} of {total}",
    "progress.runs.eta": "{kind}: Runs of {completed} of {total}, ETA {eta}",
    "progress.variables": "{kind}: variables of {completed} of {total}",
    "progress.variables.eta": "{kind}: variables of {completed} of {total}, ETA {eta}",
    "harvest.retrying": (
        "The Archive {previous} is not complete. Harvesting again what it is missing; that"
        " Archive is kept."
    ),
    "harvest.paused": "Paused: rate limited, resuming automatically at {time}",
    "harvest.done": "Archive written: {path} ({objects} objects)",
    "harvest.lossless_unavailable": "Lossless harvest is no longer available.",
    "harvest.partial": (
        "Archive written: {path} ({objects} objects). Objects not harvested in full are"
        " marked partial."
    ),
    "harvest.done.new": (
        "Archive written: {path} ({objects} objects). The earlier Archive {previous} is kept."
    ),
    "harvest.partial.new": (
        "Archive written: {path} ({objects} objects). Objects not harvested in full are"
        " marked partial. The earlier Archive {previous} is kept."
    ),
    "report.count": "{kind}: {count} in the Archive",
    "report.count.listed": "{kind}: {count} in the Archive, {listed} listed by Trigify",
    "report.not_used": (
        "Not used in this workspace (Trigify answered that the feature is not enabled): {kinds}"
    ),
    "report.not_listed": (
        "Not listed: {kinds}. Trigify gave no list of Monitors; pass their profile URLs with"
        " --monitors to harvest them."
    ),
    "report.not_available": (
        "Not available from Trigify (the route answered that it is not enabled or not found):"
        " {routes}"
    ),
    "report.missing": (
        "Not reachable through the Trigify API: {kinds}. A fallback export can fill them in later."
    ),
    "report.redacted": (
        "Credentials redacted: {count}. They are not in the Archive; enter those credentials"
        " again when rebinding in Trekk."
    ),
    "report.integration_credentials": (
        "Integrations whose credentials were not extracted: {count}. Reconnect them in Trekk."
    ),
    "report.not_written": "No Archive was written, so the report {path} was not written.",
    "harvest.exists": "Archive already written: {path}",
    "harvest.exists.partial": (
        "Archive already written: {path}. Objects not harvested in full are marked partial."
    ),
    "interrupted.stopped": "Harvest stopped.",
    "interrupted.retries": "Trigify did not answer {operation} after repeated retries.",
    "interrupted.resume": "Run the same command with --out {out} to resume where it stopped.",
    "error.key_rejected": (
        "Trigify rejected the API key. Copy the key again from your Trigify settings and run"
        " the command again."
    ),
    "error.no_subscription": (
        "Trigify refused the key: the workspace has no API subscription. Renew it in Trigify,"
        " then run the command again."
    ),
    "error.workspace_mismatch": (
        "The checkpoint in {directory} belongs to workspace {checkpoint_name}"
        " ({checkpoint_id}), but this key opens workspace {name} ({id}). Use another --out"
        " directory for this workspace."
    ),
    "error.key_unconfirmed": (
        "The checkpoint in {directory} belongs to workspace {name} ({id}), and this key was"
        " never confirmed for it while the Trigify API answered. Use a key that harvested into"
        " this directory before, or another --out directory."
    ),
    "error.storage": (
        "Cannot write to {directory}: {reason}. Free disk space or fix the permissions, then"
        " run the command again."
    ),
    "error.checkpoint_format": (
        "The checkpoint in {directory} was written by another Harvester version and cannot be"
        " resumed. Use another --out directory."
    ),
    "error.sealed_missing": (
        "The Archive {path} this directory was finished with is missing. Restore it, or use"
        " another --out directory."
    ),
    "error.archive_refused": (
        "The Archive was not written: {kind} {id} holds a credential in field {field}. The"
        " checkpoint is kept."
    ),
    "error.request_failed": (
        "Trigify answered {operation} unexpectedly ({detail}). The checkpoint is kept; run the"
        " command again later."
    ),
}


def text(key: str, **params: object) -> str:
    return CATALOGUE[key].format(**params)


def say(key: str, **params: object) -> None:
    """Print one catalogued line."""
    typer.echo(text(key, **params))


def ask_secret(key: str) -> str:
    """Hidden prompt; an empty answer comes back as ''."""
    answer: str = typer.prompt(CATALOGUE[key], hide_input=True, default="", show_default=False)
    return answer


def clock_time(moment: datetime, tz: tzinfo | None) -> str:
    """`HH:MM TZ` in `tz` (None: the machine's local time zone)."""
    local = moment.astimezone(tz)
    return local.strftime("%H:%M %Z")
