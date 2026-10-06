# Trekk Archive format, spec v1

Spec version: **1.3.0** (semantic versioning; see [CHANGELOG.md](CHANGELOG.md)).

License: Apache-2.0 (see [LICENSE](LICENSE)).

Trekk is an independent product, not affiliated with or endorsed by Trigify. Trigify is a
trademark of its owner.

An Archive is the one file format for a Trigify workspace's data: Harvester output, full-account
export and Import input are all Archives in this format. One Archive holds one Trigify
workspace.

Credentials are never in an Archive.

Machine-readable schemas (JSON Schema Draft 2020-12), generated from the models in
`packages/archive` and checked against them by a test:

- [`manifest.schema.json`](manifest.schema.json): the manifest
- [`object.schema.json`](object.schema.json): one object line (discriminated on `kind`)

## Layout

An Archive is a ZIP file:

| Entry | Content |
|-------|---------|
| `manifest.json` | First entry. One JSON object (the manifest). |
| `objects/<kind>.jsonl` | One entry per kind with at least one object, in the canonical kind order below. One JSON object per line, in the order the objects were written. |

Every JSON document is canonical: keys sorted, no insignificant whitespace (`,` and `:`
separators), UTF-8 without `\u` escaping, terminated by a newline. Entries are DEFLATE-compressed
(level 6) with a fixed timestamp (1980-01-01 00:00:00) and mode `0644`, so the same content gives
byte-identical Archives for the same writer version and compressor (zlib) build. Readers ignore entries they do not know.

A reader of spec 1.x opens a newer 1.y Archive that adds fields or kinds: it ignores manifest
and object fields it does not know, and kinds it does not know in the manifest's `counts`,
`not_used`, `not_listed`, `missing` and `redactions` (and their `objects/<kind>.jsonl` entries).
The values of existing enumerations (such as `source`) do not change within major 1. A writer
still emits only the fields and kinds of its own spec version.

## Manifest

