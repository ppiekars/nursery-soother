"""Select entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity, SelectEntityDescription

from .const import CONF_LEVEL
from .entity import NurserySootherEntity
from .models import SoothingLevel

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .controller import NurserySootherController


LEVEL_DESCRIPTION = SelectEntityDescription(
    key=CONF_LEVEL,
    translation_key="level",
    options=[level.value for level in SoothingLevel],
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Nursery Soother level control."""
    del hass
    async_add_entities([NurserySootherLevelSelect(entry)])


class NurserySootherLevelSelect(NurserySootherEntity, SelectEntity):
    """Select the active soothing output, with Standby as the off state."""

    entity_description = LEVEL_DESCRIPTION

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
    ) -> None:
        """Initialize the level control."""
        super().__init__(entry, LEVEL_DESCRIPTION)

    @property
    def current_option(self) -> str:
        """Return the controller's current output level."""
        return self._controller.level.value

    async def async_select_option(self, option: str) -> None:
        """Apply one exact parent-selected output level."""
        await self._controller.async_set_level(SoothingLevel(option))
