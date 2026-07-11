"""Number entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory

from .const import (
    CONF_BASELINE_VOLUME,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_MAX_VOLUME,
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nursery Soother level-volume controls."""
    del hass
    async_add_entities(
        NurserySootherVolumeNumber(entry, description) for description in VOLUME_NUMBERS
    )


class NurserySootherVolumeNumber(NurserySootherEntity, NumberEntity):
    """Configure one safe level or hard-cap volume."""

    entity_description: NumberEntityDescription

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
        description: NumberEntityDescription,
    ) -> None:
        """Initialize a volume control."""
        super().__init__(entry, description)

    @property
    def native_value(self) -> float:
        """Return the configured percentage."""
        return float(getattr(self._controller.settings, self.entity_description.key))

    async def async_set_native_value(self, value: float) -> None:
        """Ask the controller to validate and persist a volume percentage."""
        await self._controller.async_set_volume(self.entity_description.key, value)
