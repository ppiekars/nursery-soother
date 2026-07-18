"""Tests for the Nursery Soother config-entry lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
)
from homeassistant.config_entries import (
    ConfigEntryError,
    ConfigEntryNotReady,
    ConfigEntryState,
)
from homeassistant.const import (
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_STOP,
    SERVICE_VOLUME_SET,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import ServiceCall, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component

from custom_components.nursery_soother import (
    async_migrate_entry,
    async_setup,
    async_setup_entry,
)
from custom_components.nursery_soother.const import (
    CONF_ATTENTION_SECONDS,
    CONF_AUTOMATIC_OPERATION,
    CONF_BASELINE_VOLUME,
    CONF_CAMERA,
    CONF_CRY_GAP_SECONDS,
    CONF_CRY_SENSOR,
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
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_PROVISIONAL_SECONDS,
    CONF_SETTLING_SECONDS,
    CONF_SOUNDS,
    CONF_TOGGLE_TRIGGERS,
    DEFAULT_OPTIONS,
    DEFAULT_PROVISIONAL_SECONDS,
    DOMAIN,
    ENTRY_VERSION,
    NAME,
    PLATFORMS,
)
from custom_components.nursery_soother.controller import NurserySootherController
from custom_components.nursery_soother.frontend import (
    CARD_MODULE_URL,
    CARD_PATH,
    CARD_URL,
)
from custom_components.nursery_soother.models import ACTIVE_LEVELS, SoothingLevel

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ENTITY_DATA = {
    CONF_CRY_SENSOR: "binary_sensor.nursery_crying",
    CONF_CAMERA: "camera.nursery",
    CONF_MEDIA_PLAYER: "media_player.nursery",
}
SOUND = {
    "media_content_id": "media-source://media_source/local/white-noise.mp3",
    "media_content_type": "audio/mpeg",
}
SOUNDS = {level.value: dict(SOUND) for level in ACTIVE_LEVELS}
CONFIG_DATA = ENTITY_DATA | {
    CONF_SOUNDS: SOUNDS,
    CONF_NOTIFY_TARGETS: [
        "notify.mobile_app_parent_one",
        "notify.mobile_app_parent_two",
    ],
}
ENTITY_COUNT = 14
PREVIOUS_ENTRY_VERSION = 6
TOGGLE_TRIGGERS = [{"platform": "event", "event_type": "nursery_soother_toggle"}]
INCREASE_LEVEL_TRIGGERS = [
    {"platform": "event", "event_type": "nursery_soother_increase_level"}
]
DECREASE_LEVEL_TRIGGERS = [
    {"platform": "event", "event_type": "nursery_soother_decrease_level"}
]
MEDIA_PLAYER_FEATURES = int(
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.STOP
)
VOLUME_KEYS = (
    CONF_BASELINE_VOLUME,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_MAX_VOLUME,
)
TIMER_KEYS = (
    CONF_DEBOUNCE_SECONDS,
    CONF_EVIDENCE_WINDOW_SECONDS,
    CONF_CRY_GAP_SECONDS,
    CONF_PROVISIONAL_SECONDS,
    CONF_LEVEL_UP_SECONDS,
    CONF_SETTLING_SECONDS,
    CONF_ATTENTION_SECONDS,
)


async def test_setup_registers_frontend_card_once(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The card is served and loaded once as an integration-global resource."""
    register_static_paths = AsyncMock()
    add_extra_js_url = Mock()
    monkeypatch.setattr(
        hass,
        "http",
        Mock(async_register_static_paths=register_static_paths),
    )
    monkeypatch.setattr(
        "homeassistant.components.frontend.add_extra_js_url",
        add_extra_js_url,
    )

    assert await async_setup(hass, {}) is True
    register_static_paths.assert_not_awaited()
    add_extra_js_url.assert_not_called()

    mock_component(hass, "frontend")
    assert await async_setup(hass, {}) is True
    assert await async_setup(hass, {}) is True

    register_static_paths.assert_awaited_once()
    (static_paths,) = register_static_paths.await_args.args
    assert len(static_paths) == 1
    static_path = static_paths[0]
    assert static_path.url_path == "/nursery_soother/nursery-soother-card.js"
    assert static_path.url_path == CARD_URL
    assert CARD_PATH.is_file()
    assert static_path.path == str(CARD_PATH)
    assert static_path.cache_headers is False
    assert CARD_MODULE_URL.startswith(f"{CARD_URL}?v=")
    add_extra_js_url.assert_called_once_with(hass, CARD_MODULE_URL)


