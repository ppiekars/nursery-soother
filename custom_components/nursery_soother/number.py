"""Number entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime

from .const import (
    ATTENTION_MINUTES_STEP,
    CONF_ATTENTION_MINUTES,
    CONF_BASELINE_VOLUME,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_MAX_VOLUME,
    MAX_ATTENTION_MINUTES,
    MIN_ATTENTION_MINUTES,
)
from .entity import NurserySootherEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .controller import NurserySootherController


VOLUME_NUMBERS = tuple(
    NumberEntityDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.SLIDER,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    )
    for key in (
        CONF_BASELINE_VOLUME,
        CONF_LEVEL_1_VOLUME,
        CONF_LEVEL_2_VOLUME,
        CONF_LEVEL_3_VOLUME,
        CONF_LEVEL_4_VOLUME,
        CONF_MAX_VOLUME,
    )
)

ATTENTION_MINUTES_NUMBER = NumberEntityDescription(
    key=CONF_ATTENTION_MINUTES,
    translation_key=CONF_ATTENTION_MINUTES,
    entity_category=EntityCategory.CONFIG,
    device_class=NumberDeviceClass.DURATION,
    mode=NumberMode.BOX,
    native_min_value=MIN_ATTENTION_MINUTES,
    native_max_value=MAX_ATTENTION_MINUTES,
    native_step=ATTENTION_MINUTES_STEP,
    native_unit_of_measurement=UnitOfTime.MINUTES,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nursery Soother numeric configuration controls."""
    del hass
    async_add_entities(
        [
            *(NurserySootherVolumeNumber(entry, item) for item in VOLUME_NUMBERS),
            NurserySootherAttentionMinutesNumber(entry),
        ]
    )


class NurserySootherVolumeNumber(NurserySootherEntity, NumberEntity):
    """Configure one safe level or hard-cap volume."""

    entity_description: NumberEntityDescription

    @property
    def native_value(self) -> float:
        """Return the configured percentage."""
        return float(getattr(self._controller.settings, self.entity_description.key))

    async def async_set_native_value(self, value: float) -> None:
        """Ask the controller to validate and persist a volume percentage."""
        await self._controller.async_set_volume(self.entity_description.key, value)


class NurserySootherAttentionMinutesNumber(NurserySootherEntity, NumberEntity):
    """Configure the attention deadline in caregiver-friendly minutes."""

    entity_description = ATTENTION_MINUTES_NUMBER

    def __init__(self, entry: ConfigEntry[NurserySootherController]) -> None:
        """Initialize the attention deadline number."""
        super().__init__(entry, ATTENTION_MINUTES_NUMBER)

    @property
    def native_value(self) -> float:
        """Return the configured attention deadline in minutes."""
        return self._controller.settings.attention_seconds / 60

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new attention deadline without moving a live deadline."""
        await self._controller.async_set_attention_minutes(value)
