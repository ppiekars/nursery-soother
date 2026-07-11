"""Binary sensor entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)

from .entity import NurserySootherEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .controller import NurserySootherController


ATTENTION_REQUIRED_DESCRIPTION = BinarySensorEntityDescription(
    key="attention_required",
    translation_key="attention_required",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Nursery Soother attention sensor."""
    del hass
    async_add_entities([NurserySootherAttentionRequiredBinarySensor(entry)])


class NurserySootherAttentionRequiredBinarySensor(
    NurserySootherEntity, BinarySensorEntity
):
    """Report when the nursery response needs parent attention."""

    entity_description = ATTENTION_REQUIRED_DESCRIPTION

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
    ) -> None:
        """Initialize the attention-required sensor."""
        super().__init__(entry, ATTENTION_REQUIRED_DESCRIPTION)

    @property
    def is_on(self) -> bool:
        """Return whether parent attention is required."""
        return self._controller.attention_required
