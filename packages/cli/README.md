# Trekk Harvester

The Trekk Harvester copies one Trigify workspace, read with your own Trigify API key, into an
Archive you own: a single ZIP file that follows the published Archive spec 1.3.x. It runs on
your machine and reads the workspace from the Trigify API at `https://api.trigify.io`. It needs
no Trekk account, uses no Credits and sends the key only to that API.

Trigify's API stops answering at the Cutoff, **22 Oct 2026 23:59 BST**. Harvest before then:
after the Cutoff, lossless harvest is no longer available.

## Install

The Harvester is published as wheels on the GitHub release
[v0.1.0 of trekk-cc/trekk-cli](https://github.com/trekk-cc/trekk-cli/releases/tag/v0.1.0), not on
a package index, so install its three wheels by URL. You need
[uv](https://docs.astral.sh/uv/) (it downloads Python 3.14 if it is missing):

```bash
uv tool install --python 3.14 \
  https://github.com/trekk-cc/trekk-cli/releases/download/v0.1.0/trekk_cli-0.1.0-py3-none-any.whl \
  --with https://github.com/trekk-cc/trekk-cli/releases/download/v0.1.0/trekk_harvester-0.1.0-py3-none-any.whl \
  --with https://github.com/trekk-cc/trekk-cli/releases/download/v0.1.0/trekk_archive-0.1.0-py3-none-any.whl
```

or [pipx](https://pipx.pypa.io/) 1.7 or later:

```bash
pipx install --python 3.14 --fetch-missing-python \
  --preinstall https://github.com/trekk-cc/trekk-cli/releases/download/v0.1.0/trekk_archive-0.1.0-py3-none-any.whl \
  --preinstall https://github.com/trekk-cc/trekk-cli/releases/download/v0.1.0/trekk_harvester-0.1.0-py3-none-any.whl \
  https://github.com/trekk-cc/trekk-cli/releases/download/v0.1.0/trekk_cli-0.1.0-py3-none-any.whl
```

Both install the `trekk` command. Check it with `trekk --version`.

## Harvest a workspace

Run one harvest per Trigify workspace, each with its own label and output directory:

```bash
export TRIGIFY_API_KEY=...        # or leave it unset and type the key at the hidden prompt
trekk harvest --label "Acme Ltd" --out ~/trekk/acme --report ~/trekk/acme-report.json
```

- `--label`: your name for the workspace. It is recorded in the Archive and names the file:
  `--label "Acme Ltd"` writes `trekk-archive-acme-ltd.zip`.
- `--out`: the directory for the checkpoint and the Archive. Use a separate directory per
  workspace: while a harvest there is unfinished, another workspace's key is refused. Once the
  directory is finished, a run there just reports its Archive without any request, so the key
  is not checked.
- `--report`: optional. Writes a JSON run report (object counts, mismatches with Trigify's
  lists, partial, missing and unused kinds, errors) only when the run ends with an Archive. It
  holds no ids, record values or key, and the Harvester never sends it anywhere.
- `--monitors`: optional. A text file with one LinkedIn profile URL per line, for Monitors
  Trigify's API does not list.

The key is never accepted as an option, never printed and never written to disk.

`trekk harvest --help` lists every option and exit code.

## Interrupted or partial runs

Run the same command with the same `--out` to resume where a run stopped. The checkpoint in
`<out>/checkpoint/` keeps only redacted data, and its pages are deleted once the final Archive
is written. When an Archive holds objects marked partial, running the command again harvests
what is missing and writes `trekk-archive-<label>-2.zip` (then `-3`, ...); earlier Archives are
kept. Once the final Archive exists, the command reports it and makes no request.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | OK: Archive written (or already written) |
| 1 | FAILURE: nothing more can be done with this key or directory |
| 2 | USAGE: missing key, unusable label or bad option |
| 3 | PARTIAL: Archive written with objects marked partial |
| 4 | INTERRUPTED: checkpoint kept, run the same command again to resume |

## Credentials

Integration credentials are never extracted: reconnect your integrations in Trekk.
Credentials found anywhere else in the data are replaced by a marker in the Archive; enter them
again when rebinding in Trekk.

The release notes for this version ship with the package metadata.

## Trademarks

Trekk is an independent product, not affiliated with or endorsed by Trigify. Trigify is a
trademark of its owner.

Licensed under the Apache License 2.0 (`LICENSE`); copyright and attribution in `NOTICE`.
