"""Nursery Soother integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryError
from homeassistant.core import valid_entity_id

from .const import (
    CONF_BASELINE_VOLUME,
    CONF_BOOST_VOLUME,
    CONF_COOLDOWN_SECONDS,
    CONF_DEBOUNCE_SECONDS,
    CONF_ENABLED,
    CONF_ESCALATION_SECONDS,
    CONF_MAX_VOLUME,
    CONF_NOTIFY_TARGETS,
    CONF_SETTLING_SECONDS,
    CONF_WHITE_NOISE,
    DEFAULT_OPTIONS,
    DOMAIN,
    ENTITY_DOMAINS,
    ENTRY_VERSION,
    PLATFORMS,
)
from .controller import NurserySootherController
from .models import SootherSettings

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

type NurserySootherConfigEntry = ConfigEntry[NurserySootherController]

_LOGGER = logging.getLogger(__name__)


def _validate_entry_device_ownership(
    hass: HomeAssistant, entry: ConfigEntry[Any]
) -> None:
    """Reject persisted entries that would compete for a nursery device."""
    for other_entry in hass.config_entries.async_entries(DOMAIN):
        if other_entry.entry_id == entry.entry_id:
            continue
        if any(
            entry.data.get(config_key) == other_entry.data.get(config_key)
            for config_key in ENTITY_DOMAINS
        ):
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="duplicate_devices",
            )


def _validate_entry_data(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    """Validate persisted data independently of the config flow."""
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

    _validate_entry_device_ownership(hass, entry)

    raw_options = DEFAULT_OPTIONS | dict(entry.options)
    try:
        settings = SootherSettings.from_options(raw_options)
    except (TypeError, ValueError) as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="invalid_options",
        ) from err
    volume_values = [
        raw_options[key]
        for key in (CONF_BASELINE_VOLUME, CONF_BOOST_VOLUME, CONF_MAX_VOLUME)
    ]
    timer_values = [
        raw_options[key]
        for key in (
            CONF_DEBOUNCE_SECONDS,
            CONF_COOLDOWN_SECONDS,
            CONF_SETTLING_SECONDS,
            CONF_ESCALATION_SECONDS,
        )
    ]
    if (
        not isinstance(raw_options[CONF_ENABLED], bool)
        or any(
            isinstance(value, bool) or not isinstance(value, int | float)
            for value in volume_values
        )
        or any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or float(value) <= 0
            or not float(value).is_integer()
            for value in timer_values
        )
        or not settings.volumes_are_valid()
    ):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="invalid_options",
        )

    has_functional_data = any(
        key in entry.data for key in (CONF_WHITE_NOISE, CONF_NOTIFY_TARGETS)
    )
    if has_functional_data:
        media = entry.data.get(CONF_WHITE_NOISE)
        targets = entry.data.get(CONF_NOTIFY_TARGETS)
        if not (
            isinstance(media, dict)
            and isinstance(media.get("media_content_id"), str)
            and bool(media["media_content_id"].strip())
            and media["media_content_id"].startswith("media-source://media_source/")
            and isinstance(media.get("media_content_type"), str)
            and media["media_content_type"].startswith("audio/")
            and isinstance(targets, list)
            and bool(targets)
            and all(
                isinstance(target, str) and target.startswith("notify.mobile_app_")
                for target in targets
            )
        ):
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="invalid_configuration",
            )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Migrate the inert foundation without starting nursery playback."""
    if entry.version > ENTRY_VERSION:
        return False
    if entry.version == ENTRY_VERSION:
        return True
    if entry.version != 1:
        return False

    options = DEFAULT_OPTIONS | dict(entry.options)
    # The foundation did not collect media or notification configuration. Keep
    # it safely disabled until the user completes Reconfigure in the UI.
    options[CONF_ENABLED] = False
    hass.config_entries.async_update_entry(
        entry,
        options=options,
        version=ENTRY_VERSION,
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: NurserySootherConfigEntry
) -> bool:
    """Set up a configured nursery response controller and its entities."""
    _validate_entry_data(hass, entry)

    controller = NurserySootherController(hass, entry)
    entry.runtime_data = controller
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await controller.async_start()
    except Exception:
        if await controller.async_shutdown():
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            raise
        _LOGGER.error(  # noqa: TRY400
            "Nursery Soother setup could not roll back speaker playback; "
            "keeping controls loaded for a safe retry"
        )
        return True
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: NurserySootherConfigEntry
) -> bool:
    """Cancel policy work and unload every entity platform."""
    controller = entry.runtime_data
    if not await controller.async_shutdown():
        return False
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        await controller.async_start()
    return unloaded