| Field | Meaning |
|-------|---------|
| `spec_version` | Spec version the Archive was written with (`1.3.0`). Readers accept any `1.x.y`. |
| `writer_version` | Version of the `trekk-archive` package that wrote it. |
| `trigify_workspace_id` | The original Trigify workspace id. |
| `workspace_label` | The label the customer gave the workspace. |
| `written_at` | When the Archive was written, UTC, ISO 8601 with `Z`. |
| `counts` | Object count per kind; the writer emits every kind, zero when empty. A reader counts a kind missing from an earlier 1.x manifest as zero. |
| `signal_types` | Signal types the workspace used (sorted, unique): from its Signal subscriptions' `config.signals[].type` and its Signals' `signal_type`. `[]` when absent. |
| `not_used` | Kinds of a feature Trigify answered as never used by the workspace (403 or 404 on the first request of the feature's harvest step: Searches, topics, Signal subscriptions, Targets, the Signal feed, workflows, workflow tables, the Credit ledger, usage, Integrations), e.g. `signal_subscription`. Not an error. `[]` when absent. |
| `not_listed` | Kinds the Harvester had no source listing for (no Monitor list given and the legacy listing unavailable): `monitor`, `monitor_result`, `monitor_post_result`. `[]` when absent. |
| `not_available` | Trigify routes that answered 403 or 404, by path with path parameters filled in (e.g. `/v1/profile/track`, `/v1/account`, `/v1/integrations/hubspot/crm-fields`): legacy routes, org metadata parts and Integration inventory sources. The data they would have given is absent. Not an error. `[]` when absent (before 1.3.0 only legacy routes answering 404). |
| `missing` | Kinds the Trigify API offers no operation for, so none of their objects could be harvested: `{"kind": ..., "source": "missing", "reason": ...}` (e.g. reason `no API operation`), in canonical kind order. A fallback export can fill them later. `[]` when absent (before 1.2.0). |
| `redactions` | Per object holding credentials, in write order: `{"kind": ..., "trigify_id": ..., "paths": [...]}`, the dot-joined paths (sorted as strings) where the writer put the redaction marker (see [Credentials](#credentials)); never the values. `[]` when absent (before 1.2.0). |

## Objects

Every object has:

| Field | Meaning |
|-------|---------|
| `kind` | One of the kinds below. |
| `trigify_id` | The object's original Trigify id. |
| `source` | `api`: read from the Trigify API. `fallback`: rebuilt from another Trigify response because its own operation was unavailable. `missing`: the Trigify API has no operation for it; the object records that it exists but its content could not be read. A whole kind the API cannot reach is listed once in the manifest's `missing` instead, with no objects. |
| `partial` | `null`, or `{"reason": "..."}` when the object could not be read in full (retries exhausted, API turned off, Cutoff passed). Partial objects are kept, never omitted. |
| `data` | The raw Trigify payload of the object, unchanged (lossless). |

Parent links are the parent's Trigify id.

| Kind | Object | Parent links |
|------|--------|--------------|
| `org` | Org metadata | |
| `integration` | Integration inventory | `mappings`: id-keyed inventory (channels, campaigns, CRM field schemas); never credentials |
| `credit_ledger_entry` | Credit ledger history | |
| `usage_record` | Credit ledger history (usage) | |
| `search` | Watches: Searches | |
| `search_result` | Trigify result records of a Search | `search_id` |
| `monitor` | Watches: Monitors | |
| `monitor_result` | Trigify result records of a Monitor | `monitor_id` |
| `monitor_post_result` | Trigify result records of one post of a Monitor | `monitor_id`, `post_url` |
| `topic` | Trigify topics | |
| `topic_engagement` | Trigify result records of a topic | `topic_id` |
| `target_list` | Target lists | |
| `target` | Targets | `target_list_id` (or `null`) |
| `signal_subscription` | Signals (subscriptions) | |
| `signal` | Signals | |
| `insight` | Insights | |
| `workflow` | Workflow definitions | |
| `workflow_draft` | Workflow drafts | `workflow_id` |
| `run` | Runs | `workflow_id` |
| `output_row` | Output pages, with nested and loop rows | `workflow_id`, `run_id`, `parent_row_id` (or `null`), `loop_index` (or `null`, else `>= 0`) |
| `workflow_table` | Workflow tables | `workflow_id` (or `null`) |
| `workflow_table_row` | Workflow table rows | `table_id` |
| `list` | Lists | |
| `list_member` | List members | `list_id` |
| `agent_memory_table` | Agent Memory tables | |
| `agent_memory_record` | Agent Memory records | `table_id` |
| `legacy_route` | Items of a legacy Trigify route outside the published API | `route`: the route path |

### Workflows, Runs and Output

- `workflow`: `data` is the workflow's detail plus the fields only the list gives (such as
  `execution_count`); when the detail could not be read, the list item, marked partial. Its
  state (`status` `DRAFT` or `PUBLISHED`, `enabled`, the published definition `workflow`) is kept
  as Trigify returned it; a draft-only workflow has no published definition.
- `workflow_draft`: `trigify_id` is the draft's `id`, `workflow_id` its workflow. A workflow
  without a draft has none.
- `run`: `trigify_id` is the Run's `run_id`; `data` is
  `{"summary": <Run list item>, "detail": <Run detail or null>, "variables": <Run variables or null>}`.
- `output_row`: one row per step of the Run detail's ordered `steps`, in step order; `data` is
  the step as returned and `trigify_id` is `sha256:` + the first 32 hex of the sha256 of the
  step's canonical JSON (identical steps share one row). Rows of steps run inside a Loop (non-null
  `iteration`) are the nested rows: `loop_index` is that `iteration` when it is a non-negative
  whole number, else `null` (the raw value stays in `data`). Trigify gives no parent reference,
  so `parent_row_id` is `null`.

### Workflow tables

- `workflow_table`: `data` is the table as Trigify lists it, `trigify_id` its `id` (a table
  without one gets the `sha256:` id of its canonical JSON). Trigify gives no workflow reference,
  so `workflow_id` is `null`.
- Trigify's API has no operation for Lists, List members, workflow table rows, Agent Memory tables
  or records: the Harvester lists `list`, `list_member`, `workflow_table_row`,
  `agent_memory_table` and `agent_memory_record` in the manifest's `missing`.

### Org, Credit ledger and usage

- `org`: one object, `trigify_id` the workspace (organisation) id; `data` is
  `{"org": <organisation>, "account": <API key account>, "credits_balance": <credit balance>,
  "membership": <this workspace's entry of the key owner's organisation list, with its role>}`,
  each as Trigify returned it. A part whose route answered 403 or 404 is absent (its path is in
  the manifest's `not_available`); `membership` is absent when the list has no entry for the
  workspace. Other workspaces' entries are not kept. Members and their roles have no kind:
  Trigify gives only `member_count` (in `org`) and the key owner's `role` (in `membership`).
- `credit_ledger_entry`: one per Credit usage record Trigify lists for the last 365 days (the
  API's maximum window; older entries cannot be read through the API), `trigify_id` its `id`
  (a record without one gets the `sha256:` id of its canonical JSON), `data` as returned.
- `usage_record`: one per usage summary for Trigify's default period (the current billing
  month), `trigify_id` the `sha256:` id of its canonical JSON, `data` as returned.

### Integrations

- `integration`: one per item Trigify lists, `data` the item as returned, `trigify_id` its
  `type` (a later item of the same type, or one without a `type`, gets the `sha256:` id of its
  canonical JSON).
- `mappings` (connected integrations only, else `{}`): the inventory of each source Trigify
  offers for the type (Slack channels and users, CRM fields, sequencer campaigns, Linear teams
  and users, Notion databases, Airtable bases, Google Sheets documents), one answer each. Each
  mapping is keyed by item `id` (an item without a usable or with a repeated `id` gets the
  `sha256:` id of its canonical JSON). A source answering a list, or an object with one list,
  gives the mapping named after the source (e.g. `slack_channels`, `campaigns`); an object with
  several lists gives one mapping per list named `<source>_<field>` (e.g.
  `crm_fields_contacts`, `crm_fields_companies`); an object without a list gives one entry. A
  source whose route answered 403 or 404 gives no mapping (its path is in `not_available`). An
  answer saying Trigify holds more (`has_more`) marks the integration partial; the first answer is
  kept.
- Integration credentials are never extracted: the customer reconnects each integration in Trekk.

### Reserved kinds

`account_signal`, `dynamic_list` and `social_reaction` are reserved for the M4 legacy modes
(`$defs.ReservedKind` in the object schema). They are never valid as `kind` in v1.

## Credentials

Credentials are never in an Archive. The writer replaces each one by the marker
`[redacted: credential]` and records where in the manifest's `redactions` (paths only, never the
value), outside an object's identity fields (`trigify_id` and every `*_id` field):

- the value of any key in `x-trekk-secret-fields` (compared lowercased with `_` and `-`
  removed, at any depth) that is not null: a scalar whole; an object or array keeps its shape
  and every non-null scalar leaf inside it becomes the marker (one path per leaf; an empty object
  or array stays as it is), so typed positions such as an Integration's `mappings` stay valid;
- the same for the non-null `value` of an object whose `name` or `key` is such a field name
  (headers stored as name/value pairs, e.g. `{"name": "x-api-key", "value": ...}`);
- any whole string value matching one of `x-trekk-credential-patterns` (OAuth and access tokens,
  API keys, `trig_` keys, JWTs, bearer headers);
- any key matching one of those patterns: renamed to the marker (`[redacted: credential] 2`,
  `... 3` on a collision within its dict), its value still checked.

A value already equal to the marker, or a key starting with it, is recorded too, so writing a
read Archive again gives the same bytes. Redacted credentials must be entered again when the
objects that used them are rebound in Trekk.

A credential-shaped identity field refuses the whole Archive, naming the kind and field (never the
value): ids are Trigify's, and rewriting one would break the references to it.

The reader drops every secret-field key, at any depth, whose value is not what the writer emits
(null, the marker, or an object or array whose every leaf is the marker or null), and reports
each drop.

## Reader safety

The reader checks the file before reading any object and never extracts entries to disk. It
refuses:

| Rule | Default cap |
|------|-------------|
| `max_total_bytes`: file size, and the sum of uncompressed entry sizes | 20 GiB |
| `max_entries`: number of entries | 1000 |
| `max_entry_bytes`: uncompressed size of one entry | 4 GiB |
| `max_compression_ratio`: uncompressed / compressed size of one entry | 250 |
| `absolute_path`: an entry name starting with `/`, `\` or a drive letter | |
| `parent_path`: an entry name with a `..` segment | |
| `duplicate_entry`: two entries with the same name | |

It also refuses a file that is not a ZIP, an Archive without `manifest.json`, a manifest whose
`spec_version` major is not `1`, and an `objects/<kind>.jsonl` whose line count differs from the
manifest's count.
