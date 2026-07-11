"""Tests for the Nursery Soother config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigFlowResult,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import ServiceCall, callback
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nursery_soother.config_flow import (
    NurserySootherConfigFlow,
    sounds_are_valid,
)
from custom_components.nursery_soother.const import (
    CONF_ATTENTION_SECONDS,
    CONF_AUTOMATIC_OPERATION,
    CONF_BASELINE_SOUND,
    CONF_BASELINE_VOLUME,
    CONF_CAMERA,
    CONF_CRY_GAP_SECONDS,
    CONF_CRY_SENSOR,
    CONF_DEBOUNCE_SECONDS,
    CONF_EVIDENCE_WINDOW_SECONDS,
    CONF_LEVEL,
    CONF_LEVEL_1_SOUND,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_SOUND,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_SOUND,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_SOUND,
    CONF_LEVEL_4_VOLUME,
    CONF_LEVEL_LOCK,
    CONF_LEVEL_UP_SECONDS,
    CONF_MAX_VOLUME,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_SETTLING_SECONDS,
    CONF_SOUNDS,
    DEFAULT_ATTENTION_SECONDS,
    DEFAULT_AUTOMATIC_OPERATION,
    DEFAULT_BASELINE_VOLUME,
    DEFAULT_CRY_GAP_SECONDS,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_EVIDENCE_WINDOW_SECONDS,
    DEFAULT_LEVEL,
    DEFAULT_LEVEL_1_VOLUME,
    DEFAULT_LEVEL_2_VOLUME,
    DEFAULT_LEVEL_3_VOLUME,
    DEFAULT_LEVEL_4_VOLUME,
    DEFAULT_LEVEL_LOCK,
    DEFAULT_LEVEL_UP_SECONDS,
    DEFAULT_MAX_VOLUME,
    DEFAULT_SETTLING_SECONDS,
    DOMAIN,
    ENTRY_VERSION,
    NAME,
)
from custom_components.nursery_soother.models import ACTIVE_LEVELS, SoothingLevel

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LEVEL_SOUND_KEYS = {
    SoothingLevel.BASELINE: CONF_BASELINE_SOUND,
    SoothingLevel.LEVEL_1: CONF_LEVEL_1_SOUND,
    SoothingLevel.LEVEL_2: CONF_LEVEL_2_SOUND,
    SoothingLevel.LEVEL_3: CONF_LEVEL_3_SOUND,
    SoothingLevel.LEVEL_4: CONF_LEVEL_4_SOUND,
}
SOUND_SELECTIONS = {
    config_key: {
        "media_content_id": f"media-source://media_source/local/{level.value}.mp3",
        "media_content_type": "audio/mpeg",
    }
    for level, config_key in LEVEL_SOUND_KEYS.items()
}
SOUNDS = {
    level.value: dict(SOUND_SELECTIONS[config_key])
    for level, config_key in LEVEL_SOUND_KEYS.items()
}
USER_DATA = {
    CONF_CRY_SENSOR: "binary_sensor.nursery_crying",
    CONF_CAMERA: "camera.nursery",
    CONF_MEDIA_PLAYER: "media_player.nursery",
    **SOUND_SELECTIONS,
    CONF_NOTIFY_TARGETS: [
        "notify.mobile_app_parent_one",
        "notify.mobile_app_parent_two",
    ],
}
CONFIG_DATA = {
    key: value for key, value in USER_DATA.items() if key not in SOUND_SELECTIONS
} | {CONF_SOUNDS: SOUNDS}
BEHAVIOR_DATA = {
    CONF_BASELINE_VOLUME: DEFAULT_BASELINE_VOLUME,
    CONF_LEVEL_1_VOLUME: DEFAULT_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME: DEFAULT_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME: DEFAULT_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME: DEFAULT_LEVEL_4_VOLUME,
    CONF_MAX_VOLUME: DEFAULT_MAX_VOLUME,
    CONF_DEBOUNCE_SECONDS: DEFAULT_DEBOUNCE_SECONDS,
    CONF_EVIDENCE_WINDOW_SECONDS: DEFAULT_EVIDENCE_WINDOW_SECONDS,
    CONF_CRY_GAP_SECONDS: DEFAULT_CRY_GAP_SECONDS,
    CONF_LEVEL_UP_SECONDS: DEFAULT_LEVEL_UP_SECONDS,
    CONF_SETTLING_SECONDS: DEFAULT_SETTLING_SECONDS,
    CONF_ATTENTION_SECONDS: DEFAULT_ATTENTION_SECONDS,
}
ENTRY_OPTIONS = BEHAVIOR_DATA | {
    CONF_LEVEL: DEFAULT_LEVEL,
    CONF_AUTOMATIC_OPERATION: DEFAULT_AUTOMATIC_OPERATION,
    CONF_LEVEL_LOCK: DEFAULT_LEVEL_LOCK,
}
MEDIA_PLAYER_FEATURES = int(
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.STOP
)


@pytest.fixture(autouse=True)
def _register_dependencies(hass: HomeAssistant) -> None:
    """Register valid source entities and mobile notification actions."""
    hass.states.async_set(USER_DATA[CONF_CRY_SENSOR], "off")
    hass.states.async_set(USER_DATA[CONF_CAMERA], "idle")
    hass.states.async_set(
        USER_DATA[CONF_MEDIA_PLAYER],
        "idle",
        {ATTR_SUPPORTED_FEATURES: MEDIA_PLAYER_FEATURES},
    )

    @callback
    def _handle_notification(_call: ServiceCall) -> None:
        """Handle a test notification."""

    hass.services.async_register(
        "notify", "mobile_app_parent_one", _handle_notification
    )
    hass.services.async_register(
        "notify", "mobile_app_parent_two", _handle_notification
    )


async def _start_user_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Start a user flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )


async def _start_behavior_step(
    hass: HomeAssistant, stable_data: dict[str, Any] | None = None
) -> ConfigFlowResult:
    """Start a user flow and submit valid stable form data."""
    result = await _start_user_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], stable_data or USER_DATA
    )


def _entry(
    hass: HomeAssistant,
    *,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Add a configured Nursery Soother entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=data or CONFIG_DATA,
        options=options or ENTRY_OPTIONS,
        version=ENTRY_VERSION,
    )
    entry.add_to_hass(hass)
    return entry


async def test_user_flow_creates_standby_manual_entry_with_level_sounds(
    hass: HomeAssistant,
) -> None:
    """Setup stores one independently selected local sound for every level."""
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert NurserySootherConfigFlow.VERSION == ENTRY_VERSION
    form_keys = {key.schema for key in result["data_schema"].schema}
    assert set(SOUND_SELECTIONS) <= form_keys
    assert "soothing_sound" not in form_keys

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_DATA
    )
    assert result["step_id"] == "behavior"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BEHAVIOR_DATA
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == CONFIG_DATA
    assert result["options"] == ENTRY_OPTIONS
    assert sounds_are_valid(result["data"][CONF_SOUNDS])
    sound_values = list(result["data"][CONF_SOUNDS].values())
    assert sound_values == list(SOUNDS.values())
    assert len({id(sound) for sound in sound_values}) == len(ACTIVE_LEVELS)


def test_sound_mapping_validation_is_ready_for_distinct_level_media() -> None:
    """Stored mappings may already carry a different safe sound per level."""
    sounds = {
        level.value: {
            "media_content_id": (
                f"media-source://media_source/local/{level.value}.mp3"
            ),
            "media_content_type": "audio/mpeg",
        }
        for level in ACTIVE_LEVELS
    }
    assert sounds_are_valid(sounds)
    assert not sounds_are_valid(
        {SoothingLevel.BASELINE.value: SOUNDS[SoothingLevel.BASELINE.value]}
    )
    sounds[SoothingLevel.LEVEL_4.value]["media_content_type"] = "video/mp4"
    assert not sounds_are_valid(sounds)


async def test_user_flow_canonicalizes_registry_and_notification_values(
    hass: HomeAssistant,
) -> None:
    """Registry UUIDs and duplicated mixed-case actions are canonicalized."""
    registry_entry = er.async_get(hass).async_get_or_create(
        domain="binary_sensor",
        platform="test",
        unique_id="nursery_crying_registry",
        suggested_object_id="nursery_crying_registry",
    )
    hass.states.async_set(registry_entry.entity_id, "off")
    user_data = USER_DATA | {
        CONF_CRY_SENSOR: registry_entry.id,
        CONF_NOTIFY_TARGETS: [
            " Notify.Mobile_App_Parent_One ",
            "notify.mobile_app_parent_one",
        ],
    }
    result = await _start_behavior_step(hass, user_data)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BEHAVIOR_DATA
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CRY_SENSOR] == registry_entry.entity_id
    assert result["data"][CONF_NOTIFY_TARGETS] == ["notify.mobile_app_parent_one"]


@pytest.mark.parametrize(
    ("config_key", "invalid_entity_id"),
    [
        (CONF_CRY_SENSOR, "sensor.nursery_crying"),
        (CONF_CAMERA, "image.nursery"),
        (CONF_MEDIA_PLAYER, "switch.nursery_speaker"),
    ],
)
async def test_user_flow_selector_rejects_wrong_domain(
    hass: HomeAssistant,
    config_key: str,
    invalid_entity_id: str,
) -> None:
    """Each entity selector rejects an entity from the wrong domain."""
    result = await _start_user_flow(hass)
    with pytest.raises(InvalidData) as error:
        await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_DATA | {config_key: invalid_entity_id}
        )
    assert error.value.path == [config_key]


@pytest.mark.parametrize(
    "supported_features",
    [
        0,
        int(MediaPlayerEntityFeature.PLAY_MEDIA),
        int(MediaPlayerEntityFeature.VOLUME_SET),
        int(MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET),
        None,
    ],
)
async def test_user_flow_rejects_unsupported_player(
    hass: HomeAssistant,
    supported_features: int | None,
) -> None:
    """The speaker must play media, set volume, and stop or pause."""
    hass.states.async_set(
        USER_DATA[CONF_MEDIA_PLAYER],
        "idle",
        {ATTR_SUPPORTED_FEATURES: supported_features},
    )
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MEDIA_PLAYER: "unsupported_media_player"}


async def test_user_flow_accepts_pause_as_stop_fallback(
    hass: HomeAssistant,
) -> None:
    """A player with Pause provides a safe Standby fallback."""
    hass.states.async_set(
        USER_DATA[CONF_MEDIA_PLAYER],
        "idle",
        {
            ATTR_SUPPORTED_FEATURES: int(
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.PAUSE
            )
        },
    )
    result = await _start_behavior_step(hass)
    assert result["step_id"] == "behavior"


@pytest.mark.parametrize(
    ("media", "error_key"),
    [
        (
            {
                "media_content_id": "media-source://media_source/local/video.mp4",
                "media_content_type": "video/mp4",
            },
            "invalid_audio_media",
        ),
        (
            {"media_content_id": "   ", "media_content_type": "audio/mpeg"},
            "invalid_audio_media",
        ),
        (
            {
                "media_content_id": "media-source://radio_browser/example",
                "media_content_type": "audio/mpeg",
            },
            "invalid_local_audio_media",
        ),
    ],
)
async def test_user_flow_rejects_unsafe_soothing_sound(
    hass: HomeAssistant,
    media: dict[str, str],
    error_key: str,
) -> None:
    """Only local audio may be selected for a level."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_DATA | {CONF_LEVEL_2_SOUND: media}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_LEVEL_2_SOUND: error_key}


