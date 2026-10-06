"""Loading the hash-pinned Trigify specs (recovered + legacy), naming their operations and
classifying their pagination."""

import hashlib
import json
import keyword
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from trekk_harvester.trigify.operation import Pagination, PaginationKind, ParamLocation

type Schema = dict[str, Any]
type Spec = dict[str, Any]

METHODS = ("get", "post", "put", "patch", "delete")
AUTH_HEADER = "x-api-key"
DEFAULT_PAGE_SIZE = 20
# First match wins: the parameter that moves the page decides the pagination kind.
PAGINATION_PARAMS: tuple[tuple[str, PaginationKind], ...] = (
    ("paginationToken", "page_token"),
    ("cursor", "cursor"),
    ("previousCursor", "cursor"),
    ("offset", "offset"),
    ("page", "page"),
)
SIZE_PARAMS = ("page_size", "limit")
# Keywords that describe a schema without constraining it.
ANNOTATION_ONLY = frozenset({"description", "example", "default", "deprecated", "title"})
# Field names that would shadow BaseModel attributes or names the generated annotations use.
_RESERVED_FIELDS = frozenset(dir(BaseModel)) | {"str", "int", "float", "bool", "list", "dict"}
_RESERVED_FIELDS |= {"Any", "Literal", "Field"}


class SpecHashMismatchError(Exception):
    def __init__(self, path: Path, expected: str, actual: str) -> None:
        super().__init__(f"{path}: sha256 mismatch, expected {expected}, actual {actual}")
        self.expected = expected
        self.actual = actual


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pinned_sha256(spec_path: Path) -> str:
    """The pin from the `sha256sum`-format sidecar `<spec>.sha256`."""
    sidecar = spec_path.with_name(spec_path.name + ".sha256")
    digest, name = sidecar.read_text().split()
    if name != spec_path.name:
        raise ValueError(f"{sidecar}: names {name}, expected {spec_path.name}")
    return digest


def read_verified(spec_path: Path, expected: str) -> bytes:
    """The spec bytes, or `SpecHashMismatchError` when they do not hash to `expected`."""
    data = spec_path.read_bytes()
    actual = sha256_hex(data)
    if actual != expected:
        raise SpecHashMismatchError(spec_path, expected, actual)
    return data


def load_specs(spec_path: Path, legacy_path: Path, *, pins: tuple[str, str] | None = None) -> Spec:
    """The pinned spec merged with the hand-authored legacy spec: `paths` and
    `components.schemas` united (a path or schema name in both raises). Each file must hash to
    its pin (`pins`: (spec, legacy); None: each file's sidecar)."""
    expected = pins or (pinned_sha256(spec_path), pinned_sha256(legacy_path))
    spec: Spec = json.loads(read_verified(spec_path, expected[0]))
    legacy: Spec = json.loads(read_verified(legacy_path, expected[1]))
    for section in ("paths", "schemas"):
        ours = spec["paths"] if section == "paths" else spec["components"]["schemas"]
        theirs = legacy["paths"] if section == "paths" else legacy["components"]["schemas"]
        clash = sorted(set(ours) & set(theirs))
        if clash:
            raise ValueError(f"{legacy_path}: {section} already in {spec_path}: {clash}")
        ours.update(theirs)
    return spec


def op_name(method: str, path: str) -> str:
    """`<method>_<path segments>` without `v1`, `{x}` -> `by_x`, non-alphanumerics -> `_`."""
    segments = [
        f"by_{segment[1:-1]}" if segment.startswith("{") else segment
        for segment in path.strip("/").split("/")
        if segment != "v1"
    ]
    return re.sub(r"[^a-z0-9]", "_", "_".join([method, *segments]).lower())


