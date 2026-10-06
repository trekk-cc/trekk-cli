"""The one secrets registry: key names whose values the writer redacts and the reader drops,
and the credential shapes the writer redacts in any key or string value. Both are published in
the object schema as `x-trekk-secret-fields` / `x-trekk-credential-patterns`."""

import re
from typing import Any

# Compared against keys lowercased with `_` and `-` removed.
SECRET_FIELDS: tuple[str, ...] = (
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "apikey",
    "xapikey",
    "clientsecret",
    "password",
    "secret",
    "privatekey",
    "authorization",
    "bearertoken",
    "oauthtoken",
    "apitoken",
    "authtoken",
    "sessiontoken",
    "webhooksecret",
    "signingsecret",
    "cookie",
)

# Each shape must start at a token boundary, so ordinary prose ("risk-assessment-...") passes.
_BOUNDARY = r"(?<![A-Za-z0-9])"
CREDENTIAL_PATTERNS: tuple[str, ...] = tuple(
    _BOUNDARY + shape
    for shape in (
        r"trig_[A-Za-z0-9_-]{16,}",
        r"ya29\.[\w-]{20,}",
        r"xox[abpsr]-[\w-]{10,}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"sk-[A-Za-z0-9_-]{20,}",
        r"sk_live_[A-Za-z0-9]{16,}",
        r"sk_test_[A-Za-z0-9]{16,}",
        r"rk_live_[A-Za-z0-9]{16,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"pat-(?:na|eu)1-[0-9a-f-]{36}",
        r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}",
        r"(?i:bearer)\s+[\w.~+/-]{20,}",
    )
)

REDACTED = "[redacted: credential]"  # what the writer puts in place of each credential

_SECRET_FIELDS = frozenset(SECRET_FIELDS)
_CREDENTIAL = re.compile("|".join(f"(?:{p})" for p in CREDENTIAL_PATTERNS))


def is_secret_field(key: str) -> bool:
    return key.lower().replace("_", "").replace("-", "") in _SECRET_FIELDS


def is_credential(value: str) -> bool:
    return _CREDENTIAL.search(value) is not None


def _join(path: str, key: str | int) -> str:
    return f"{path}.{key}" if path else str(key)


def redact(value: Any, path: str = "") -> tuple[Any, list[str]]:
    """`value` with every credential replaced by `REDACTED`, and the sorted, distinct paths of
    the replacements: a secret-tagged key's non-null value, and the non-null `value` of a dict
    whose `name` or `key` is a secret-tagged name (a scalar whole; a dict or list keeps its
    shape and each non-null scalar leaf inside it is replaced, one path per leaf), a
    credential-shaped string (whole), a credential-shaped key (renamed `REDACTED`,
    `REDACTED 2`, ... in dict order within its dict; its value is still scanned). A value equal
    to `REDACTED` or a key starting with it is recorded as well, so redacting a redacted value
    gives the same value and paths."""
    paths: list[str] = []
    return _redact(value, path, paths), sorted(set(paths))


def _redact(value: Any, path: str, paths: list[str]) -> Any:
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        secret_pair = _is_secret_pair(value)
        for key, item in value.items():
            name = _name(key, kept, value, path, paths)
            if is_secret_field(key) or (secret_pair and key == "value"):
                kept[name] = _hide(item, _join(path, name), paths)
            else:
                kept[name] = _redact(item, _join(path, name), paths)
        return kept
    if isinstance(value, list):
        return [_redact(item, _join(path, i), paths) for i, item in enumerate(value)]
    if isinstance(value, str) and (value == REDACTED or is_credential(value)):
        paths.append(path)
        return REDACTED
    return value


def _hide(value: Any, path: str, paths: list[str]) -> Any:
    """A secret value: null stays null, a scalar becomes `REDACTED`, a dict or list keeps its
    shape with every non-null scalar leaf `REDACTED` (credential-shaped keys still renamed)."""
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        for key, item in value.items():
            name = _name(key, kept, value, path, paths)
            kept[name] = _hide(item, _join(path, name), paths)
        return kept
    if isinstance(value, list):
        return [_hide(item, _join(path, i), paths) for i, item in enumerate(value)]
    if value is None:
        return None
    paths.append(path)
    return REDACTED


def _name(
    key: str, kept: dict[str, Any], original: dict[str, Any], path: str, paths: list[str]
) -> str:
    """The key as written: a credential-shaped key renamed to a free marker name; a key that
    is (now) a marker name is recorded."""
    name = _free_name(kept, original) if is_credential(key) else key
    if name.startswith(REDACTED):
        paths.append(_join(path, name))
    return name


def _is_secret_pair(value: dict[str, Any]) -> bool:
    """A name/value pair (e.g. `{"name": "x-api-key", "value": ...}`) naming a secret field."""
    return any(
        isinstance(value.get(label), str) and is_secret_field(value[label])
        for label in ("name", "key")
    )


def _free_name(kept: dict[str, Any], original: dict[str, Any]) -> str:
    """The first of `REDACTED`, `REDACTED 2`, ... that is neither a key kept so far nor one of
    the dict's own keys."""
    name, number = REDACTED, 1
    while name in kept or name in original:
        number += 1
        name = f"{REDACTED} {number}"
    return name


def drop_secret_fields(value: Any, dropped: list[str], path: str = "") -> Any:
    """Return `value` without any secret-tagged key at any depth whose value is not what the
    writer emits (null, `REDACTED`, or a dict or list whose every leaf is `REDACTED` or null);
    append each dropped path."""
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            if is_secret_field(key) and not _redacted(item):
                dropped.append(_join(path, key))
            else:
                kept[key] = drop_secret_fields(item, dropped, _join(path, key))
        return kept
    if isinstance(value, list):
        return [drop_secret_fields(item, dropped, _join(path, i)) for i, item in enumerate(value)]
    return value


def _redacted(value: Any) -> bool:
    """Whether every leaf of `value` is `REDACTED` or null."""
    if isinstance(value, dict):
        return all(_redacted(item) for item in value.values())
    if isinstance(value, list):
        return all(_redacted(item) for item in value)
    return value is None or value == REDACTED
