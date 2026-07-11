"""Config flow for Nursery Soother."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import voluptuous as vol
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    MediaSelector,
    MediaSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

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
    CONF_LEVEL_UP_SECONDS,
    CONF_MAX_VOLUME,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_SETTLING_SECONDS,
    CONF_SOOTHING_SOUND,
    CONF_SOUNDS,
    DEFAULT_AUTOMATIC_OPERATION,
    DEFAULT_LEVEL,
    DEFAULT_OPTIONS,
    DOMAIN,
    ENTITY_DOMAINS,
    ENTRY_VERSION,
    NAME,
)
from .models import ACTIVE_LEVELS, SootherSettings, SoothingLevel

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_REQUIRED_MEDIA_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET
)
_STOP_MEDIA_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.STOP | MediaPlayerEntityFeature.PAUSE
)
_NOTIFY_ACTION_PREFIX = "notify.mobile_app_"
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

_VOLUME_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        step=1,
        unit_of_measurement="%",
        mode=NumberSelectorMode.BOX,
    )
)
_TIMER_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        step=1,
        unit_of_measurement="s",
        mode=NumberSelectorMode.BOX,
    )
)

BEHAVIOR_SCHEMA = vol.Schema(
    {
        **{vol.Required(config_key): _VOLUME_SELECTOR for config_key in _VOLUME_KEYS},
        **{vol.Required(config_key): _TIMER_SELECTOR for config_key in _TIMER_KEYS},
    }
)


def _media_validation_error(media: object) -> str | None:
    """Return a translated error key for an unsafe media selection."""
    if not isinstance(media, dict):
        return "invalid_audio_media"
    content_id = media.get("media_content_id")
    content_type = media.get("media_content_type")
    if (
        not isinstance(content_id, str)
        or not content_id.strip()
        or not isinstance(content_type, str)
        or not content_type.startswith("audio/")
    ):
        return "invalid_audio_media"
    if not content_id.startswith("media-source://media_source/local/"):
        return "invalid_local_audio_media"
    return None


def sounds_are_valid(sounds: object) -> bool:
    """Return whether every active level has one safe local audio mapping."""
    if not isinstance(sounds, dict):
        return False
    expected_keys = {level.value for level in ACTIVE_LEVELS}
    return set(sounds) == expected_keys and all(
        _media_validation_error(sounds[level]) is None for level in expected_keys
    )


def _sounds_from_media(media: dict[str, str]) -> dict[str, dict[str, str]]:
    """Build independent per-level mappings from the initial shared sound."""
    return {level.value: dict(media) for level in ACTIVE_LEVELS}


def _stable_data_schema(hass: HomeAssistant) -> vol.Schema:
    """Build stable selectors with the currently registered mobile actions."""
    notify_actions = sorted(
        f"notify.{service}"
        for service in hass.services.async_services().get("notify", {})
        if service.startswith("mobile_app_")
    )
    return vol.Schema(
        {
            vol.Required(config_key): EntitySelector(
                EntitySelectorConfig(domain=expected_domain)
            )
            for config_key, expected_domain in ENTITY_DOMAINS.items()
        }
        | {
            vol.Required(CONF_SOOTHING_SOUND): MediaSelector(
                MediaSelectorConfig(accept=["audio/*"])
            ),
            vol.Required(CONF_NOTIFY_TARGETS): SelectSelector(
                SelectSelectorConfig(
                    options=notify_actions,
                    multiple=True,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _stable_suggested_values(data: dict[str, Any]) -> dict[str, Any]:
    """Translate stored per-level sounds back to the current shared selector."""
    suggested = {key: value for key, value in data.items() if key != CONF_SOUNDS}
    sounds = data.get(CONF_SOUNDS)
    if isinstance(sounds, dict):
        baseline = sounds.get(SoothingLevel.BASELINE.value)
        if isinstance(baseline, dict):
            suggested[CONF_SOOTHING_SOUND] = baseline
    return suggested


def _normalize_stable_data(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve and validate the stable references used by the controller."""
    registry = er.async_get(hass)
    normalized_data: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for config_key, expected_domain in ENTITY_DOMAINS.items():
        try:
            entity_id = er.async_validate_entity_id(registry, user_input[config_key])
        except vol.Invalid:
            errors[config_key] = "invalid_entity"
            continue

        if entity_id.partition(".")[0] != expected_domain:
            errors[config_key] = "invalid_entity_domain"
            continue

        state = hass.states.get(entity_id)
        if state is None:
            errors[config_key] = "invalid_entity"
            continue

        if config_key == CONF_MEDIA_PLAYER:
            raw_supported_features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
            if not isinstance(raw_supported_features, int):
                errors[config_key] = "unsupported_media_player"
                continue
            supported_features = MediaPlayerEntityFeature(raw_supported_features)
            if (
                supported_features & _REQUIRED_MEDIA_PLAYER_FEATURES
                != _REQUIRED_MEDIA_PLAYER_FEATURES
                or not supported_features & _STOP_MEDIA_PLAYER_FEATURES
            ):
                errors[config_key] = "unsupported_media_player"
                continue

        normalized_data[config_key] = entity_id

    media = user_input[CONF_SOOTHING_SOUND]
    if media_error := _media_validation_error(media):
        errors[CONF_SOOTHING_SOUND] = media_error
    else:
        normalized_data[CONF_SOUNDS] = _sounds_from_media(media)

    normalized_targets, target_error = _normalize_notify_targets(
        hass, user_input[CONF_NOTIFY_TARGETS]
    )
    if target_error is not None:
        errors[CONF_NOTIFY_TARGETS] = target_error
    else:
        normalized_data[CONF_NOTIFY_TARGETS] = normalized_targets

    return normalized_data, errors


