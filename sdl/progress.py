"""Progress reporting.

The downloader counts segments (which is what gives a usable ETA) while the user
wants to see megabytes per second, so a task tracks both: segments as the bar's
own progress and recent bytes for the speed column.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from threading import Lock
from time import monotonic

from rich.console import Console
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, ProgressColumn, SpinnerColumn,
    TextColumn, TimeElapsedColumn, TimeRemainingColumn,
)
from rich.text import Text

UNITS = ("B", "KB", "MB", "GB", "TB")
SPEED_WINDOW_SECONDS = 2.0


def format_bytes(value: float) -> str:
    size = float(value)
    for unit in UNITS:
        if size < 1024 or unit == UNITS[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} {UNITS[-1]}"


class ByteSpeedColumn(ProgressColumn):
    """Recent transfer rate, falling to zero when no bytes arrive."""

    def render(self, task) -> Text:
        speed = task.fields.get("byte_speed")
        updated = task.fields.get("speed_updated")
        if speed is None or updated is None:
            return Text("", style="progress.data.speed")
        if monotonic() - updated >= SPEED_WINDOW_SECONDS:
            speed = 0
        return Text(f"{format_bytes(speed)}/s", style="progress.data.speed")


class StatusColumn(ProgressColumn):
    """Optional detail about the work currently happening inside a task."""

    def render(self, task) -> Text:
        return Text(task.fields.get("status", ""), style="dim")


class Task:
    """Handle on one line of the progress display."""

    def __init__(self, progress: Progress, task_id):
        self._progress = progress
        self._id = task_id
        self._bytes = 0
        self._samples: deque[tuple[float, int]] = deque()
        self._lock = Lock()

    def set_total(self, total: int) -> None:
        self._progress.update(self._id, total=total)

    def set_status(self, status: str) -> None:
        self._progress.update(self._id, status=status)

    def advance(self, count: int = 1, nbytes: int = 0) -> None:
        # Segment workers call this concurrently, so byte samples are protected
        # before Rich receives its own thread-safe update.
        with self._lock:
            now = monotonic()
            self._bytes += nbytes
            if self._samples and now - self._samples[-1][0] >= SPEED_WINDOW_SECONDS:
                self._samples.clear()
            self._samples.append((now, self._bytes))

            cutoff = now - SPEED_WINDOW_SECONDS
            while len(self._samples) > 1 and self._samples[1][0] <= cutoff:
                self._samples.popleft()

            started, initial_bytes = self._samples[0]
            elapsed = now - started
            speed = (self._bytes - initial_bytes) / elapsed if elapsed else 0
            self._progress.update(
                self._id, advance=count, byte_speed=speed, speed_updated=now
            )


class Reporter:
    """Wraps a rich Progress so the download code never imports rich itself."""

    def __init__(self, console: Console):
        self.console = console
        self._progress: Progress | None = None

    @contextmanager
    def live(self):
        progress = Progress(
            SpinnerColumn(style="red"),
            TextColumn("[bold]{task.description}"),
            StatusColumn(),
            BarColumn(bar_width=32, complete_style="red", finished_style="green"),
            MofNCompleteColumn(),
            ByteSpeedColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(compact=True),
            console=self.console,
            transient=True,
        )
        self._progress = progress
        try:
            with progress:
                yield self
        finally:
            self._progress = None

    @contextmanager
    def task(self, label: str, total: int | None = None):
        if self._progress is None:
            raise RuntimeError("Reporter.task() used outside Reporter.live()")
        task_id = self._progress.add_task(label, total=total, status="")
        task = Task(self._progress, task_id)
        try:
            yield task
        finally:
            self._progress.remove_task(task_id)

    def note(self, message: str) -> None:
        """A line printed above the bars, for things worth keeping on screen."""
        self.console.print(message)
