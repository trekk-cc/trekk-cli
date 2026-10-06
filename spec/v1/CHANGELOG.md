# Changelog

## 1.3.0 — 2026-10-06

- A secret field's non-null object or array keeps its shape: each non-null scalar leaf inside it
  becomes `[redacted: credential]` (one `redactions` path per leaf); a scalar is replaced whole as
  before. The reader keeps a secret field whose every leaf is the marker or null.
- Readers of 1.x open newer 1.y Archives that add fields or kinds: unknown manifest and object
  fields are ignored, and unknown kinds in `counts`, `not_used`, `not_listed`, `missing` and
  `redactions` are dropped. Values of existing enumerations do not change within major 1. The published schemas no longer forbid additional properties.
- `not_available` holds any route that answered 403 or 404 (legacy routes, org metadata parts,
  Integration inventory sources); `not_used` covers the first request of every feature step.
- Documented the `org`, `credit_ledger_entry` (365-day window), `usage_record` and `integration`
  payloads and the mapping-name rule.

## 1.2.0 — 2026-10-06

- The writer redacts credentials instead of refusing the Archive: each becomes
  `[redacted: credential]` (a credential-shaped key is renamed to it). A secret field's
  non-null value of any type (object, list, number, `""`) becomes the marker string, as does the
  `value` of a name/value pair whose `name` or `key` is a secret field name. Identity fields
  (`trigify_id`, `*_id`) holding a credential still refuse it.
- `SecretFieldError` is removed from `trekk_archive` (never raised any more).
- Optional manifest field `redactions`: per object, the paths the writer redacted (never values).
- Optional manifest field `missing`: kinds the Trigify API cannot reach, each
  `{"kind", "source": "missing", "reason"}`.
- The reader keeps a secret-field key whose value is the redaction marker.
- Documented the `workflow_table` payload.

## 1.1.1 — 2026-10-06

- Documented the payloads of `workflow`, `workflow_draft`, `run` and `output_row` (Runs keep
  their list summary, detail and variables; Output rows are the Run detail's steps). No schema
  change.

## 1.1.0 — 2026-10-06

- New kind `monitor_post_result` (after `monitor_result`): result records of one post of a
  Monitor, linked by `monitor_id` and `post_url`.
- `legacy_route` is no longer reserved: items of a legacy Trigify route, with its `route` path.
- Optional manifest fields `signal_types`, `not_used`, `not_listed` and `not_available`
  (`[]` when absent).
- `counts` may lack kinds added after the manifest's spec version; readers count them as zero,
  so every 1.0.0 Archive stays readable.

## 1.0.0 — 2026-10-06

- First published version: ZIP layout with `manifest.json` and `objects/<kind>.jsonl`.
- 25 object kinds covering Watches, Targets, Workflows, Runs and Output, Lists, Agent Memory,
  Credit ledger, org metadata, Integration inventory, topics, result records, Signals and Insights.
- Every object carries its Trigify id, `source` and an optional `partial` marker.
- Reserved M4 kinds: `account_signal`, `dynamic_list`, `social_reaction`, `legacy_route`.
- Secret-field and credential-pattern registries published in the object schema.
