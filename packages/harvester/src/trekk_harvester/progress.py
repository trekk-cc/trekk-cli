"""Progress per object type and phase, throttled to one event per 5 s plus the phase end."""

from datetime import datetime, timedelta

from trekk_harvester.clock import Clock
from trekk_harvester.events import ObjectType, Phase, Progress, Reporter

THROTTLE_SECONDS = 5.0


class ProgressTracker:
    """`start` is what was already done before this run (resumed work): the ETA uses only the
    rate of this run."""

    def __init__(
        self,
        clock: Clock,
        reporter: Reporter,
        object_type: ObjectType,
        phase: Phase,
        *,
        start: int,
        total: int | None = None,
    ) -> None:
        self._clock = clock
        self._reporter = reporter
        self._object_type = object_type
        self._phase = phase
        self._start = start
        self._total = total
        self._began = clock.monotonic()
        self._last: float | None = None
        self._shown: int | None = None

    def update(self, completed: int, *, final: bool = False) -> None:
        now = self._clock.monotonic()
        if completed == self._shown:
            return
        if not final and self._last is not None and now - self._last < THROTTLE_SECONDS:
            return
        self._last, self._shown = now, completed
        self._reporter.emit(
            Progress(self._object_type, self._phase, completed, self._total, self._eta(completed))
        )

    def _eta(self, completed: int) -> datetime | None:
        done = completed - self._start
        if self._total is None or done <= 0 or completed >= self._total:
            return None
        seconds = (self._clock.monotonic() - self._began) * (self._total - completed) / done
        return self._clock.now() + timedelta(seconds=seconds)
