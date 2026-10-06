"""Every Trigify request goes through `Runner.call`: Cutoff check, client-side rate budget,
error classification and retries with backoff. One request is in flight at a time."""

from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto

import httpx2
from pydantic import ValidationError

from trekk_harvester.clock import Clock
from trekk_harvester.errors import (
    ApiOffError,
    ForbiddenError,
    KeyRejectedError,
    NotFoundError,
    RequestFailedError,
    RetriesExhaustedError,
)
from trekk_harvester.events import Paused, Reporter
from trekk_harvester.trigify import TrigifyApiError

# 22 Oct 2026 23:59 BST: Trigify's API stops answering.
CUTOFF = datetime(2026, 10, 22, 22, 59, tzinfo=UTC)
API_OFF_CODE = "API_OFF"


@dataclass(frozen=True, slots=True)
class RateBudget:
    """At most `requests` sent in any `window_seconds`; 60 s plus a 1 s margin for clock and
    latency skew, so Trigify never sees more than 100 in its 60 s window."""

    requests: int = 100
    window_seconds: float = 61.0


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """`backoff[n]` is the wait after failed attempt n + 1 (a 429 waits its `retry_after`)."""

    attempts: int = 6
    backoff: tuple[float, ...] = (1, 2, 4, 8, 16)


DEFAULT_BUDGET = RateBudget()
DEFAULT_POLICY = RetryPolicy()


class SlidingWindow:
    """Client-side limiter counting every request sent, retries included. `seed` takes the
    UTC epoch seconds of requests an earlier run sent, so an immediate re-run stays in budget."""

    def __init__(self, budget: RateBudget, clock: Clock) -> None:
        self._budget = budget
        self._clock = clock
        self._sent: deque[float] = deque()

    def seed(self, sent: Iterable[float]) -> None:
        """Map earlier wall-clock send times onto the monotonic clock (never into the future)."""
        wall, mono = self._clock.now().timestamp(), self._clock.monotonic()
        for epoch in sorted(sent):
            self._sent.append(mono - max(wall - epoch, 0.0))
        self._expire(mono)

    def sent(self) -> list[float]:
        """UTC epoch seconds of the requests sent within the last window."""
        wall, mono = self._clock.now().timestamp(), self._clock.monotonic()
        self._expire(mono)
        return [wall - (mono - t) for t in self._sent]

    def _expire(self, now: float) -> None:
        while self._sent and self._sent[0] <= now - self._budget.window_seconds:
            self._sent.popleft()

    async def acquire(self) -> None:
        while True:
            now = self._clock.monotonic()
            self._expire(now)
            if len(self._sent) < self._budget.requests:
                self._sent.append(now)
                return
            await self._clock.sleep(self._sent[0] + self._budget.window_seconds - now)


class _Failure(Enum):
    API_OFF = auto()
    NO_ANSWER = auto()  # transport error: Trigify did not answer
    SERVER_ERROR = auto()  # another HTTP 5xx: Trigify answered, the object failed
    RATE_LIMITED = auto()


class Runner:
    def __init__(
        self,
        clock: Clock,
        reporter: Reporter,
        *,
        budget: RateBudget = DEFAULT_BUDGET,
        policy: RetryPolicy = DEFAULT_POLICY,
        sent: Iterable[float] = (),
    ) -> None:
        """`sent`: UTC epoch seconds of requests an earlier run sent (seeds the window)."""
        self._clock = clock
        self._reporter = reporter
        self._window = SlidingWindow(budget, clock)
        self._window.seed(sent)
        self._policy = policy
        self._on_answer: Callable[[], None] | None = None

    def once_answered(self, callback: Callable[[], None]) -> None:
        """Call `callback` once, when Trigify next answers anything but API off (an error
        answer such as a 404 counts: Trigify is up)."""
        self._on_answer = callback

    def _answered(self) -> None:
        callback, self._on_answer = self._on_answer, None
        if callback is not None:
            callback()

    def sent(self) -> list[float]:
        """UTC epoch seconds of the requests sent within the last rate window."""
        return self._window.sent()

    async def call[**P, T](
        self, fn: Callable[P, Awaitable[T]], /, *args: P.args, **kwargs: P.kwargs
    ) -> T:
        operation = getattr(fn, "__name__", repr(fn))
        for attempt in range(1, self._policy.attempts + 1):
            await self._window.acquire()
            if self._clock.now() >= CUTOFF:
                raise ApiOffError("cutoff")
            retry_after: float | None = None
            try:
                answer = await fn(*args, **kwargs)
            except TrigifyApiError as error:
                if not _is_api_off(error):
                    self._answered()
                failure = _classify(operation, error)
                retry_after = error.retry_after
            except httpx2.TransportError:
                failure = _Failure.NO_ANSWER
            except ValidationError as error:
                raise RequestFailedError(operation, "unreadable answer") from error
            else:
                self._answered()
                return answer
            if attempt == self._policy.attempts:
                if self._clock.now() >= CUTOFF:
                    raise ApiOffError("cutoff")
                if failure is _Failure.API_OFF:
                    raise ApiOffError("api_off")
                raise RetriesExhaustedError(
                    operation, server_error=failure is _Failure.SERVER_ERROR
                )
            backoff = self._policy.backoff
            delay = backoff[min(attempt - 1, len(backoff) - 1)]
            if failure is _Failure.RATE_LIMITED:
                delay = delay if retry_after is None else retry_after
                self._reporter.emit(Paused(self._clock.now() + timedelta(seconds=delay)))
            await self._clock.sleep(delay)
        raise AssertionError("unreachable: the last attempt returns or raises")


def _code(error: TrigifyApiError) -> str | None:
    return error.error.error.code if error.error is not None else None


def _is_api_off(error: TrigifyApiError) -> bool:
    return error.status_code == 503 and _code(error) == API_OFF_CODE


def _classify(operation: str, error: TrigifyApiError) -> _Failure:
    code = _code(error)
    status = error.status_code
    if status == 401:
        raise KeyRejectedError(operation) from error
    if status == 403:
        raise ForbiddenError(operation, code) from error
    if status == 404:
        raise NotFoundError(operation) from error
    if _is_api_off(error):
        return _Failure.API_OFF
    if status == 429:
        return _Failure.RATE_LIMITED
    if status >= 500:
        return _Failure.SERVER_ERROR
    raise RequestFailedError(operation, f"HTTP {status} {code or ''}".strip()) from error