async def test_frontend_registration_failure_keeps_integration_available(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken optional card must not block the native entity integration."""
    register_static_paths = AsyncMock(side_effect=RuntimeError("route failed"))
    add_extra_js_url = Mock()
    monkeypatch.setattr(
        hass,
        "http",
        Mock(async_register_static_paths=register_static_paths),
    )
    monkeypatch.setattr(
        "homeassistant.components.frontend.add_extra_js_url",
        add_extra_js_url,
    )
    mock_component(hass, "frontend")

    assert await async_setup(hass, {}) is True
    register_static_paths.assert_awaited_once()
    add_extra_js_url.assert_not_called()


async def test_v7_standby_setup_reload_and_unload(hass: HomeAssistant) -> None:
    """A clean entry exposes all fourteen entities and remains safely off."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA | {CONF_TOGGLE_TRIGGERS: TOGGLE_TRIGGERS},
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    entry.add_to_hass(hass)

    assert Platform.SELECT in PLATFORMS
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == ENTRY_VERSION
    assert entry.options == DEFAULT_OPTIONS

    registry_entries = er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    )
    assert len(registry_entries) == ENTITY_COUNT
    assert {item.domain for item in registry_entries} == {
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.NUMBER,
        Platform.SELECT,
        Platform.SENSOR,
        Platform.SWITCH,
    }
    assert all(
        (state := hass.states.get(item.entity_id)) is not None
        and state.state != STATE_UNAVAILABLE
        for item in registry_entries
    )
    state_entity = next(
        item for item in registry_entries if item.unique_id == f"{entry.entry_id}_state"
    )
    level_entity = next(
        item for item in registry_entries if item.unique_id == f"{entry.entry_id}_level"
    )
    recommendation_entity = next(
        item
        for item in registry_entries
        if item.unique_id == f"{entry.entry_id}_recommendation"
    )
    assert hass.states.get(state_entity.entity_id).state == "standby"
    assert hass.states.get(level_entity.entity_id).state == "standby"
    assert hass.states.get(recommendation_entity.entity_id).state == "start"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert all(
        (state := hass.states.get(item.entity_id)) is None
        or state.state == STATE_UNAVAILABLE
        for item in registry_entries
    )


async def test_v7_has_no_legacy_entry_migration(
    hass: HomeAssistant,
) -> None:
    """Only current entries pass migration; v6 is deliberately unsupported."""
    current = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    current.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        Platform.SWITCH,
        DOMAIN,
        f"{current.entry_id}_simulated_cry",
        config_entry=current,
    )

    assert await async_migrate_entry(hass, current) is True
    assert registry.async_get(legacy.entity_id) is not None

    previous = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=PREVIOUS_ENTRY_VERSION,
    )
    previous.add_to_hass(hass)

    assert await async_migrate_entry(hass, previous) is False
    assert previous.version == PREVIOUS_ENTRY_VERSION
    assert previous.options == DEFAULT_OPTIONS


async def test_existing_v7_entry_uses_new_provisional_timeout_default(
    hass: HomeAssistant,
) -> None:
    """A v7 entry created before this option existed remains compatible."""
    previous_options = dict(DEFAULT_OPTIONS)
    previous_options.pop(CONF_PROVISIONAL_SECONDS)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=previous_options,
        version=ENTRY_VERSION,
    )

    controller = NurserySootherController(hass, entry)

    assert controller.settings.provisional_seconds == DEFAULT_PROVISIONAL_SECONDS