@pytest.mark.parametrize(
    ("targets", "error_key"),
    [
        ([], "notify_targets_required"),
        (["invalid"], "invalid_notify_action"),
        (["notify.parent_phone"], "invalid_notify_action"),
        (["notify.mobile_app_missing"], "notify_action_not_found"),
    ],
)
async def test_user_flow_rejects_invalid_notification_actions(
    hass: HomeAssistant,
    targets: list[str],
    error_key: str,
) -> None:
    """Parent targets must be registered Companion App notification actions."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_DATA | {CONF_NOTIFY_TARGETS: targets}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NOTIFY_TARGETS: error_key}


@pytest.mark.parametrize(
    "volume_data",
    [
        {CONF_BASELINE_VOLUME: -1},
        {CONF_BASELINE_VOLUME: 16},
        {CONF_LEVEL_1_VOLUME: 21},
        {CONF_LEVEL_2_VOLUME: 26},
        {CONF_LEVEL_3_VOLUME: 31},
        {CONF_LEVEL_4_VOLUME: 41},
        {CONF_MAX_VOLUME: 101},
    ],
)
async def test_behavior_rejects_nonmonotonic_or_unsafe_volumes(
    hass: HomeAssistant,
    volume_data: dict[str, float],
) -> None:
    """All five active levels and the hard cap form one invariant."""
    result = await _start_behavior_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BEHAVIOR_DATA | volume_data
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_volume_configuration"}


@pytest.mark.parametrize(
    "timer_key",
    [
        CONF_DEBOUNCE_SECONDS,
        CONF_EVIDENCE_WINDOW_SECONDS,
        CONF_CRY_GAP_SECONDS,
        CONF_LEVEL_UP_SECONDS,
        CONF_SETTLING_SECONDS,
        CONF_ATTENTION_SECONDS,
    ],
)
@pytest.mark.parametrize("invalid_value", [0, -1, 1.5])
async def test_behavior_rejects_nonpositive_or_fractional_timers(
    hass: HomeAssistant,
    timer_key: str,
    invalid_value: float,
) -> None:
    """Every evidence and response timer uses positive whole seconds."""
    result = await _start_behavior_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BEHAVIOR_DATA | {timer_key: invalid_value}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {timer_key: "positive_integer_required"}


async def test_entries_cannot_share_a_nursery_device(
    hass: HomeAssistant,
) -> None:
    """Two controllers cannot compete for one physical nursery resource."""
    _entry(hass)
    second = USER_DATA | {
        CONF_CRY_SENSOR: "binary_sensor.second_nursery_crying",
        CONF_CAMERA: "camera.second_nursery",
    }
    hass.states.async_set(second[CONF_CRY_SENSOR], "off")
    hass.states.async_set(second[CONF_CAMERA], "idle")
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], second)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MEDIA_PLAYER: "entity_already_configured"}


async def test_reconfigure_round_trips_stored_sound_mapping(
    hass: HomeAssistant,
) -> None:
    """Reconfigure presents and updates independent per-level selectors."""
    entry = _entry(hass)
    hass.states.async_set("camera.second_nursery", "idle")
    replacement = {
        "media_content_id": "media-source://media_source/local/level_3-new.mp3",
        "media_content_type": "audio/mpeg",
    }
    updated_form = USER_DATA | {
        CONF_CAMERA: "camera.second_nursery",
        CONF_LEVEL_3_SOUND: replacement,
    }
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["step_id"] == "reconfigure"
    suggested_values = {
        key.schema: key.description["suggested_value"]
        for key in result["data_schema"].schema
        if isinstance(key.description, dict)
    }
    assert {
        config_key: suggested_values[config_key] for config_key in SOUND_SELECTIONS
    } == SOUND_SELECTIONS

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], updated_form
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CAMERA] == "camera.second_nursery"
    assert entry.data[CONF_SOUNDS] == SOUNDS | {
        SoothingLevel.LEVEL_3.value: replacement
    }
    assert not set(SOUND_SELECTIONS) & set(entry.data)
    assert entry.options == ENTRY_OPTIONS


async def test_options_preserve_runtime_level_and_automatic_preference(
    hass: HomeAssistant,
) -> None:
    """Behavior updates cannot silently change the primary controls."""
    current_options = ENTRY_OPTIONS | {
        CONF_LEVEL: SoothingLevel.LEVEL_3.value,
        CONF_AUTOMATIC_OPERATION: True,
        CONF_LEVEL_LOCK: True,
    }
    entry = _entry(hass, options=current_options)
    updated_behavior = BEHAVIOR_DATA | {
        CONF_LEVEL_1_VOLUME: 16,
        CONF_LEVEL_2_VOLUME: 21,
        CONF_LEVEL_3_VOLUME: 26,
        CONF_LEVEL_4_VOLUME: 31,
        CONF_MAX_VOLUME: 45,
        CONF_DEBOUNCE_SECONDS: 12,
    }
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], updated_behavior
    )

    expected = updated_behavior | {
        CONF_LEVEL: SoothingLevel.LEVEL_3.value,
        CONF_AUTOMATIC_OPERATION: True,
        CONF_LEVEL_LOCK: True,
    }
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == expected
    assert entry.options == expected


async def test_options_reject_invalid_behavior_without_mutation(
    hass: HomeAssistant,
) -> None:
    """Options use the same monotonic safety validation as initial setup."""
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], BEHAVIOR_DATA | {CONF_LEVEL_4_VOLUME: 50}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_volume_configuration"}
    assert entry.options == ENTRY_OPTIONS
