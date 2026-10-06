"""Append-only per-page checkpoint: one jsonl per crawl plus `state.json` naming each crawl's
committed byte size, cursor, done flag, page count, list total and last failure, and the send
times of the requests within the last rate window (`sent`, UTC epoch seconds). It also counts
the commits made (`commits`), records per written Archive, in writing order, the commit count it
was built from and the list totals it reported (`archives`), and which harvest steps finished
(`steps`). `keys` holds the salted scrypt fingerprints (`key_salt`, hex) of the API keys Trigify
confirmed open the checkpoint's `workspace`, never a key: a run whose first request finds the API
off may finalize the checkpoint only with one of them.

A commit appends the page's records redacted (`redacted`: credentials replaced as the Archive
writer does, id-keyed values kept), fsyncs, then atomically replaces `state.json`. Bytes past a
crawl's committed size (a torn page) are truncated on load, so a resumed run never sees a record
twice. Commits never `await`: a cancelled harvest stops between pages, never inside one.

Once the final Archive exists (or the Cutoff passed), the checkpoint is sealed: `state.json`
names that Archive (`sealed`), every page file is deleted and only `state.json` stays.
"""

import json
import os
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from trekk_archive.secrets import redact
from trekk_harvester.errors import (
    CheckpointFormatError,
    StorageError,
    Workspace,
    WorkspaceMismatchError,
)
from trekk_harvester.fingerprint import fingerprint, new_salt

STATE = "state.json"
FORMAT = 2

type Cursor = str | int | None
type SentTimes = Callable[[], Iterable[float]]


@dataclass(frozen=True, slots=True)
class CrawlState:
    """`pages`: committed pages; `total`: the list total from the first page (None when Trigify
    gave none); `failure`: why the last attempt stopped short (a commit clears it)."""

    size: int = 0
    cursor: Cursor = None
    done: bool = False
    pages: int = 0
    total: int | None = None
    failure: str | None = None


def redacted(record: Any) -> Any:
    """`record` as the checkpoint stores it: every credential redacted (`trekk_archive`'s
    `redact`), except that a key equal to `id` or ending in `_id` keeps its scalar value at any
    depth (an object or array under it is redacted like any other; follow-up requests, dedupe
    and the writer's refusal of a credential-shaped id rely on the scalar ids)."""
    return _keep_ids(record, redact(record)[0])


def _is_id(key: str) -> bool:
    return key == "id" or key.endswith("_id")


def _keep_ids(original: Any, clean: Any) -> Any:
    if isinstance(original, dict) and isinstance(clean, dict):
        return {
            key: original[key]
            if key in original and _is_id(key) and not isinstance(original[key], dict | list)
            else _keep_ids(original.get(key), value)
            for key, value in clean.items()
        }
    if isinstance(original, list) and isinstance(clean, list):
        return [_keep_ids(item, value) for item, value in zip(original, clean, strict=True)]
    return clean


def _load(directory: Path) -> dict[str, Any] | None:
    """The state in `directory` (None without one); another format is refused."""
    path = directory / STATE
    if not path.exists():
        return None
    state: dict[str, Any] = json.loads(path.read_bytes())
    if state.get("format") != FORMAT:
        raise CheckpointFormatError(directory)
    return state


