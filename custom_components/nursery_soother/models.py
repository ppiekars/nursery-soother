"""Typed models for Nursery Soother."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .const import (
    CONF_BASELINE_VOLUME,
    CONF_BOOST_VOLUME,
    CONF_COOLDOWN_SECONDS,
    CONF_DEBOUNCE_SECONDS,
    CONF_ENABLED,
    CONF_ESCALATION_SECONDS,
    CONF_MAX_VOLUME,
    CONF_SETTLING_SECONDS,
    DEFAULT_OPTIONS,
    MAX_VOLUME_PERCENT,
)


class SootherState(StrEnum):
    """States owned by the nursery response policy."""

    DISABLED = "disabled"
    BASELINE = "baseline"
    CRY_PENDING = "cry_pending"
    BOOST = "boost"
    ATTENTION_REQUIRED = "attention_required"
    SETTLING = "settling"


class Recommendation(StrEnum):
    """Parent-facing recommendation from the controller."""

    NONE = "none"
    ENABLE = "enable"
    CONFIGURE = "configure"
    WAIT = "wait"
    BOOST = "boost"
    OBSERVE = "observe"
    ATTEND = "attend"
    ACKNOWLEDGED = "acknowledged"
    SETTLING = "settling"
    CHECK_DEVICES = "check_devices"
    COOLDOWN = "cooldown"


@dataclass(slots=True)
class SootherSettings:
    """Mutable, persisted behavior settings."""

    baseline_volume: float
    boost_volume: float
    max_volume: float
    debounce_seconds: int
    cooldown_seconds: int
    settling_seconds: int
    escalation_seconds: int
    enabled: bool

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> SootherSettings:
        """Build settings from config-entry options with safe defaults."""
        values = DEFAULT_OPTIONS | options
        return cls(
            baseline_volume=float(values[CONF_BASELINE_VOLUME]),
            boost_volume=float(values[CONF_BOOST_VOLUME]),
            max_volume=float(values[CONF_MAX_VOLUME]),
            debounce_seconds=int(values[CONF_DEBOUNCE_SECONDS]),
            cooldown_seconds=int(values[CONF_COOLDOWN_SECONDS]),
            settling_seconds=int(values[CONF_SETTLING_SECONDS]),
            escalation_seconds=int(values[CONF_ESCALATION_SECONDS]),
            enabled=bool(values[CONF_ENABLED]),
        )

    def as_options(self) -> dict[str, float | int | bool]:
        """Return settings in config-entry storage form."""
        return {
            CONF_BASELINE_VOLUME: self.baseline_volume,
            CONF_BOOST_VOLUME: self.boost_volume,
            CONF_MAX_VOLUME: self.max_volume,
            CONF_DEBOUNCE_SECONDS: self.debounce_seconds,
            CONF_COOLDOWN_SECONDS: self.cooldown_seconds,
            CONF_SETTLING_SECONDS: self.settling_seconds,
            CONF_ESCALATION_SECONDS: self.escalation_seconds,
            CONF_ENABLED: self.enabled,
        }

    def volumes_are_valid(self) -> bool:
        """Return whether volume relationships are safe and coherent."""
        return (
            0.0
            <= self.baseline_volume
            <= self.boost_volume
            <= self.max_volume
            <= MAX_VOLUME_PERCENT
        )
