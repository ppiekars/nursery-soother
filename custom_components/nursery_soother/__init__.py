"""Nursery Soother integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryError
from homeassistant.core import valid_entity_id

from .const import (
    CONF_ATTENTION_SECONDS,
    CONF_AUTOMATIC_OPERATION,
    CONF_BASELINE_VOLUME,
    CONF_CRY_GAP_SECONDS,
    CONF_DEBOUNCE_SECONDS,
    CONF_EVIDENCE_WINDOW_SECONDS,
    CONF_LEVEL,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_LEVEL_LOCK,
    CONF_LEVEL_UP_SECONDS,
    CONF_MAX_VOLUME,
    CONF_NOTIFY_TARGETS,
    CONF_SETTLING_SECONDS,
    CONF_SOUNDS,
    DEFAULT_OPTIONS,
    DOMAIN,
    ENTITY_DOMAINS,
    ENTRY_VERSION,
    PLATFORMS,
)
from .controller import NurserySootherController
from .models import ACTIVE_LEVELS, SootherSettings, SoothingLevel

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

type NurserySootherConfigEntry = ConfigEntry[NurserySootherController]

_LOGGER = logging.getLogger(__name__)

_VOLUME_KEYS = (
    CONF_BASELINE_VOLUME,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_MAX_VOLUME,
)
_TIMER_KEYS = (
    CONF_DEBOUNCE_SECONDS,
    CONF_EVIDENCE_WINDOW_SECONDS,
    CONF_CRY_GAP_SECONDS,
    CONF_LEVEL_UP_SECONDS,
    CONF_SETTLING_SECONDS,
    CONF_ATTENTION_SECONDS,
)
_LOCAL_MEDIA_PREFIX = "media-source://media_source/local/"
_MOBILE_NOTIFY_PREFIX = "notify.mobile_app_"
_LEGACY_ENTRY_VERSION = 4
_LEGACY_DEFAULT_DEBOUNCE_SECONDS = 10
_LEGACY_DEFAULT_LEVEL_UP_SECONDS = 30


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


def _media_is_valid(media: object) -> bool:
    """Return whether one stored profile selects safe local audio."""
    if not isinstance(media, dict):
        return False
    content_id = media.get("media_content_id")
    content_type = media.get("media_content_type")
    return (
        isinstance(content_id, str)
        and bool(content_id.strip())
        and content_id.startswith(_LOCAL_MEDIA_PREFIX)
        and isinstance(content_type, str)
        and content_type.startswith("audio/")
    )


def _functional_data_is_valid(entry: ConfigEntry[Any]) -> bool:
    """Validate the complete level-sound catalog and parent targets."""
    sounds = entry.data.get(CONF_SOUNDS)
    expected_sound_keys = {level.value for level in ACTIVE_LEVELS}
    targets = entry.data.get(CONF_NOTIFY_TARGETS)
    return (
        isinstance(sounds, dict)
        and set(sounds) == expected_sound_keys
        and all(_media_is_valid(sounds[key]) for key in expected_sound_keys)
        and isinstance(targets, list)
        and bool(targets)
        and all(
            isinstance(target, str)
            and valid_entity_id(target)
            and target.startswith(_MOBILE_NOTIFY_PREFIX)
            and len(target) > len(_MOBILE_NOTIFY_PREFIX)
            for target in targets
        )
    )


def _validate_entry_data(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    """Validate persisted v6 data independently of the config flow."""
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

    if (
        not isinstance(raw_options[CONF_LEVEL], str)
        or not isinstance(settings.level, SoothingLevel)
        or not isinstance(raw_options[CONF_AUTOMATIC_OPERATION], bool)
        or not isinstance(raw_options[CONF_LEVEL_LOCK], bool)
        or any(
            isinstance(value, bool) or not isinstance(value, int | float)
            for value in (raw_options[key] for key in _VOLUME_KEYS)
        )
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (raw_options[key] for key in _TIMER_KEYS)
        )
        or not settings.volumes_are_valid()
    ):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="invalid_options",
        )

    if not _functional_data_is_valid(entry):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="invalid_configuration",
        )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Migrate legacy timings and add the v6 level-lock default."""
    if entry.version == ENTRY_VERSION:
        return True
    if entry.version not in {_LEGACY_ENTRY_VERSION, 5}:
        return False

    options = dict(entry.options)
    if entry.version == _LEGACY_ENTRY_VERSION:
        if (
            options.get(CONF_DEBOUNCE_SECONDS, _LEGACY_DEFAULT_DEBOUNCE_SECONDS)
            == _LEGACY_DEFAULT_DEBOUNCE_SECONDS
        ):
            options[CONF_DEBOUNCE_SECONDS] = DEFAULT_OPTIONS[CONF_DEBOUNCE_SECONDS]
        if (
            options.get(CONF_LEVEL_UP_SECONDS, _LEGACY_DEFAULT_LEVEL_UP_SECONDS)
            == _LEGACY_DEFAULT_LEVEL_UP_SECONDS
        ):
            options[CONF_LEVEL_UP_SECONDS] = DEFAULT_OPTIONS[CONF_LEVEL_UP_SECONDS]
    options.setdefault(CONF_LEVEL_LOCK, DEFAULT_OPTIONS[CONF_LEVEL_LOCK])

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