def py_identifier(name: str, *, field: bool = False) -> str:
    """A valid Python name for `name`. Invalid names (non-word characters -> `_`), keywords and,
    for fields, names shadowing BaseModel or annotation names get a trailing `_`."""
    sanitized = re.sub(r"\W", "_", name)
    if not sanitized.isidentifier() or sanitized.startswith("_"):
        raise ValueError(f"no Python name for {name!r}")
    reserved = keyword.iskeyword(sanitized) or (field and sanitized in _RESERVED_FIELDS)
    return f"{sanitized}_" if reserved or sanitized != name else sanitized


def deref(spec: Spec, node: Schema) -> Schema:
    while "$ref" in node:
        node = spec["components"]["schemas"][node["$ref"].removeprefix("#/components/schemas/")]
    return node


def types_of(node: Schema) -> list[str]:
    declared = node.get("type")
    if declared is None:
        return []
    return [declared] if isinstance(declared, str) else list(declared)


def resolve_all_of(spec: Spec, node: Schema) -> Schema:
    """The schema an `allOf` node stands for: its only substantive part, or all parts merged
    (properties and required united, declared types intersected)."""
    parts = [part for part in node["allOf"] if not set(part) <= ANNOTATION_ONLY]
    if len(parts) == 1:
        only: Schema = parts[0]
        return only
    merged: Schema = {k: v for k, v in node.items() if k != "allOf"}
    types: set[str] | None = None
    for raw in parts:
        part = deref(spec, raw)
        if "allOf" in part:
            part = resolve_all_of(spec, part)
        if "type" in part:
            types = set(types_of(part)) if types is None else types & set(types_of(part))
        merged["properties"] = {**merged.get("properties", {}), **part.get("properties", {})}
        merged["required"] = list(
            dict.fromkeys([*merged.get("required", []), *part.get("required", [])])
        )
    if types is not None:
        if not types:
            raise ValueError("allOf parts declare disjoint types")
        merged["type"] = sorted(types)
    return merged


def iter_operations(spec: Spec) -> Iterator[tuple[str, str, str, Schema]]:
    """(name, method, path, operation) in spec order; names are unique."""
    seen: set[str] = set()
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in METHODS:
                raise ValueError(f"unsupported method {method} on {path}")
            name = op_name(method, path)
            if name in seen:
                raise ValueError(f"duplicate operation name {name}")
            seen.add(name)
            yield name, method, path, operation


def success_status(operation: Schema) -> int:
    return min(int(code) for code in operation["responses"] if code.startswith("2"))


def params_in(spec: Spec, operation: Schema, location: str) -> dict[str, Schema]:
    """Parameters by name in `query`/`path`, or the JSON body's properties for `body`."""
    if location == "body":
        body = operation.get("requestBody")
        if body is None:
            return {}
        schema = deref(spec, body["content"]["application/json"]["schema"])
        properties: dict[str, Schema] = schema["properties"]
        return properties
    return {p["name"]: p["schema"] for p in operation.get("parameters", []) if p["in"] == location}


def classify_pagination(spec: Spec, operation: Schema) -> Pagination | None:
    query = params_in(spec, operation, "query")
    body = params_in(spec, operation, "body")
    params: Mapping[str, Schema] = {**query, **body}
    found = next(((p, kind) for p, kind in PAGINATION_PARAMS if p in params), None)
    if found is None:
        return None
    param, kind = found
    location: ParamLocation = "query" if param in query else "body"
    size_param = next((p for p in SIZE_PARAMS if p in params), None)
    default_size = DEFAULT_PAGE_SIZE
    if size_param is not None:
        size_schema = params[size_param]
        default_size = int(size_schema.get("default", DEFAULT_PAGE_SIZE))
        if "maximum" in size_schema:
            default_size = min(default_size, int(size_schema["maximum"]))
    page: Schema = params.get("page", {})
    base = int(page["minimum"] if "minimum" in page else page.get("default", 1))
    if base not in (0, 1):
        raise ValueError(f"unsupported page base {base}")
    return Pagination(kind, param, size_param, location, base, default_size)
