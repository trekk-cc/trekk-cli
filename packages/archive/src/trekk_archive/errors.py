"""Archive errors: every message names its subject (cap, version, kind + field), never a value."""

import errno


class ArchiveError(Exception):
    """Base of every Archive writer and reader error."""


class CredentialValueError(ArchiveError):
    """The writer met a credential-shaped identity field (`trigify_id` or a `*_id` field): ids
    are never redacted, since that would break references. The value is never repeated
    (`trigify_id` is the redaction marker when it is the credential)."""

    def __init__(self, kind: str, trigify_id: str, path: str) -> None:
        super().__init__(
            f"{kind} {trigify_id}: credential-shaped value at {path} must not be archived"
        )
        self.kind, self.trigify_id, self.path = kind, trigify_id, path


class CorruptArchiveError(ArchiveError):
    """Not a ZIP, or its content does not match spec v1."""


class UnsafeArchiveError(ArchiveError):
    """An entry path or size breaks a reader safety rule; `reason` names the rule or cap."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"unsafe archive: {reason} ({detail})")
        self.reason = reason


class MissingManifestError(ArchiveError):
    """The Archive has no manifest.json."""

    def __init__(self) -> None:
        super().__init__("archive has no manifest.json")


class UnsupportedSpecVersionError(ArchiveError):
    """The manifest declares a spec version whose major this reader does not support."""

    def __init__(self, declared: object) -> None:
        super().__init__(f"unsupported archive spec_version {declared!r}: this reader reads 1.x")
        self.declared = declared


class ArchivePublishError(ArchiveError, OSError):
    """The output filesystem cannot hard-link files, which publishing an Archive without ever
    overwriting an existing file needs. `strerror` names the limitation."""

    def __init__(self, directory: object) -> None:
        super().__init__(
            errno.ENOTSUP,
            "the filesystem cannot hard-link files, which writing an Archive without"
            " overwriting needs; choose an output directory on another filesystem",
        )
        self.directory = directory
