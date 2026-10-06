"""The only Archive writer: deterministic ZIP, canonical JSON, redacts credentials, atomic."""

import errno
import json
import os
import shutil
import zipfile
from collections.abc import Iterable, Sequence
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import IO, Any

from trekk_archive.errors import ArchivePublishError, CredentialValueError
from trekk_archive.models import (
    KINDS,
    SPEC_VERSION,
    ArchiveObject,
    Kind,
    Manifest,
    MissingKind,
    Redaction,
    writer_version,
)
from trekk_archive.secrets import REDACTED, is_credential, redact

MANIFEST_ENTRY = "manifest.json"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def objects_entry(kind: str) -> str:
    return f"objects/{kind}.jsonl"


def canonical_json(value: Any) -> bytes:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (text + "\n").encode()


def _zip_info(name: str, size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    info.create_system = 3
    info.external_attr = 0o644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    info.compress_level = 6
    info.file_size = size
    return info


def _is_identity(field: str) -> bool:
    return field == "trigify_id" or field.endswith("_id")


def _line(obj: ArchiveObject) -> tuple[bytes, list[str]]:
    """The object's canonical line with every credential outside its identity fields redacted,
    and the redacted paths. A credential-shaped identity field refuses the object: ids are
    Trigify's, and rewriting one would break the references to it."""
    dump = obj.model_dump(mode="json")
    for field, value in dump.items():
        if _is_identity(field) and isinstance(value, str) and is_credential(value):
            shown = REDACTED if is_credential(obj.trigify_id) else obj.trigify_id
            raise CredentialValueError(obj.kind, shown, field)
    identity = {field: value for field, value in dump.items() if _is_identity(field)}
    rest, paths = redact({field: value for field, value in dump.items() if field not in identity})
    return canonical_json({**rest, **identity}), paths


def write_archive(
    path: Path,
    *,
    trigify_workspace_id: str,
    workspace_label: str,
    written_at: datetime,
    objects: Iterable[ArchiveObject],
    signal_types: Sequence[str] = (),
    not_used: Sequence[Kind] = (),
    not_listed: Sequence[Kind] = (),
    not_available: Sequence[str] = (),
    missing: Sequence[MissingKind] = (),
) -> Manifest:
    """Stream `objects` into a new Archive at `path`; on any error nothing is left behind.
    The other arguments go to the manifest fields of the same name; the manifest's
    `redactions` record where the writer replaced credentials (`secrets.redact`).
    The staged ZIP is published with a hard link (atomic, `FileExistsError` when `path`
    appeared meanwhile: an existing file is never overwritten); the staging directory is
    then removed.
    A filesystem without hard links raises `ArchivePublishError`."""
    if path.exists():
        raise FileExistsError(path)
    counts: dict[Kind, int] = dict.fromkeys(KINDS, 0)
    redactions: list[Redaction] = []
    with TemporaryDirectory(prefix=".trekk-archive-", dir=path.parent) as tmp_name:
        tmp = Path(tmp_name)
        with ExitStack() as stack:
            parts: dict[Kind, IO[bytes]] = {}
            for obj in objects:
                if obj.kind not in parts:
                    parts[obj.kind] = stack.enter_context((tmp / obj.kind).open("wb"))
                line, paths = _line(obj)
                parts[obj.kind].write(line)
                counts[obj.kind] += 1
                if paths:
                    redactions.append(
                        Redaction(kind=obj.kind, trigify_id=obj.trigify_id, paths=paths)
                    )
        manifest = Manifest(
            spec_version=SPEC_VERSION,
            writer_version=writer_version(),
            trigify_workspace_id=trigify_workspace_id,
            workspace_label=workspace_label,
            written_at=written_at,
            counts=counts,
            signal_types=list(signal_types),
            not_used=list(not_used),
            not_listed=list(not_listed),
            not_available=list(not_available),
            missing=list(missing),
            redactions=redactions,
        )
        staged = tmp / "archive.zip"
        with zipfile.ZipFile(staged, "x") as archive:
            body = canonical_json(manifest.model_dump(mode="json"))
            archive.writestr(_zip_info(MANIFEST_ENTRY, len(body)), body)
            for kind in KINDS:
                if counts[kind]:
                    part = tmp / kind
                    info = _zip_info(objects_entry(kind), part.stat().st_size)
                    with part.open("rb") as source, archive.open(info, "w") as target:
                        shutil.copyfileobj(source, target)
        _publish(staged, path)
    return manifest


def _publish(staged: Path, path: Path) -> None:
    try:
        os.link(staged, path)  # the staged name goes with its TemporaryDirectory
    except OSError as error:
        if error.errno in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
            raise ArchivePublishError(path.parent) from error
        raise
