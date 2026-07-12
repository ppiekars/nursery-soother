"""Tests for rolling cry evidence reconstruction."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.nursery_soother.evidence import CryEvidence

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
TWO_EVENTS = 2
THREE_EVENTS = 3


def test_short_reolink_pulses_accumulate_events_and_active_time() -> None:
    """Repeated camera pulses form evidence without a continuously-on state."""
    evidence = CryEvidence(window_seconds=30)
    evidence.reset(NOW)

    for offset in (0, 8, 17):
        started = NOW + timedelta(seconds=offset)
        assert evidence.record_on(started)
        assert evidence.record_off(started + timedelta(seconds=3)) == pytest.approx(3)

    snapshot = evidence.snapshot(NOW + timedelta(seconds=20))
    assert snapshot.events == THREE_EVENTS
    assert snapshot.active_seconds == pytest.approx(9)


def test_window_prunes_old_events_and_clips_active_intervals() -> None:
    """Only evidence inside the rolling window can authorize a response."""
    evidence = CryEvidence(window_seconds=30)
    evidence.reset(NOW)
    evidence.record_on(NOW)
    evidence.record_off(NOW + timedelta(seconds=12))
    evidence.record_on(NOW + timedelta(seconds=35))

    snapshot = evidence.snapshot(NOW + timedelta(seconds=40))
    assert snapshot.events == 1
    assert snapshot.active_seconds == pytest.approx(7)


def test_stage_reset_requires_fresh_edges_but_counts_new_held_time() -> None:
    """One burst cannot be reused to race through multiple soothing levels."""
    evidence = CryEvidence(window_seconds=30)
    evidence.reset(NOW)
    evidence.record_on(NOW)
    evidence.record_off(NOW + timedelta(seconds=6))

    reset_at = NOW + timedelta(seconds=10)
    evidence.reset(reset_at, active=True)

    snapshot = evidence.snapshot(reset_at + timedelta(seconds=4))
    assert snapshot.events == 0
    assert snapshot.active_seconds == pytest.approx(4)
    assert evidence.seconds_until_active_threshold(
        reset_at + timedelta(seconds=4), 10
    ) == pytest.approx(6)


def test_duplicate_edges_do_not_inflate_evidence() -> None:
    """Attribute updates and duplicate off events do not count as cry events."""
    evidence = CryEvidence(window_seconds=30)
    evidence.reset(NOW)

    assert evidence.record_on(NOW)
    assert not evidence.record_on(NOW + timedelta(seconds=1))
    assert evidence.record_off(NOW + timedelta(seconds=3)) == pytest.approx(3)
    assert evidence.record_off(NOW + timedelta(seconds=4)) is None
    evidence.record_event(NOW + timedelta(seconds=5))

    snapshot = evidence.snapshot(NOW + timedelta(seconds=5))
    assert snapshot.events == TWO_EVENTS
    assert snapshot.active_seconds == pytest.approx(3)


def test_clock_skew_clamps_pulse_to_zero_duration() -> None:
    """A backward off timestamp still closes and records the active pulse."""
    evidence = CryEvidence(window_seconds=30)
    evidence.reset(NOW)
    started_at = NOW + timedelta(seconds=5)

    assert evidence.record_on(started_at)
    assert evidence.record_off(NOW) == pytest.approx(0)
    assert not evidence.active

    snapshot = evidence.snapshot(started_at)
    assert snapshot.events == 1
    assert snapshot.active_seconds == pytest.approx(0)