async def test_active_entry_setup_reload_and_unload(
    hass: HomeAssistant,
) -> None:
    """A persisted active preference safely restarts at Baseline playback."""
    media_calls: list[ServiceCall] = []

    @callback
    def _record_media(call: ServiceCall) -> None:
        media_calls.append(call)
        if call.service == SERVICE_PLAY_MEDIA:
            current_state = hass.states.get(ENTITY_DATA[CONF_MEDIA_PLAYER])
            attributes = (
                dict(current_state.attributes) if current_state is not None else {}
            )
            attributes[ATTR_MEDIA_CONTENT_ID] = call.data[ATTR_MEDIA_CONTENT_ID]
            hass.states.async_set(
                ENTITY_DATA[CONF_MEDIA_PLAYER],
                "playing",
                attributes,
                context=call.context,
            )
        elif call.service == SERVICE_MEDIA_STOP:
            hass.states.async_set(
                ENTITY_DATA[CONF_MEDIA_PLAYER],
                "idle",
                {ATTR_SUPPORTED_FEATURES: MEDIA_PLAYER_FEATURES},
                context=call.context,
            )

    @callback
    def _ignore_notification(call: ServiceCall) -> None:
        del call

    for service in (SERVICE_VOLUME_SET, SERVICE_PLAY_MEDIA, SERVICE_MEDIA_STOP):
        hass.services.async_register("media_player", service, _record_media)
    for service in ("mobile_app_parent_one", "mobile_app_parent_two"):
        hass.services.async_register("notify", service, _ignore_notification)
    hass.states.async_set(ENTITY_DATA[CONF_CRY_SENSOR], "off")
    hass.states.async_set(ENTITY_DATA[CONF_CAMERA], "idle")
    hass.states.async_set(
        ENTITY_DATA[CONF_MEDIA_PLAYER],
        "idle",
        {ATTR_SUPPORTED_FEATURES: MEDIA_PLAYER_FEATURES},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=DEFAULT_OPTIONS | {CONF_LEVEL: SoothingLevel.BASELINE.value},
        version=ENTRY_VERSION,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert [call.service for call in media_calls] == [
        SERVICE_VOLUME_SET,
        SERVICE_PLAY_MEDIA,
    ]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert [call.service for call in media_calls] == [
        SERVICE_VOLUME_SET,
        SERVICE_PLAY_MEDIA,
        SERVICE_MEDIA_STOP,
        SERVICE_VOLUME_SET,
        SERVICE_PLAY_MEDIA,
    ]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert media_calls[-1].service == SERVICE_MEDIA_STOP


@pytest.mark.parametrize(
    ("config_data", "invalid_config_key"),
    [
        ({}, CONF_CRY_SENSOR),
        (CONFIG_DATA | {CONF_CAMERA: None}, CONF_CAMERA),
        (
            CONFIG_DATA | {CONF_MEDIA_PLAYER: "0123456789abcdef0123456789abcdef"},
            CONF_MEDIA_PLAYER,
        ),
    ],
)
async def test_setup_rejects_missing_or_malformed_entity_ids(
    hass: HomeAssistant,
    config_data: dict[str, object],
    invalid_config_key: str,
) -> None:
    """Stored data must contain canonical entity IDs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=config_data,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)
    assert error.value.translation_key == "invalid_entity"
    assert error.value.translation_placeholders == {"config_key": invalid_config_key}


@pytest.mark.parametrize(
    ("config_key", "invalid_entity_id", "expected_domain"),
    [
        (CONF_CRY_SENSOR, "sensor.nursery_crying", "binary_sensor"),
        (CONF_CAMERA, "image.nursery", "camera"),
        (CONF_MEDIA_PLAYER, "switch.nursery_speaker", "media_player"),
    ],
)
async def test_setup_rejects_wrong_entity_domain(
    hass: HomeAssistant,
    config_key: str,
    invalid_entity_id: str,
    expected_domain: str,
) -> None:
    """Stored selectors cannot bypass their entity-domain boundary."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA | {config_key: invalid_entity_id},
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)
    assert error.value.translation_key == "invalid_entity_domain"
    assert error.value.translation_placeholders == {
        "config_key": config_key,
        "expected_domain": expected_domain,
    }


