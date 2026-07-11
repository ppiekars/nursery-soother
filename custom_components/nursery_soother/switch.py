"""Switch entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory

from .entity import NurserySootherEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .controller import NurserySootherController


ENABLED_DESCRIPTION = SwitchEntityDescription(
    key="enabled",
    translation_key="enabled",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Nursery Soother enabled switch."""
    del hass
    async_add_entities([NurserySootherEnabledSwitch(entry)])


class NurserySootherEnabledSwitch(NurserySootherEntity, SwitchEntity):
    """Enable or disable the response controller."""

    entity_description = ENABLED_DESCRIPTION

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
    ) -> None:
        """Initialize the enabled switch."""
        super().__init__(entry, ENABLED_DESCRIPTION)

    @property
    def is_on(self) -> bool:
        """Return whether the controller is enabled."""
        return self._controller.enabled

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable the controller."""
        del kwargs
        await self._controller.async_set_enabled(enabled=True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable the controller."""
        del kwargs
        await self._controller.async_set_enabled(enabled=False)
