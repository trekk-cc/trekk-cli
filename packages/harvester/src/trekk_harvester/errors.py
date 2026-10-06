"""Why a harvest stopped. Every error carries the facts the CLI needs, never the API key."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    name: str


class HarvestError(Exception):
    """Base of every harvest error."""


class KeyRejectedError(HarvestError):
    """Trigify answered 401: the key is unknown or revoked."""


class NoSubscriptionError(HarvestError):
    """Trigify answered 403 to the workspace lookup: the workspace has no API subscription."""


class WorkspaceMismatchError(HarvestError):
    def __init__(self, checkpoint: Workspace, found: Workspace) -> None:
        super().__init__(f"checkpoint workspace {checkpoint.id}, key workspace {found.id}")
        self.checkpoint, self.found = checkpoint, found


class UnconfirmedKeyError(HarvestError):
    """Trigify did not answer (API off or the Cutoff passed) and the checkpoint in `directory`
    (of `workspace`) never recorded this key as confirmed for it: it is left as it is."""

    def __init__(self, directory: Path, workspace: Workspace) -> None:
        super().__init__(f"checkpoint {directory} of workspace {workspace.id}: key not confirmed")
        self.directory, self.workspace = directory, workspace


class StorageError(HarvestError):
    """Writing under the output directory failed (read-only, disk full, ...)."""

    def __init__(self, directory: Path, reason: str) -> None:
        super().__init__(f"cannot write {directory}: {reason}")
        self.directory, self.reason = directory, reason


class CheckpointFormatError(HarvestError):
    """The checkpoint in `directory` was written in another format (another Harvester
    version); it is left as it is."""

    def __init__(self, directory: Path) -> None:
        super().__init__(f"checkpoint {directory}: unknown format")
        self.directory = directory


class SealedArchiveMissingError(HarvestError):
    """The checkpoint was sealed with the Archive at `path`, which is no longer there."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"sealed Archive {path} is missing")
        self.path = path


type ApiOffReason = Literal["api_off", "cutoff"]


class ApiOffError(HarvestError):
    """Lossless harvest is no longer possible: the API is off or the Cutoff has passed."""

    def __init__(self, reason: ApiOffReason) -> None:
        super().__init__(reason)
        self.reason: ApiOffReason = reason


class RetriesExhaustedError(HarvestError):
    """A request kept failing transiently; the checkpoint lets the next run resume.
    `server_error`: the last failure was an HTTP 5xx answer (Trigify is up, the object is not)."""

    def __init__(self, operation: str, *, server_error: bool) -> None:
        super().__init__(f"{operation}: retries exhausted")
        self.operation, self.server_error = operation, server_error


class RequestFailedError(HarvestError):
    """Trigify answered something a retry cannot fix (another 4xx, an unreadable body)."""

    def __init__(self, operation: str, detail: str) -> None:
        super().__init__(f"{operation}: {detail}")
        self.operation, self.detail = operation, detail


class NotFoundError(RequestFailedError):
    """Trigify answered 404: the object is gone (for a top-level request, the operation)."""

    def __init__(self, operation: str) -> None:
        super().__init__(operation, "HTTP 404 NOT_FOUND")


class ForbiddenError(RequestFailedError):
    """Trigify answered 403 (`code`: its error code, if any). For the workspace lookup it means
    no API subscription; for a feature's first request, a feature the workspace never used."""

    def __init__(self, operation: str, code: str | None) -> None:
        super().__init__(operation, f"HTTP 403 {code or ''}".strip())
        self.code = code


class ArchiveRefusedError(HarvestError):
    """The Archive writer refused an object holding a credential; the value is never kept."""

    def __init__(self, kind: str, trigify_id: str, field: str) -> None:
        super().__init__(f"{kind} {trigify_id}: credential in {field}")
        self.kind, self.trigify_id, self.field = kind, trigify_id, field