@pytest.mark.parametrize(
    "options",
    [
        *(DEFAULT_OPTIONS | {key: True} for key in VOLUME_KEYS),
        DEFAULT_OPTIONS | {CONF_BASELINE_VOLUME: 16},
        DEFAULT_OPTIONS | {CONF_LEVEL_1_VOLUME: 21},
        DEFAULT_OPTIONS | {CONF_LEVEL_2_VOLUME: 26},
        DEFAULT_OPTIONS | {CONF_LEVEL_3_VOLUME: 31},
        DEFAULT_OPTIONS | {CONF_LEVEL_4_VOLUME: 41},
        DEFAULT_OPTIONS | {CONF_MAX_VOLUME: 101},
        *(DEFAULT_OPTIONS | {key: 0} for key in TIMER_KEYS),
        *(DEFAULT_OPTIONS | {key: 1.5} for key in TIMER_KEYS),
        *(DEFAULT_OPTIONS | {key: True} for key in TIMER_KEYS),
        DEFAULT_OPTIONS | {CONF_AUTOMATIC_OPERATION: "yes"},
        DEFAULT_OPTIONS | {CONF_LEVEL_LOCK: "yes"},
        DEFAULT_OPTIONS | {CONF_LEVEL: "boost"},
        DEFAULT_OPTIONS | {CONF_LEVEL: 1},
    ],
)
async def test_setup_rejects_unsafe_persisted_options(
    hass: HomeAssistant,
    options: dict[str, object],
) -> None:
    """Every volume, timer, mode, and level is revalidated during setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=options,
        version=ENTRY_VERSION,
    )
    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)
    assert error.value.translation_key == "invalid_options"


@pytest.mark.parametrize(
    "data",
    [
        ENTITY_DATA | {CONF_NOTIFY_TARGETS: ["notify.mobile_app_parent"]},
        CONFIG_DATA
        | {
            CONF_SOUNDS: {
                key: value
                for key, value in SOUNDS.items()
                if key != SoothingLevel.LEVEL_4.value
            }
        },
        CONFIG_DATA
        | {CONF_SOUNDS: SOUNDS | {SoothingLevel.STANDBY.value: dict(SOUND)}},
        CONFIG_DATA
        | {
            CONF_SOUNDS: SOUNDS
            | {
                SoothingLevel.LEVEL_2.value: {
                    "media_content_id": "media-source://media_source/local/video.mp4",
                    "media_content_type": "video/mp4",
                }
            }
        },
        CONFIG_DATA
        | {
            CONF_SOUNDS: SOUNDS
            | {
                SoothingLevel.BASELINE.value: {
                    "media_content_id": "media-source://radio_browser/example",
                    "media_content_type": "audio/mpeg",
                }
            }
        },
        CONFIG_DATA | {CONF_NOTIFY_TARGETS: []},
        CONFIG_DATA | {CONF_NOTIFY_TARGETS: ["notify.not_a_mobile_app"]},
        CONFIG_DATA | {CONF_NOTIFY_TARGETS: ["notify.mobile_app_"]},
        CONFIG_DATA | {CONF_NOTIFY_TARGETS: ["notify.mobile_app_parent phone"]},
    ],
)
async def test_setup_rejects_incomplete_or_invalid_functional_data(
    hass: HomeAssistant,
    data: dict[str, object],
) -> None:
    """All five active sound profiles and parent targets are mandatory."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=data,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)
    assert error.value.translation_key == "invalid_configuration"


