"""Switch entities for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription

from .const import CONF_AUTOMATIC_OPERATION, CONF_BASELINE_PREVIEW, CONF_LEVEL_LOCK
from .entity import NurserySootherEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .controller import NurserySootherController


AUTOMATIC_DESCRIPTION = SwitchEntityDescription(
    key=CONF_AUTOMATIC_OPERATION,
    translation_key="automatic_operation",
)
LEVEL_LOCK_DESCRIPTION = SwitchEntityDescription(
    key=CONF_LEVEL_LOCK,
    translation_key="level_lock",
)
BASELINE_PREVIEW_DESCRIPTION = SwitchEntityDescription(
    key=CONF_BASELINE_PREVIEW,
    translation_key="baseline_preview",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NurserySootherController],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Nursery Soother operating-mode controls."""
    del hass
    async_add_entities(
        [
            NurserySootherAutomaticSwitch(entry),
            NurserySootherLevelLockSwitch(entry),
            NurserySootherBaselinePreviewSwitch(entry),
        ]
    )


class NurserySootherAutomaticSwitch(NurserySootherEntity, SwitchEntity):
    """Choose automatic level changes instead of manual suggestions."""

    entity_description = AUTOMATIC_DESCRIPTION

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
    ) -> None:
        """Initialize the automatic-operation switch."""
        super().__init__(entry, AUTOMATIC_DESCRIPTION)

    @property
    def is_on(self) -> bool:
        """Return whether automatic level changes are authorized."""
        return self._controller.automatic

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable automatic operation."""
        del kwargs
        await self._controller.async_set_automatic(enabled=True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Use manual suggestions without automatic level changes."""
        del kwargs
        await self._controller.async_set_automatic(enabled=False)


class NurserySootherLevelLockSwitch(NurserySootherEntity, SwitchEntity):
    """Freeze policy-driven level changes while preserving parent control."""

    entity_description = LEVEL_LOCK_DESCRIPTION

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
    ) -> None:
        """Initialize the level-lock switch."""
        super().__init__(entry, LEVEL_LOCK_DESCRIPTION)

    @property
    def is_on(self) -> bool:
        """Return whether policy-driven level changes are frozen."""
        return self._controller.locked

    async def async_turn_on(self, **kwargs: object) -> None:
        """Freeze the current output level."""
        del kwargs
        await self._controller.async_set_locked(locked=True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Allow the response policy to change levels again."""
        del kwargs
        await self._controller.async_set_locked(locked=False)


class NurserySootherBaselinePreviewSwitch(NurserySootherEntity, SwitchEntity):
    """Play Baseline independently while the soothing system remains in Standby."""

    entity_description = BASELINE_PREVIEW_DESCRIPTION

    def __init__(
        self,
        entry: ConfigEntry[NurserySootherController],
    ) -> None:
        """Initialize the independent Baseline playback switch."""
        super().__init__(entry, BASELINE_PREVIEW_DESCRIPTION)

    @property
    def is_on(self) -> bool:
        """Return whether independent Baseline playback is active."""
        return self._controller.baseline_previewing

    async def async_turn_on(self, **kwargs: object) -> None:
        """Start Baseline playback without starting a soothing session."""
        del kwargs
        await self._controller.async_set_baseline_preview(enabled=True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Stop independent Baseline playback."""
        del kwargs
        await self._controller.async_set_baseline_preview(enabled=False)
