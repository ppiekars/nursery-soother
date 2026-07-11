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

from custom_components.nursery_soother.config_flow import NurserySootherConfigFlow
from custom_components.nursery_soother.const import (
    CONF_BASELINE_VOLUME,
    CONF_BOOST_VOLUME,
    CONF_CAMERA,
    CONF_COOLDOWN_SECONDS,
    CONF_CRY_SENSOR,
    CONF_DEBOUNCE_SECONDS,
    CONF_ENABLED,
    CONF_ESCALATION_SECONDS,
    CONF_MAX_VOLUME,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_SETTLING_SECONDS,
    CONF_WHITE_NOISE,
    DEFAULT_BASELINE_VOLUME,
    DEFAULT_BOOST_VOLUME,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_ESCALATION_SECONDS,
    DEFAULT_MAX_VOLUME,
    DEFAULT_SETTLING_SECONDS,
    DOMAIN,
    ENTRY_VERSION,
    NAME,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

WHITE_NOISE = {
    "media_content_id": "media-source://media_source/local/white-noise.mp3",
    "media_content_type": "audio/mpeg",
}
CONFIG_DATA = {
    CONF_CRY_SENSOR: "binary_sensor.nursery_crying",
    CONF_CAMERA: "camera.nursery",
    CONF_MEDIA_PLAYER: "media_player.nursery",
    CONF_WHITE_NOISE: WHITE_NOISE,
    CONF_NOTIFY_TARGETS: [
        "notify.mobile_app_parent_one",
        "notify.mobile_app_parent_two",
    ],
}
BEHAVIOR_DATA = {
    CONF_BASELINE_VOLUME: DEFAULT_BASELINE_VOLUME,
    CONF_BOOST_VOLUME: DEFAULT_BOOST_VOLUME,
    CONF_MAX_VOLUME: DEFAULT_MAX_VOLUME,
    CONF_DEBOUNCE_SECONDS: DEFAULT_DEBOUNCE_SECONDS,
    CONF_COOLDOWN_SECONDS: DEFAULT_COOLDOWN_SECONDS,
    CONF_SETTLING_SECONDS: DEFAULT_SETTLING_SECONDS,
    CONF_ESCALATION_SECONDS: DEFAULT_ESCALATION_SECONDS,
}
ENTRY_OPTIONS = BEHAVIOR_DATA | {CONF_ENABLED: False}
MEDIA_PLAYER_FEATURES = int(
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.STOP
)


@pytest.fixture(autouse=True)
def _register_dependencies(hass: HomeAssistant) -> None:
    """Register valid source entities and mobile notification actions."""
    hass.states.async_set(CONFIG_DATA[CONF_CRY_SENSOR], "off")
    hass.states.async_set(CONFIG_DATA[CONF_CAMERA], "idle")
    hass.states.async_set(
        CONFIG_DATA[CONF_MEDIA_PLAYER],
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
    """Start a user flow and submit valid stable data."""
    result = await _start_user_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], stable_data or CONFIG_DATA
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


async def test_user_flow_creates_disabled_entry(hass: HomeAssistant) -> None:
    """Test the complete two-step setup flow."""
    result = await _start_user_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert NurserySootherConfigFlow.VERSION == ENTRY_VERSION

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONFIG_DATA
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "behavior"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BEHAVIOR_DATA
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == CONFIG_DATA
    assert result["options"] == ENTRY_OPTIONS


async def test_user_flow_canonicalizes_selector_and_action_values(
    hass: HomeAssistant,
) -> None:
    """Test registry UUIDs and notification action names are canonicalized."""
    registry_entry = er.async_get(hass).async_get_or_create(
        domain="binary_sensor",
        platform="test",
        unique_id="nursery_crying_registry",
        suggested_object_id="nursery_crying_registry",
    )
    hass.states.async_set(registry_entry.entity_id, "off")
    stable_data = CONFIG_DATA | {
        CONF_CRY_SENSOR: registry_entry.id,
        CONF_NOTIFY_TARGETS: [
            " Notify.Mobile_App_Parent_One ",
            "notify.mobile_app_parent_one",
        ],
    }
    result = await _start_behavior_step(hass, stable_data)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BEHAVIOR_DATA
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CRY_SENSOR] == registry_entry.entity_id
    assert result["data"][CONF_NOTIFY_TARGETS] == ["notify.mobile_app_parent_one"]


async def test_user_flow_rejects_uuid_from_wrong_domain(
    hass: HomeAssistant,
) -> None:
    """Test domain validation runs after resolving selector UUIDs."""
    registry_entry = er.async_get(hass).async_get_or_create(
        domain="sensor",
        platform="test",
        unique_id="nursery_crying",
        suggested_object_id="nursery_crying",
    )
    hass.states.async_set(registry_entry.entity_id, "off")
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        CONFIG_DATA | {CONF_CRY_SENSOR: registry_entry.id},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_CRY_SENSOR: "invalid_entity_domain"}