@pytest.mark.parametrize(
    "action_trigger_data",
    [
        {CONF_TOGGLE_TRIGGERS: []},
        {CONF_INCREASE_LEVEL_TRIGGERS: "not-a-trigger-list"},
        {CONF_DECREASE_LEVEL_TRIGGERS: [1]},
        {CONF_TOGGLE_TRIGGERS: [{}]},
    ],
)
async def test_setup_rejects_invalid_persisted_action_triggers(
    hass: HomeAssistant,
    action_trigger_data: dict[str, object],
) -> None:
    """Stored action triggers must be non-empty structurally valid lists."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA | action_trigger_data,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )

    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)
    assert error.value.translation_key == "invalid_action_triggers"


async def test_setup_rejects_persisted_action_trigger_reuse(
    hass: HomeAssistant,
) -> None:
    """One stored trigger cannot attach callbacks for two actions."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA
        | {
            CONF_TOGGLE_TRIGGERS: TOGGLE_TRIGGERS,
            CONF_DECREASE_LEVEL_TRIGGERS: TOGGLE_TRIGGERS,
        },
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )

    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)
    assert error.value.translation_key == "action_trigger_reused"


async def test_setup_rejects_duplicate_trigger_in_one_action(
    hass: HomeAssistant,
) -> None:
    """Stored data cannot attach one action callback twice."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA | {CONF_TOGGLE_TRIGGERS: TOGGLE_TRIGGERS * 2},
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )

    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)
    assert error.value.translation_key == "action_trigger_reused"


async def test_setup_rejects_persisted_device_overlap(
    hass: HomeAssistant,
) -> None:
    """Stored entries cannot bypass one-controller-per-device ownership."""
    first = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    first.add_to_hass(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, second)
    assert error.value.translation_key == "duplicate_devices"


async def test_failed_setup_with_incomplete_rollback_requests_retry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A controller that cannot stop during setup must not be marked loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    platforms_unloaded = False
    runtime_aborted = False

    async def _forward_setups(*args: object) -> None:
        del args

    async def _fail_start(self: NurserySootherController) -> None:
        del self
        error_message = "controller startup failed"
        raise RuntimeError(error_message)

    async def _incomplete_shutdown(self: NurserySootherController) -> bool:
        del self
        return False

    async def _unload_platforms(*args: object) -> bool:
        nonlocal platforms_unloaded
        del args
        platforms_unloaded = True
        return True

    async def _abort_startup(self: NurserySootherController) -> None:
        nonlocal runtime_aborted
        del self
        runtime_aborted = True

    monkeypatch.setattr(
        hass.config_entries, "async_forward_entry_setups", _forward_setups
    )
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", _unload_platforms
    )
    monkeypatch.setattr(NurserySootherController, "async_start", _fail_start)
    monkeypatch.setattr(
        NurserySootherController, "async_shutdown", _incomplete_shutdown
    )
    monkeypatch.setattr(NurserySootherController, "async_abort_startup", _abort_startup)

    with pytest.raises(ConfigEntryNotReady) as error:
        await async_setup_entry(hass, entry)

    assert isinstance(error.value.__cause__, RuntimeError)
    assert platforms_unloaded is True
    assert runtime_aborted is True


async def test_setup_preserves_original_error_when_platform_cleanup_fails(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup errors must not mask the setup failure after a safe shutdown."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )

    async def _forward_setups(*args: object) -> None:
        del args

    async def _fail_start(self: NurserySootherController) -> None:
        del self
        error_message = "original controller startup failure"
        raise RuntimeError(error_message)

    async def _complete_shutdown(self: NurserySootherController) -> bool:
        del self
        return True

    async def _fail_platform_cleanup(*args: object) -> bool:
        del args
        error_message = "platform cleanup failed"
        raise RuntimeError(error_message)

    monkeypatch.setattr(
        hass.config_entries, "async_forward_entry_setups", _forward_setups
    )
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", _fail_platform_cleanup
    )
    monkeypatch.setattr(NurserySootherController, "async_start", _fail_start)
    monkeypatch.setattr(NurserySootherController, "async_shutdown", _complete_shutdown)

    with pytest.raises(RuntimeError, match="original controller startup failure"):
        await async_setup_entry(hass, entry)