def _normalize_notify_targets(
    hass: HomeAssistant, targets: list[str]
) -> tuple[list[str], str | None]:
    """Validate and canonicalize mobile-app notification actions."""
    if not targets:
        return [], "notify_targets_required"

    normalized_targets: list[str] = []
    for target in targets:
        try:
            action = cv.service(target.strip())
        except AttributeError, vol.Invalid:
            return [], "invalid_notify_action"

        if not action.startswith(_NOTIFY_ACTION_PREFIX):
            return [], "invalid_notify_action"

        domain, service = action.split(".", 1)
        if not hass.services.has_service(domain, service):
            return [], "notify_action_not_found"

        if action not in normalized_targets:
            normalized_targets.append(action)

    return normalized_targets, None


def _find_entry_conflicts(
    hass: HomeAssistant,
    data: dict[str, Any],
    *,
    ignore_entry_id: str | None = None,
) -> dict[str, str]:
    """Prevent two controllers from competing for the same nursery devices."""
    errors: dict[str, str] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == ignore_entry_id:
            continue
        for config_key in ENTITY_DOMAINS:
            if data.get(config_key) == entry.data.get(config_key):
                errors[config_key] = "entity_already_configured"
    return errors


def _normalize_behavior(
    user_input: dict[str, Any],
    *,
    level: str,
    automatic_operation: bool,
) -> tuple[dict[str, float | int | bool | str], dict[str, str]]:
    """Validate behavior values and return their persisted representation."""
    normalized: dict[str, float | int | bool | str] = {
        config_key: float(user_input[config_key]) for config_key in _VOLUME_KEYS
    }
    errors: dict[str, str] = {}

    for config_key in _TIMER_KEYS:
        value = float(user_input[config_key])
        if value <= 0 or not value.is_integer():
            errors[config_key] = "positive_integer_required"
            continue
        normalized[config_key] = int(value)

    normalized[CONF_LEVEL] = level
    normalized[CONF_AUTOMATIC_OPERATION] = automatic_operation
    if errors:
        return normalized, errors

    settings = SootherSettings.from_options(normalized)
    if not settings.volumes_are_valid():
        errors["base"] = "invalid_volume_configuration"

    return normalized, errors


class NurserySootherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Nursery Soother config flow."""

    VERSION = ENTRY_VERSION

    _stable_data: dict[str, Any]

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> NurserySootherOptionsFlow:
        """Return the behavior options flow."""
        return NurserySootherOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect stable entity, media, and notification references."""
        errors: dict[str, str] = {}
        if user_input is not None:
            normalized_data, errors = _normalize_stable_data(self.hass, user_input)
            if not errors:
                errors.update(_find_entry_conflicts(self.hass, normalized_data))
            if not errors:
                self._stable_data = normalized_data
                return await self.async_step_behavior()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _stable_data_schema(self.hass), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_behavior(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect safe initial level and evidence policy settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = _normalize_behavior(
                user_input,
                level=DEFAULT_LEVEL,
                automatic_operation=DEFAULT_AUTOMATIC_OPERATION,
            )
            if not errors:
                if _find_entry_conflicts(self.hass, self._stable_data):
                    return self.async_abort(reason="devices_already_configured")
                return self.async_create_entry(
                    title=NAME,
                    data=self._stable_data,
                    options=options,
                )

        return self.async_show_form(
            step_id="behavior",
            data_schema=self.add_suggested_values_to_schema(
                BEHAVIOR_SCHEMA, user_input or DEFAULT_OPTIONS
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update stable references and reload the config entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            normalized_data, errors = _normalize_stable_data(self.hass, user_input)
            if not errors:
                errors.update(
                    _find_entry_conflicts(
                        self.hass,
                        normalized_data,
                        ignore_entry_id=entry.entry_id,
                    )
                )
            if not errors:
                return self.async_update_reload_and_abort(entry, data=normalized_data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _stable_data_schema(self.hass),
                user_input or _stable_suggested_values(dict(entry.data)),
            ),
            errors=errors,
        )


class NurserySootherOptionsFlow(OptionsFlowWithReload):
    """Handle parent-adjustable Nursery Soother behavior settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update behavior while preserving the level and operating mode."""
        errors: dict[str, str] = {}
        current_options = DEFAULT_OPTIONS | self.config_entry.options
        if user_input is not None:
            options, errors = _normalize_behavior(
                user_input,
                level=str(current_options[CONF_LEVEL]),
                automatic_operation=bool(current_options[CONF_AUTOMATIC_OPERATION]),
            )
            if not errors:
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                BEHAVIOR_SCHEMA, user_input or current_options
            ),
            errors=errors,
        )