async def test_user_flow_rejects_unknown_entity_registry_uuid(
    hass: HomeAssistant,
) -> None:
    """Test unknown selector UUIDs return a translated form error."""
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        CONFIG_DATA | {CONF_CRY_SENSOR: "0123456789abcdef0123456789abcdef"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CRY_SENSOR: "invalid_entity"}


@pytest.mark.parametrize(
    "config_key",
    [CONF_CRY_SENSOR, CONF_CAMERA, CONF_MEDIA_PLAYER],
)
async def test_user_flow_rejects_entity_without_state(
    hass: HomeAssistant,
    config_key: str,
) -> None:
    """Test selected entities must exist in Home Assistant."""
    hass.states.async_remove(CONFIG_DATA[config_key])
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONFIG_DATA
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {config_key: "invalid_entity"}


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
    """Test each entity selector filters entities from the wrong domain."""
    result = await _start_user_flow(hass)

    with pytest.raises(InvalidData) as error:
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            CONFIG_DATA | {config_key: invalid_entity_id},
        )

    assert error.value.path == [config_key]
    assert config_key in error.value.schema_errors


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
async def test_user_flow_rejects_media_player_without_required_features(
    hass: HomeAssistant,
    supported_features: int | None,
) -> None:
    """Test the speaker must play media and set an absolute volume."""
    hass.states.async_set(
        CONFIG_DATA[CONF_MEDIA_PLAYER],
        "idle",
        {ATTR_SUPPORTED_FEATURES: supported_features},
    )
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONFIG_DATA
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MEDIA_PLAYER: "unsupported_media_player"}


async def test_user_flow_accepts_pause_as_stop_fallback(
    hass: HomeAssistant,
) -> None:
    """A player that can pause instead of stop still provides safe Stop behavior."""
    hass.states.async_set(
        CONFIG_DATA[CONF_MEDIA_PLAYER],
        "idle",
        {
            ATTR_SUPPORTED_FEATURES: int(
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.PAUSE
            )
        },
    )
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONFIG_DATA
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "behavior"


async def test_user_flow_rejects_non_audio_media(
    hass: HomeAssistant,
) -> None:
    """Backend validation rejects media-selector payloads that are not audio."""
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        CONFIG_DATA
        | {
            CONF_WHITE_NOISE: {
                "media_content_id": "media-source://media_source/local/video.mp4",
                "media_content_type": "video/mp4",
            }
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_WHITE_NOISE: "invalid_audio_media"}


async def test_user_flow_rejects_empty_audio_media_id(
    hass: HomeAssistant,
) -> None:
    """Backend validation rejects an empty media-selector identifier."""
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        CONFIG_DATA
        | {
            CONF_WHITE_NOISE: {
                "media_content_id": "   ",
                "media_content_type": "audio/mpeg",
            }
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_WHITE_NOISE: "invalid_audio_media"}


async def test_user_flow_rejects_non_local_audio_media(
    hass: HomeAssistant,
) -> None:
    """White noise must come from Home Assistant's local My media library."""
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        CONFIG_DATA
        | {
            CONF_WHITE_NOISE: {
                "media_content_id": "media-source://radio_browser/example",
                "media_content_type": "audio/mpeg",
            }
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_WHITE_NOISE: "invalid_local_audio_media"}


@pytest.mark.parametrize(
    ("notify_targets", "error_key"),
    [
        ([], "notify_targets_required"),
        (["invalid"], "invalid_notify_action"),
        (["notify.parent_phone"], "invalid_notify_action"),
        (["notify.mobile_app_missing"], "notify_action_not_found"),
    ],
)
async def test_user_flow_rejects_invalid_notification_actions(
    hass: HomeAssistant,
    notify_targets: list[str],
    error_key: str,
) -> None:
    """Test parent targets are registered mobile-app notification actions."""
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        CONFIG_DATA | {CONF_NOTIFY_TARGETS: notify_targets},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NOTIFY_TARGETS: error_key}


@pytest.mark.parametrize(
    "volume_data",
    [
        {CONF_BASELINE_VOLUME: -1},
        {CONF_BASELINE_VOLUME: 31},
        {CONF_BOOST_VOLUME: 41},
        {CONF_MAX_VOLUME: 101},
    ],
)
async def test_behavior_rejects_unsafe_volume_relationships(
    hass: HomeAssistant,
    volume_data: dict[str, float],
) -> None:
    """Test volume limits are validated as one safety invariant."""
    result = await _start_behavior_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BEHAVIOR_DATA | volume_data
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "behavior"
    assert result["errors"] == {"base": "invalid_volume_configuration"}


@pytest.mark.parametrize(
    "timer_key",
    BEHAVIOR_DATA.keys()
    - {
        CONF_BASELINE_VOLUME,
        CONF_BOOST_VOLUME,
        CONF_MAX_VOLUME,
    },
)
@pytest.mark.parametrize("invalid_value", [0, -1, 1.5])
async def test_behavior_rejects_non_positive_or_fractional_timers(
    hass: HomeAssistant,
    timer_key: str,
    invalid_value: float,
) -> None:
    """Test all timers are positive whole seconds."""
    result = await _start_behavior_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        BEHAVIOR_DATA | {timer_key: invalid_value},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {timer_key: "positive_integer_required"}


