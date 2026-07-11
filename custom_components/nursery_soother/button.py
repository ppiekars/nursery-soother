"""Button entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription

from .const import (
    SERVICE_ACKNOWLEDGE,
    SERVICE_BASELINE,
    SERVICE_BOOST,
    SERVICE_STOP,
)
from .entity import NurserySootherEntity

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .controller import NurserySootherController


BUTTONS = (
    ButtonEntityDescription(key=SERVICE_BOOST, translation_key="boost"),
    ButtonEntityDescription(key=SERVICE_BASELINE, translation_key="baseline"),
    ButtonEntityDescription(
        key=SERVICE_ACKNOWLEDGE,
        translation_key="acknowledge",
    ),
    ButtonEntityDescription(key=SERVICE_STOP, translation_key="stop"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nursery Soother action buttons."""
    del hass
    async_add_entities(
        NurserySootherActionButton(entry, description) for description in BUTTONS
    )


class NurserySootherActionButton(NurserySootherEntity, ButtonEntity):
    """Run one explicit parent action."""

    entity_description: ButtonEntityDescription

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
        description: ButtonEntityDescription,
    ) -> None:
        """Initialize an action button."""
        super().__init__(entry, description)

    async def async_press(self) -> None:
        """Run the action represented by this button."""
        actions: dict[str, Callable[[], Awaitable[object]]] = {
            SERVICE_BOOST: self._controller.async_boost,
            SERVICE_BASELINE: self._controller.async_baseline,
            SERVICE_ACKNOWLEDGE: self._controller.async_acknowledge,
            SERVICE_STOP: self._controller.async_stop,
        }
        await actions[self.entity_description.key]()
