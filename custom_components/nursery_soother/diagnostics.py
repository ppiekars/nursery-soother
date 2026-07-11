"""Privacy-safe diagnostics for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import (
    CONF_CAMERA,
    CONF_CRY_SENSOR,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_SOUNDS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import NurserySootherConfigEntry

TO_REDACT = {
    CONF_CRY_SENSOR,
    CONF_CAMERA,
    CONF_MEDIA_PLAYER,
    CONF_SOUNDS,
    CONF_NOTIFY_TARGETS,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NurserySootherConfigEntry
) -> dict[str, Any]:
    """Return support data without devices, media, targets, or action tokens."""
    del hass
    return {
        "entry_version": entry.version,
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "runtime": entry.runtime_data.diagnostics,
    }