async def test_multiple_nursery_entries_are_allowed(hass: HomeAssistant) -> None:
    """Test that each nursery can have its own config entry."""
    _entry(hass)
    second_data = CONFIG_DATA | {
        CONF_CRY_SENSOR: "binary_sensor.second_nursery_crying",
        CONF_CAMERA: "camera.second_nursery",
        CONF_MEDIA_PLAYER: "media_player.second_nursery",
    }
    hass.states.async_set(second_data[CONF_CRY_SENSOR], "off")
    hass.states.async_set(second_data[CONF_CAMERA], "idle")
    hass.states.async_set(
        second_data[CONF_MEDIA_PLAYER],
        "idle",
        {ATTR_SUPPORTED_FEATURES: MEDIA_PLAYER_FEATURES},
    )

    result = await _start_user_flow(hass)

    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], second_data
    )
    assert result["step_id"] == "behavior"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BEHAVIOR_DATA
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    "shared_key", [CONF_CRY_SENSOR, CONF_CAMERA, CONF_MEDIA_PLAYER]
)
async def test_entry_cannot_share_a_nursery_device(
    hass: HomeAssistant,
    shared_key: str,
) -> None:
    """Two controllers cannot compete for the same physical nursery resource."""
    _entry(hass)
    second_data = CONFIG_DATA | {
        CONF_CRY_SENSOR: "binary_sensor.second_nursery_crying",
        CONF_CAMERA: "camera.second_nursery",
        CONF_MEDIA_PLAYER: "media_player.second_nursery",
        shared_key: CONFIG_DATA[shared_key],
    }
    hass.states.async_set("binary_sensor.second_nursery_crying", "off")
    hass.states.async_set("camera.second_nursery", "idle")
    hass.states.async_set(
        "media_player.second_nursery",
        "idle",
        {ATTR_SUPPORTED_FEATURES: MEDIA_PLAYER_FEATURES},
    )
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], second_data
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {shared_key: "entity_already_configured"}


async def test_concurrent_setup_rechecks_device_ownership(
    hass: HomeAssistant,
) -> None:
    """Only one of two in-flight flows can claim the same nursery devices."""
    first = await _start_behavior_step(hass)
    second = await _start_behavior_step(hass)

    first = await hass.config_entries.flow.async_configure(
        first["flow_id"], BEHAVIOR_DATA
    )
    second = await hass.config_entries.flow.async_configure(
        second["flow_id"], BEHAVIOR_DATA
    )

    assert first["type"] is FlowResultType.CREATE_ENTRY
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "devices_already_configured"


async def test_reconfigure_updates_stable_data_and_preserves_options(
    hass: HomeAssistant,
) -> None:
    """Test reconfigure validates, stores, and reloads stable references."""
    entry = _entry(hass, options=ENTRY_OPTIONS | {CONF_ENABLED: True})
    hass.states.async_set("camera.second_nursery", "idle")
    updated_data = CONFIG_DATA | {CONF_CAMERA: "camera.second_nursery"}

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], updated_data
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == updated_data
    assert entry.options == ENTRY_OPTIONS | {CONF_ENABLED: True}


async def test_reconfigure_rejects_invalid_stable_data(
    hass: HomeAssistant,
) -> None:
    """Test invalid reconfigure input does not mutate the entry."""
    entry = _entry(hass)
    invalid_data = CONFIG_DATA | {CONF_NOTIFY_TARGETS: ["notify.mobile_app_missing"]}

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], invalid_data
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NOTIFY_TARGETS: "notify_action_not_found"}
    assert entry.data == CONFIG_DATA


async def test_options_update_behavior_and_preserve_enabled(
    hass: HomeAssistant,
) -> None:
    """Test behavior options reload the entry without changing enabled."""
    entry = _entry(hass, options=ENTRY_OPTIONS | {CONF_ENABLED: True})
    updated_behavior = BEHAVIOR_DATA | {
        CONF_BASELINE_VOLUME: 25,
        CONF_BOOST_VOLUME: 35,
        CONF_MAX_VOLUME: 45,
        CONF_DEBOUNCE_SECONDS: 20,
    }

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], updated_behavior
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == updated_behavior | {CONF_ENABLED: True}
    assert entry.options == updated_behavior | {CONF_ENABLED: True}


async def test_options_reject_invalid_behavior(hass: HomeAssistant) -> None:
    """Test options use the same safety validation as initial setup."""
    entry = _entry(hass, options=ENTRY_OPTIONS | {CONF_ENABLED: True})
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        BEHAVIOR_DATA | {CONF_BOOST_VOLUME: 101},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "invalid_volume_configuration"}
    assert entry.options == ENTRY_OPTIONS | {CONF_ENABLED: True}
