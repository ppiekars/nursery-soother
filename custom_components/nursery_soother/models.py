"""Typed models for Nursery Soother."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from .const import (
    CONF_ATTENTION_SECONDS,
    CONF_AUTOMATIC_OPERATION,
    CONF_BASELINE_VOLUME,
    CONF_CRY_GAP_SECONDS,
    CONF_DEBOUNCE_SECONDS,
    CONF_EVIDENCE_WINDOW_SECONDS,
    CONF_LEVEL,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_LEVEL_UP_SECONDS,
    CONF_MAX_VOLUME,
    CONF_SETTLING_SECONDS,
    DEFAULT_OPTIONS,
    MAX_VOLUME_PERCENT,
)


class SoothingLevel(StrEnum):
    """Ordered output levels controlled by the parent or response policy."""

    STANDBY = "standby"
    BASELINE = "baseline"
    LEVEL_1 = "level_1"
    LEVEL_2 = "level_2"
    LEVEL_3 = "level_3"
    LEVEL_4 = "level_4"

    def next_active(self) -> SoothingLevel | None:
        """Return the next active level without stepping above Level 4."""
        if self is SoothingLevel.STANDBY:
            return SoothingLevel.BASELINE
        index = ACTIVE_LEVELS.index(self)
        return ACTIVE_LEVELS[index + 1] if index + 1 < len(ACTIVE_LEVELS) else None

    def previous_active(self) -> SoothingLevel | None:
        """Return the previous active level without stepping into Standby."""
        if self is SoothingLevel.STANDBY:
            return None
        index = ACTIVE_LEVELS.index(self)
        return ACTIVE_LEVELS[index - 1] if index > 0 else None


ACTIVE_LEVELS: Final[tuple[SoothingLevel, ...]] = (
    SoothingLevel.BASELINE,
    SoothingLevel.LEVEL_1,
    SoothingLevel.LEVEL_2,
    SoothingLevel.LEVEL_3,
    SoothingLevel.LEVEL_4,
)


class SootherState(StrEnum):
    """Parent-facing phases of the nursery response policy."""

    STANDBY = "standby"
    SOOTHING = "soothing"
    CRY_PENDING = "cry_pending"
    RESPONDING = "responding"
    SETTLING = "settling"
    ATTENTION_REQUIRED = "attention_required"


class Recommendation(StrEnum):
    """Parent-facing recommendation from the controller."""

    NONE = "none"
    START = "start"
    WAIT = "wait"
    INCREASE_LEVEL = "increase_level"
    OBSERVE = "observe"
    ATTEND = "attend"
    SETTLING = "settling"
    CHECK_DEVICES = "check_devices"


LEVEL_VOLUME_KEYS: Final[dict[SoothingLevel, str]] = {
    SoothingLevel.BASELINE: CONF_BASELINE_VOLUME,
    SoothingLevel.LEVEL_1: CONF_LEVEL_1_VOLUME,
    SoothingLevel.LEVEL_2: CONF_LEVEL_2_VOLUME,
    SoothingLevel.LEVEL_3: CONF_LEVEL_3_VOLUME,
    SoothingLevel.LEVEL_4: CONF_LEVEL_4_VOLUME,
}


@dataclass(slots=True)
class SootherSettings:
    """Mutable, persisted behavior settings."""

    level: SoothingLevel
    automatic_operation: bool
    baseline_volume: float
    level_1_volume: float
    level_2_volume: float
    level_3_volume: float
    level_4_volume: float
    max_volume: float
    debounce_seconds: int
    evidence_window_seconds: int
    cry_gap_seconds: int
    level_up_seconds: int
    settling_seconds: int
    attention_seconds: int

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> SootherSettings:
        """Build settings from config-entry options with safe defaults."""
        values: dict[str, Any] = dict(DEFAULT_OPTIONS)
        values.update(options)
        return cls(
            level=SoothingLevel(values[CONF_LEVEL]),
            automatic_operation=bool(values[CONF_AUTOMATIC_OPERATION]),
            baseline_volume=float(values[CONF_BASELINE_VOLUME]),
            level_1_volume=float(values[CONF_LEVEL_1_VOLUME]),
            level_2_volume=float(values[CONF_LEVEL_2_VOLUME]),
            level_3_volume=float(values[CONF_LEVEL_3_VOLUME]),
            level_4_volume=float(values[CONF_LEVEL_4_VOLUME]),
            max_volume=float(values[CONF_MAX_VOLUME]),
            debounce_seconds=int(values[CONF_DEBOUNCE_SECONDS]),
            evidence_window_seconds=int(values[CONF_EVIDENCE_WINDOW_SECONDS]),
            cry_gap_seconds=int(values[CONF_CRY_GAP_SECONDS]),
            level_up_seconds=int(values[CONF_LEVEL_UP_SECONDS]),
            settling_seconds=int(values[CONF_SETTLING_SECONDS]),
            attention_seconds=int(values[CONF_ATTENTION_SECONDS]),
        )

    def as_options(self) -> dict[str, float | int | bool | str]:
        """Return settings in config-entry storage form."""
        return {
            CONF_LEVEL: self.level.value,
            CONF_AUTOMATIC_OPERATION: self.automatic_operation,
            CONF_BASELINE_VOLUME: self.baseline_volume,
            CONF_LEVEL_1_VOLUME: self.level_1_volume,
            CONF_LEVEL_2_VOLUME: self.level_2_volume,
            CONF_LEVEL_3_VOLUME: self.level_3_volume,
            CONF_LEVEL_4_VOLUME: self.level_4_volume,
            CONF_MAX_VOLUME: self.max_volume,
            CONF_DEBOUNCE_SECONDS: self.debounce_seconds,
            CONF_EVIDENCE_WINDOW_SECONDS: self.evidence_window_seconds,
            CONF_CRY_GAP_SECONDS: self.cry_gap_seconds,
            CONF_LEVEL_UP_SECONDS: self.level_up_seconds,
            CONF_SETTLING_SECONDS: self.settling_seconds,
            CONF_ATTENTION_SECONDS: self.attention_seconds,
        }

    def volume_for_level(self, level: SoothingLevel) -> float:
        """Return the configured volume for one active level."""
        key = LEVEL_VOLUME_KEYS.get(level)
        if key is None:
            raise ValueError
        return float(getattr(self, key))

    def volumes_are_valid(self) -> bool:
        """Return whether every output level is monotonic and safely capped."""
        return (
            0.0
            <= self.baseline_volume
            <= self.level_1_volume
            <= self.level_2_volume
            <= self.level_3_volume
            <= self.level_4_volume
            <= self.max_volume
            <= MAX_VOLUME_PERCENT
        )