class Checkpoint:
    def __init__(
        self,
        directory: Path,
        state: dict[str, Any],
        *,
        resumed: bool,
        sent: SentTimes | None = None,
    ) -> None:
        """`sent` gives the send times every state write persists (None: keep what is there)."""
        self.directory = directory
        self.resumed = resumed
        self._state = state
        self._sent = sent

    @staticmethod
    def sent_times(directory: Path) -> list[float]:
        """The send times persisted in `directory`'s state (empty without a checkpoint)."""
        state = _load(directory)
        sent: list[float] = [] if state is None else state.get("sent", [])
        return sent

    @classmethod
    def existing(cls, directory: Path, *, sent: SentTimes | None = None) -> Checkpoint | None:
        """The checkpoint in `directory` with torn page tails truncated (a sealed one: page
        files left by a crash during sealing deleted), or None."""
        state = _load(directory)
        if state is None:
            return None
        checkpoint = cls(directory, state, resumed=True, sent=sent)
        cleanup = checkpoint._delete_pages if checkpoint.sealed else checkpoint._truncate_torn
        checkpoint._storage(cleanup)
        return checkpoint

    @classmethod
    def open(
        cls, directory: Path, workspace: Workspace, *, sent: SentTimes | None = None
    ) -> Checkpoint:
        """Resume the checkpoint in `directory` (refused for another workspace) or start one.
        A refused checkpoint is left byte for byte as it was."""
        found = _load(directory)
        if found is not None:
            owner = Workspace(**found["workspace"])
            if owner.id != workspace.id:
                raise WorkspaceMismatchError(owner, workspace)
            checkpoint = cls.existing(directory, sent=sent)
            assert checkpoint is not None
            return checkpoint
        state = {
            "format": FORMAT,
            "workspace": {"id": workspace.id, "name": workspace.name},
            "crawls": {},
        }
        checkpoint = cls(directory, state, resumed=False, sent=sent)
        checkpoint._storage(lambda: directory.mkdir(parents=True, exist_ok=True))
        checkpoint._storage(checkpoint._write_state)
        return checkpoint

    @property
    def workspace(self) -> Workspace:
        return Workspace(**self._state["workspace"])

    @property
    def commits(self) -> int:
        """Pages committed over the checkpoint's life (never decreases)."""
        commits: int = self._state.get("commits", 0)
        return commits

    @property
    def archives(self) -> list[str]:
        """The names of the Archives built from this checkpoint, oldest first."""
        return list(self._state.get("archives", {}))

    @property
    def sealed(self) -> str | None:
        """The name of the Archive the checkpoint was sealed with (None: not sealed)."""
        sealed: str | None = self._state.get("sealed")
        return sealed

    def confirm(self, key: str) -> None:
        """Record that Trigify confirmed `key` opens this checkpoint's workspace (its fingerprint
        only; the salt is created on the first call). Writes the state only when it changed."""
        salt = self._state.get("key_salt")
        if salt is None:
            salt = self._state["key_salt"] = new_salt().hex()
        keys: list[str] = self._state.setdefault("keys", [])
        mine = fingerprint(key, bytes.fromhex(salt))
        if mine not in keys:
            keys.append(mine)
            self.save()

    def confirms(self, key: str) -> bool:
        """Whether `key` was confirmed for this checkpoint's workspace by an earlier run."""
        salt: str | None = self._state.get("key_salt")
        keys: list[str] = self._state.get("keys", [])
        return salt is not None and fingerprint(key, bytes.fromhex(salt)) in keys

    def advanced_past(self, archive: str) -> bool:
        """Whether pages were committed after the Archive named `archive` was built from this
        checkpoint (an Archive without a record counts as built from nothing)."""
        built: int = self._state.get("archives", {}).get(archive, {}).get("commits", 0)
        return self.commits > built

    def archived(self, archive: str, listed: Mapping[str, int]) -> None:
        """Record that the Archive named `archive` was built from the current commits, with the
        list totals Trigify gave per kind (`listed`)."""
        archives = self._state.setdefault("archives", {})
        archives[archive] = {"commits": self.commits, "listed": dict(listed)}
        self.save()

    def listed(self, archive: str) -> dict[str, int]:
        """The list totals per kind recorded for the Archive named `archive`."""
        listed: dict[str, int] = self._state["archives"][archive]["listed"]
        return listed

    def errors(self) -> list[tuple[str, str]]:
        """Distinct (step, failure) of the crawls that stopped short on a failure; the step is
        the crawl name before its first `__`."""
        found = {
            (name.split("__", 1)[0], crawl["failure"])
            for name, crawl in self._state["crawls"].items()
            if crawl.get("failure") is not None and not crawl.get("done")
        }
        return sorted(found)

    def seal(self, archive: str) -> None:
        """Seal the checkpoint with the Archive named `archive`: `state.json` names it and every
        crawl's committed size and cursor are cleared (atomically), then the page files are
        deleted. List totals, steps and Archive records are kept."""
        for name, crawl in self._state["crawls"].items():
            self._state["crawls"][name] = {**crawl, "size": 0, "cursor": None}
        self._state["sealed"] = archive
        self.save()
        self._storage(self._delete_pages)

    def complete(self, step: str) -> None:
        """Mark a harvest step finished (it ran to its end in some run)."""
        steps = self._state.setdefault("steps", {})
        if not steps.get(step):
            steps[step] = True
            self.save()

    def completed(self, steps: Iterable[str]) -> bool:
        """Whether every one of `steps` finished."""
        done = self._state.get("steps", {})
        return all(done.get(step) for step in steps)

    def crawl(self, name: str) -> CrawlState:
        return CrawlState(**self._state["crawls"].get(name, {}))

    def commit(
        self,
        name: str,
        records: Sequence[Any],
        *,
        cursor: Cursor,
        done: bool,
        total: int | None = None,
    ) -> None:
        """Append one page of `records`, redacted, and move the crawl to `cursor` (and `done`).
        `total` is kept from the crawl's first page only; a commit clears the crawl's failure."""
        state = self.crawl(name)
        path = self._file(name)
        payload = b"".join(
            (json.dumps(redacted(r), ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            for r in records
        )
        moved = CrawlState(
            size=state.size + len(payload),
            cursor=cursor,
            done=done,
            pages=state.pages + 1,
            total=state.total if state.pages else total,
        )
        try:
            with path.open("ab") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            self._put(name, moved, committed=True)
        except OSError as error:
            try:
                if path.exists():
                    os.truncate(path, state.size)
            finally:
                raise StorageError(self.directory, error.strerror or str(error)) from error

    def fail(self, name: str, reason: str) -> None:
        """Record why the crawl stopped short; the next run crawls it again from its cursor."""
        self._storage(lambda: self._put(name, replace(self.crawl(name), failure=reason)))

    def mark(self, name: str, failure: str) -> None:
        """Mark the crawl done without records because of `failure` (e.g. a feature never used);
        counted as a commit, so an Archive built before it is not final."""
        done = replace(self.crawl(name), done=True, failure=failure)
        self._storage(lambda: self._put(name, done, committed=True))

    def save(self) -> None:
        """Write the state again (persists the latest send times)."""
        self._storage(self._write_state)

    def records(self, name: str) -> Iterator[Any]:
        """The crawl's committed records, in commit order."""
        size = self.crawl(name).size
        if not size:
            return
        with self._file(name).open("rb") as file:
            for line in file.read(size).splitlines():
                yield json.loads(line)

    def _put(self, name: str, state: CrawlState, *, committed: bool = False) -> None:
        """Set the crawl's state (`committed`: a page was appended, counted in `commits`) and
        write it; an `OSError` restores the previous state."""
        crawls = self._state["crawls"]
        previous, commits = crawls.get(name), self.commits
        crawls[name] = asdict(state)
        self._state["commits"] = commits + committed
        try:
            self._write_state()
        except OSError:
            if previous is None:
                crawls.pop(name)
            else:
                crawls[name] = previous
            self._state["commits"] = commits
            raise

    def _file(self, name: str) -> Path:
        return self.directory / f"{name}.jsonl"

    def _delete_pages(self) -> None:
        for path in self.directory.glob("*.jsonl"):
            path.unlink()

    def _truncate_torn(self) -> None:
        for path in self.directory.glob("*.jsonl"):
            committed = self.crawl(path.stem).size
            if path.stat().st_size > committed:
                os.truncate(path, committed)

    def _write_state(self) -> None:
        if self._sent is not None:
            self._state["sent"] = list(self._sent())
        temp = self.directory / f"{STATE}.tmp"
        with temp.open("wb") as file:
            file.write(json.dumps(self._state, ensure_ascii=False, indent=1).encode())
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, self.directory / STATE)

    def _storage(self, action: Callable[[], object]) -> None:
        try:
            action()
        except OSError as error:
            raise StorageError(self.directory, error.strerror or str(error)) from error
