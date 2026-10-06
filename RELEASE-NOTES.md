# Trekk Harvester release notes

## 0.1.0 — 2026-10-06

First customer release of the `trekk` command, published as wheels on the GitHub release
https://github.com/trekk-cc/trekk-cli/releases/tag/v0.1.0 (install commands in the README). One
run harvests one Trigify workspace into one Archive, `trekk-archive-<label>.zip`.

- Integration credentials are never extracted. Reconnect your integrations in Trekk.
- Credentials redacted in the Archive must be re-entered when rebinding in Trekk.
- The Credit ledger covers the last 365 days: older entries are not reachable through the
  Trigify API.
- Archives follow Archive spec 1.3.x.
- An interrupted harvest resumes when you run the same command with the same `--out`. The
  checkpoint keeps only redacted data, and its pages are deleted once the final Archive
  exists.
- `--report FILE` writes a JSON run report without ids, record values or the key; it is never
  sent anywhere.

Trekk is an independent product, not affiliated with or endorsed by Trigify. Trigify is a
trademark of its owner.
