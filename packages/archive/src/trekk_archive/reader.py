"""The only Archive reader: checks caps and paths before reading anything, streams objects
kind by kind, drops secret-field keys, never extracts to disk."""

import io
import json
import re
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any, NamedTuple, Self

from pydantic import TypeAdapter, ValidationError

from trekk_archive.errors import (
    CorruptArchiveError,
    MissingManifestError,
    UnsafeArchiveError,
    UnsupportedSpecVersionError,
)
from trekk_archive.limits import ReaderLimits
from trekk_archive.models import KINDS, ArchiveObject, Manifest
from trekk_archive.secrets import drop_secret_fields
from trekk_archive.writer import MANIFEST_ENTRY, objects_entry

SUPPORTED_MAJOR = 1
_OBJECT = TypeAdapter[ArchiveObject](ArchiveObject)
_DRIVE = re.compile(r"^[A-Za-z]:")


class DroppedField(NamedTuple):
    kind: str
    trigify_id: str
    path: str


class ArchiveReader:
    """An open, checked Archive. Use as a context manager; iterate `objects()` once."""

    def __init__(self, archive: zipfile.ZipFile, manifest: Manifest) -> None:
        self._archive = archive
        self.manifest = manifest
        self.dropped: list[DroppedField] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._archive.close()

    def objects(self) -> Iterator[ArchiveObject]:
        """Yield typed objects kind by kind in canonical order; unknown entries are ignored."""
        names = set(self._archive.namelist())
        for kind in KINDS:
            entry = objects_entry(kind)
            count = 0
            if entry in names:
                for number, line in enumerate(_lines(self._archive, entry), start=1):
                    yield self._object(kind, entry, number, line)
                    count += 1
            if count != self.manifest.counts[kind]:
                raise CorruptArchiveError(
                    f"{entry}: {count} objects, manifest counts {self.manifest.counts[kind]}"
                )

    def _object(self, kind: str, entry: str, number: int, line: str) -> ArchiveObject:
        where = f"{entry} line {number}"
        raw = _json(line, where)
        if not isinstance(raw, dict) or raw.get("kind") != kind:
            raise CorruptArchiveError(f"{where}: not a {kind} object")
        paths: list[str] = []
        clean = drop_secret_fields(raw, paths)
        trigify_id = str(raw.get("trigify_id"))
        self.dropped += [DroppedField(kind, trigify_id, path) for path in paths]
        try:
            return _OBJECT.validate_python(clean)
        except ValidationError as error:
            raise CorruptArchiveError(f"{where}: invalid {kind}: {error}") from error


def open_archive(path: Path, limits: ReaderLimits) -> ArchiveReader:
    """Open and check an Archive: size, ZIP, entry paths and caps, manifest and spec major."""
    size = path.stat().st_size
    if size > limits.max_total_bytes:
        raise UnsafeArchiveError("max_total_bytes", f"file is {size} bytes")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise CorruptArchiveError(f"not a ZIP archive: {error}") from error
    try:
        _check_entries(archive.infolist(), limits)
        return ArchiveReader(archive, _manifest(archive))
    except BaseException:
        archive.close()
        raise


def _check_entries(entries: list[zipfile.ZipInfo], limits: ReaderLimits) -> None:
    seen: set[str] = set()
    for info in entries:
        name = info.filename
        if name.startswith(("/", "\\")) or _DRIVE.match(name):
            raise UnsafeArchiveError("absolute_path", name)
        if ".." in re.split(r"[/\\]", name):
            raise UnsafeArchiveError("parent_path", name)
        if name in seen:
            raise UnsafeArchiveError("duplicate_entry", name)
        seen.add(name)
    if len(entries) > limits.max_entries:
        raise UnsafeArchiveError("max_entries", f"{len(entries)} entries")
    for info in entries:
        if info.file_size > limits.max_entry_bytes:
            raise UnsafeArchiveError(
                "max_entry_bytes", f"{info.filename} is {info.file_size} bytes"
            )
    total = sum(info.file_size for info in entries)
    if total > limits.max_total_bytes:
        raise UnsafeArchiveError("max_total_bytes", f"entries total {total} bytes")
    for info in entries:
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > limits.max_compression_ratio:
            raise UnsafeArchiveError("max_compression_ratio", f"{info.filename} ratio {ratio:.1f}")


def _manifest(archive: zipfile.ZipFile) -> Manifest:
    if MANIFEST_ENTRY not in archive.namelist():
        raise MissingManifestError()
    raw = _json("".join(_lines(archive, MANIFEST_ENTRY)), MANIFEST_ENTRY)
    declared = raw.get("spec_version") if isinstance(raw, dict) else None
    if _major(declared) != SUPPORTED_MAJOR:
        raise UnsupportedSpecVersionError(declared)
    try:
        return Manifest.model_validate(raw)
    except ValidationError as error:
        raise CorruptArchiveError(f"{MANIFEST_ENTRY}: invalid manifest: {error}") from error


def _major(declared: object) -> int | None:
    if not isinstance(declared, str):
        return None
    major = declared.split(".", 1)[0]
    return int(major) if major.isascii() and major.isdigit() and len(major) <= 9 else None


def _lines(archive: zipfile.ZipFile, entry: str) -> Iterator[str]:
    try:
        with archive.open(entry) as raw, io.TextIOWrapper(raw, encoding="utf-8") as text:
            yield from text
    except (
        zipfile.BadZipFile,
        UnicodeDecodeError,
        EOFError,
        zlib.error,
        RuntimeError,  # encrypted entry
        NotImplementedError,  # unsupported compression method
    ) as error:
        raise CorruptArchiveError(f"{entry}: {error}") from error


def _json(text: str, where: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise CorruptArchiveError(f"{where}: invalid JSON: {error.msg}") from error
