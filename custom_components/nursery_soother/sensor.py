"""Sensor entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)

from .entity import NurserySootherEntity
from .models import Recommendation, SootherState

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .controller import NurserySootherController


SENSORS = (
    SensorEntityDescription(
        key="state",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=[state.value for state in SootherState],
    ),
    SensorEntityDescription(
        key="recommendation",
        translation_key="recommendation",
        device_class=SensorDeviceClass.ENUM,
        options=[recommendation.value for recommendation in Recommendation],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nursery Soother sensors."""
    del hass
    async_add_entities(
        NurserySootherSensor(entry, description) for description in SENSORS
    )


class NurserySootherSensor(NurserySootherEntity, SensorEntity):
    """Expose controller state as an enum sensor."""

    entity_description: SensorEntityDescription

    @property
    def native_value(self) -> str:
        """Return the controller value represented by this sensor."""
        if self.entity_description.key == "state":
            return self._controller.state.value
        return self._controller.recommendation.value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose structured policy context without creating polling entities."""
        if self.entity_description.key == "state":
            return self._controller.status_attributes
        suggested_level = self._controller.suggested_level
        return {
            "suggested_level": (
                suggested_level.value
                if self._controller.recommendation is Recommendation.INCREASE_LEVEL
                and suggested_level is not None
                else None
            )
        }
