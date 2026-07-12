"""Nursery Soother integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.core import valid_entity_id
from homeassistant.helpers.selector import TriggerSelector

from .const import (
    CONF_ATTENTION_SECONDS,
    CONF_AUTOMATIC_OPERATION,
    CONF_BASELINE_VOLUME,
    CONF_CRY_GAP_SECONDS,
    CONF_DEBOUNCE_SECONDS,
    CONF_DECREASE_LEVEL_TRIGGERS,
    CONF_EVIDENCE_WINDOW_SECONDS,
    CONF_INCREASE_LEVEL_TRIGGERS,
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
    CONF_TOGGLE_TRIGGERS,
    DEFAULT_OPTIONS,
    DOMAIN,
    ENTITY_DOMAINS,
    ENTRY_VERSION,
    PLATFORMS,
)
from .controller import NurserySootherController
from .frontend import FRONTEND_DOMAIN, async_register_frontend
from .models import ACTIVE_LEVELS, SootherSettings, SoothingLevel

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

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
_SETUP_ROLLBACK_FAILED = "Nursery Soother setup could not safely stop speaker playback"
_TRIGGER_SELECTOR = TriggerSelector()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-global Nursery Soother resources."""
    del config

    if FRONTEND_DOMAIN in hass.config.components:
        try:
            await async_register_frontend(hass)
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Nursery Soother dashboard card could not be registered; "
                "native entity controls remain available",
                exc_info=True,
            )
    return True


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


def _action_trigger_validation_error(entry: ConfigEntry[Any]) -> str | None:
    """Return an error for unsafe action triggers, if any."""
    seen_triggers: list[dict[str, Any]] = []
    for config_key in (
        CONF_TOGGLE_TRIGGERS,
        CONF_INCREASE_LEVEL_TRIGGERS,
        CONF_DECREASE_LEVEL_TRIGGERS,
    ):
        if config_key not in entry.data:
            continue
        trigger_config = entry.data.get(config_key)
        if not isinstance(trigger_config, list) or not trigger_config:
            return "invalid_action_triggers"
        try:
            validated = _TRIGGER_SELECTOR(trigger_config)
        except TypeError, ValueError, vol.Invalid:
            return "invalid_action_triggers"
        if not validated:
            return "invalid_action_triggers"
        for trigger in validated:
            if trigger in seen_triggers:
                return "action_trigger_reused"
            seen_triggers.append(trigger)
    return None


def _validate_entry_data(hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
    """Validate persisted v7 data independently of the config flow."""
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

    if trigger_error := _action_trigger_validation_error(entry):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key=trigger_error,
        )

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
    """Reject older entries; version 7 intentionally has no migration path."""
    del hass
    return entry.version == ENTRY_VERSION


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
    except Exception as err:
        rollback_complete = False
        try:
            rollback_complete = await controller.async_shutdown()
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Nursery Soother controller cleanup failed during setup rollback",
                exc_info=True,
            )
        if not rollback_complete:
            try:
                await controller.async_abort_startup()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Nursery Soother runtime cleanup failed during setup rollback",
                    exc_info=True,
                )
        try:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Nursery Soother platform cleanup failed during setup rollback",
                exc_info=True,
            )
        if not rollback_complete:
            raise ConfigEntryNotReady(_SETUP_ROLLBACK_FAILED) from err
        raise
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
