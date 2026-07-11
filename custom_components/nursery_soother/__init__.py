"""Nursery Soother integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryError
from homeassistant.core import valid_entity_id

from .const import DOMAIN, ENTITY_DOMAINS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Validate and set up an inert Nursery Soother config entry."""
    del hass

    for config_key, expected_domain in ENTITY_DOMAINS.items():
        entity_id = entry.data.get(config_key)
        if not isinstance(entity_id, str) or not valid_entity_id(entity_id):
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="invalid_entity",
                translation_placeholders={"config_key": config_key},
            )

        if entity_id.partition(".")[0] != expected_domain:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="invalid_entity_domain",
                translation_placeholders={
                    "config_key": config_key,
                    "expected_domain": expected_domain,
                },
            )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an inert Nursery Soother config entry."""
    del hass, entry
    return True
