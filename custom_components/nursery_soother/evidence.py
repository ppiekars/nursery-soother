"""Rolling cry evidence reconstructed from short binary-sensor pulses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """Cry evidence inside the current rolling window."""

    events: int
    active_seconds: float


class CryEvidence:
    """Track rising edges and active time without treating pulses as held state."""

    def __init__(self, window_seconds: int) -> None:
        """Initialize an empty rolling window."""
        self.window_seconds = window_seconds
        self._events: list[datetime] = []
        self._active_intervals: list[tuple[datetime, datetime]] = []
        self._active_started_at: datetime | None = None
        self._reset_at: datetime | None = None

    @property
    def active(self) -> bool:
        """Return whether the physical cry sensor is currently on."""
        return self._active_started_at is not None

    def reset(self, now: datetime, *, active: bool = False) -> None:
        """Start a fresh evidence stage, optionally during an existing pulse."""
        self._events.clear()
        self._active_intervals.clear()
        self._active_started_at = now if active else None
        self._reset_at = now

    def record_on(self, now: datetime) -> bool:
        """Record one off-to-on edge and return whether it was new."""
        if self._active_started_at is not None:
            return False
        if self._reset_at is None:
            self._reset_at = now
        self._events.append(now)
        self._active_started_at = now
        self._prune(now)
        return True

    def record_event(self, now: datetime) -> None:
        """Record one point-in-time synthetic cry event."""
        if self._reset_at is None:
            self._reset_at = now
        self._events.append(now)
        self._prune(now)

    def record_off(self, now: datetime) -> float | None:
        """Close the current physical pulse and return its duration."""
        if self._active_started_at is None:
            return None
        started_at = self._active_started_at
        self._active_started_at = None
        if now < started_at:
            return None
        self._active_intervals.append((started_at, now))
        self._prune(now)
        return (now - started_at).total_seconds()

    def snapshot(self, now: datetime) -> EvidenceSnapshot:
        """Return rising-edge count and active seconds in the rolling window."""
        self._prune(now)
        cutoff = self._cutoff(now)
        active_seconds = sum(
            max(0.0, (end - max(start, cutoff)).total_seconds())
            for start, end in self._active_intervals
            if end >= cutoff
        )
        if self._active_started_at is not None:
            active_seconds += max(
                0.0,
                (now - max(self._active_started_at, cutoff)).total_seconds(),
            )
        return EvidenceSnapshot(
            events=sum(event_at >= cutoff for event_at in self._events),
            active_seconds=active_seconds,
        )

    def seconds_until_active_threshold(
        self, now: datetime, threshold_seconds: float
    ) -> float | None:
        """Return the earliest delay at which a held pulse can meet a threshold."""
        if self._active_started_at is None:
            return None
        remaining = threshold_seconds - self.snapshot(now).active_seconds
        return max(0.0, remaining)

    def _cutoff(self, now: datetime) -> datetime:
        """Return the later of stage reset and rolling-window boundaries."""
        window_cutoff = now - timedelta(seconds=self.window_seconds)
        if self._reset_at is None:
            return window_cutoff
        return max(window_cutoff, self._reset_at)

    def _prune(self, now: datetime) -> None:
        """Discard evidence that can no longer enter the rolling window."""
        cutoff = self._cutoff(now)
        self._events = [event_at for event_at in self._events if event_at >= cutoff]
        self._active_intervals = [
            (start, end) for start, end in self._active_intervals if end >= cutoff
        ]
