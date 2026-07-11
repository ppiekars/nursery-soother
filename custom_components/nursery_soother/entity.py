"""Shared entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, NAME

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .controller import NurserySootherController


class NurserySootherEntity(Entity):
    """Base for entities backed by one nursery controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
        description: EntityDescription,
    ) -> None:
        """Initialize a controller-backed entity."""
        self.entity_description = description
        self._controller = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer=NAME,
            model=NAME,
            name=entry.title,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._controller.async_add_listener(self.async_write_ha_state)
        )
