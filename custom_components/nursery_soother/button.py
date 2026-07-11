"""Button entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory

from .const import SERVICE_SIMULATE_CRY_EVENT
from .entity import NurserySootherEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .controller import NurserySootherController


SIMULATE_DESCRIPTION = ButtonEntityDescription(
    key=SERVICE_SIMULATE_CRY_EVENT,
    translation_key="simulate_cry_event",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the finite diagnostic cry-event button."""
    del hass
    async_add_entities([NurserySootherSimulateCryButton(entry)])


class NurserySootherSimulateCryButton(NurserySootherEntity, ButtonEntity):
    """Inject one finite event through the normal cry-evidence path."""

    entity_description = SIMULATE_DESCRIPTION

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
    ) -> None:
        """Initialize the diagnostic button."""
        super().__init__(entry, SIMULATE_DESCRIPTION)

    async def async_press(self) -> None:
        """Inject one synthetic cry event."""
        await self._controller.async_simulate_cry_event()
