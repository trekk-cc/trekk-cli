"""The one error a Trigify call raises for a non-2xx answer."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trekk_harvester.trigify._generated import ErrorResponse


class TrigifyApiError(Exception):
    """Trigify answered non-2xx. `error` is the parsed `ErrorResponse` envelope, if it was one;
    `retry_after` comes from `Retry-After` or `error.retryAfterSeconds`, in seconds."""

    def __init__(
        self, status_code: int, error: ErrorResponse | None, retry_after: float | None
    ) -> None:
        code = error.error.code if error is not None else None
        super().__init__(f"Trigify answered {status_code} ({code})")
        self.status_code = status_code
        self.error = error
        self.retry_after = retry_after
