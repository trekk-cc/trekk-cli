"""Generate the committed spec v1 JSON Schemas: `python -m trekk_archive.schema spec/v1`."""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from trekk_archive.models import RESERVED_KINDS, SPEC_VERSION, ArchiveObject, Manifest
from trekk_archive.secrets import CREDENTIAL_PATTERNS, SECRET_FIELDS

DRAFT = "https://json-schema.org/draft/2020-12/schema"


def manifest_schema() -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "x-trekk-spec-version": SPEC_VERSION,
        **Manifest.model_json_schema(mode="serialization"),
    }


def object_schema() -> dict[str, Any]:
    schema = TypeAdapter[ArchiveObject](ArchiveObject).json_schema(mode="serialization")
    schema["$defs"]["ReservedKind"] = {
        "description": "Kinds reserved for M4; never valid as `kind` in v1.",
        "enum": list(RESERVED_KINDS),
        "type": "string",
    }
    return {
        "$schema": DRAFT,
        "title": "ArchiveObject",
        "x-trekk-spec-version": SPEC_VERSION,
        "x-trekk-secret-fields": list(SECRET_FIELDS),
        "x-trekk-credential-patterns": list(CREDENTIAL_PATTERNS),
        **schema,
    }


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write(directory: Path) -> None:
    (directory / "manifest.schema.json").write_text(render(manifest_schema()))
    (directory / "object.schema.json").write_text(render(object_schema()))


if __name__ == "__main__":
    write(Path(sys.argv[1]))
