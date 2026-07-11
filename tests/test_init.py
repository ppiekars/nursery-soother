"""Tests for the Nursery Soother config-entry lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
)
from homeassistant.config_entries import ConfigEntryError, ConfigEntryState
from homeassistant.const import (
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_STOP,
    SERVICE_VOLUME_SET,
    STATE_UNAVAILABLE,
)
from homeassistant.core import ServiceCall, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nursery_soother import async_setup_entry
from custom_components.nursery_soother.const import (
    CONF_BASELINE_VOLUME,
    CONF_CAMERA,
    CONF_CRY_SENSOR,
    CONF_DEBOUNCE_SECONDS,
    CONF_ENABLED,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_WHITE_NOISE,
    DEFAULT_OPTIONS,
    DOMAIN,
    ENTRY_VERSION,
    NAME,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CONFIG_DATA = {
    CONF_CRY_SENSOR: "binary_sensor.nursery_crying",
    CONF_CAMERA: "camera.nursery",
    CONF_MEDIA_PLAYER: "media_player.nursery",
}
ENTITY_COUNT = 12
FUNCTIONAL_CONFIG_DATA = CONFIG_DATA | {
    CONF_WHITE_NOISE: {
        "media_content_id": "media-source://media_source/local/white-noise.mp3",
        "media_content_type": "audio/mpeg",
    },
    CONF_NOTIFY_TARGETS: [
        "notify.mobile_app_parent_one",
        "notify.mobile_app_parent_two",
    ],
}


async def test_setup_reload_and_unload(hass: HomeAssistant) -> None:
    """Test safe foundation migration plus entity reload and unload."""
    entry = MockConfigEntry(domain=DOMAIN, title=NAME, data=CONFIG_DATA)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == ENTRY_VERSION
    assert entry.options == DEFAULT_OPTIONS
    assert entry.options[CONF_ENABLED] is False

    registry_entries = er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    )
    assert len(registry_entries) == ENTITY_COUNT
    assert all(
        (state := hass.states.get(registry_entry.entity_id)) is not None
        and state.state != STATE_UNAVAILABLE
        for registry_entry in registry_entries
    )
    state_registry_entry = next(
        registry_entry
        for registry_entry in registry_entries
        if registry_entry.unique_id == f"{entry.entry_id}_state"
    )
    recommendation_registry_entry = next(
        registry_entry
        for registry_entry in registry_entries
        if registry_entry.unique_id == f"{entry.entry_id}_recommendation"
    )
    assert hass.states.get(state_registry_entry.entity_id).state == "disabled"
    assert hass.states.get(recommendation_registry_entry.entity_id).state == "configure"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert all(
        (state := hass.states.get(registry_entry.entity_id)) is None
        or state.state == STATE_UNAVAILABLE
        for registry_entry in registry_entries
    )


async def test_v2_migration_replaces_renamed_simulator_switch_with_button(
    hass: HomeAssistant,
) -> None:
    """The 0.2.2 stateful simulator is removed even after a user rename."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=2,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy_unique_id = f"{entry.entry_id}_simulated_cry"
    legacy = registry.async_get_or_create(
        "switch",
        DOMAIN,
        legacy_unique_id,
        config_entry=entry,
    )
    registry.async_update_entity(
        legacy.entity_id,
        new_entity_id="switch.renamed_legacy_cry_test",
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == ENTRY_VERSION
    assert registry.async_get_entity_id("switch", DOMAIN, legacy_unique_id) is None
    registry_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(registry_entries) == ENTITY_COUNT
    assert any(
        item.domain == "button"
        and item.unique_id == f"{entry.entry_id}_simulate_cry_event"
        for item in registry_entries
    )

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_functional_enabled_entry_setup_reload_and_unload(
    hass: HomeAssistant,
) -> None:
    """Exercise the complete config-entry lifecycle with real controller effects."""
    media_calls: list[ServiceCall] = []

    @callback
    def _record_media(call: ServiceCall) -> None:
        media_calls.append(call)
        if call.service == SERVICE_PLAY_MEDIA:
            current_state = hass.states.get(CONFIG_DATA[CONF_MEDIA_PLAYER])
            attributes = (
                dict(current_state.attributes) if current_state is not None else {}
            )
            attributes[ATTR_MEDIA_CONTENT_ID] = call.data[ATTR_MEDIA_CONTENT_ID]
            hass.states.async_set(
                CONFIG_DATA[CONF_MEDIA_PLAYER],
                "playing",
                attributes,
                context=call.context,
            )

    @callback
    def _ignore_notification(call: ServiceCall) -> None:
        del call

    for service in (SERVICE_VOLUME_SET, SERVICE_PLAY_MEDIA, SERVICE_MEDIA_STOP):
        hass.services.async_register("media_player", service, _record_media)
    for service in ("mobile_app_parent_one", "mobile_app_parent_two"):
        hass.services.async_register("notify", service, _ignore_notification)
    hass.states.async_set(CONFIG_DATA[CONF_CRY_SENSOR], "off")
    hass.states.async_set(CONFIG_DATA[CONF_CAMERA], "idle")
    hass.states.async_set(
        CONFIG_DATA[CONF_MEDIA_PLAYER],
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.STOP
            )
        },
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=FUNCTIONAL_CONFIG_DATA,
        options=DEFAULT_OPTIONS | {CONF_ENABLED: True},
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
    config_data: dict[str, str | None],
    invalid_config_key: str,
) -> None:
    """Test stored data must contain canonical entity IDs."""
    entry = MockConfigEntry(domain=DOMAIN, title=NAME, data=config_data)

    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)

    assert error.value.translation_domain == DOMAIN
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
async def test_setup_rejects_wrong_domain(
    hass: HomeAssistant,
    config_key: str,
    invalid_entity_id: str,
    expected_domain: str,
) -> None:
    """Test setup validates stored entity domains independently of the flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA | {config_key: invalid_entity_id},
    )

    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)

    assert error.value.translation_domain == DOMAIN
    assert error.value.translation_key == "invalid_entity_domain"
    assert error.value.translation_placeholders == {
        "config_key": config_key,
        "expected_domain": expected_domain,
    }


@pytest.mark.parametrize(
    "options",
    [
        DEFAULT_OPTIONS | {CONF_BASELINE_VOLUME: 50},
        DEFAULT_OPTIONS | {CONF_DEBOUNCE_SECONDS: 0},
        DEFAULT_OPTIONS | {CONF_DEBOUNCE_SECONDS: 1.5},
        DEFAULT_OPTIONS | {CONF_BASELINE_VOLUME: "loud"},
        DEFAULT_OPTIONS | {CONF_ENABLED: "yes"},
    ],
)
async def test_setup_rejects_unsafe_persisted_options(
    hass: HomeAssistant,
    options: dict[str, object],
) -> None:
    """Stored data cannot bypass volume ordering or positive timers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=options,
        version=ENTRY_VERSION,
    )

    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)

    assert error.value.translation_domain == DOMAIN
    assert error.value.translation_key == "invalid_options"


@pytest.mark.parametrize(
    "functional_data",
    [
        {CONF_WHITE_NOISE: {}},
        {
            CONF_WHITE_NOISE: {
                "media_content_id": "media-source://media_source/local/noise.mp3",
                "media_content_type": "audio/mpeg",
            },
            CONF_NOTIFY_TARGETS: [],
        },
        {
            CONF_WHITE_NOISE: {
                "media_content_id": "media-source://media_source/local/noise.mp3",
                "media_content_type": "audio/mpeg",
            },
            CONF_NOTIFY_TARGETS: ["notify.not_a_mobile_app"],
        },
        {
            CONF_WHITE_NOISE: {
                "media_content_id": "media-source://media_source/local/video.mp4",
                "media_content_type": "video/mp4",
            },
            CONF_NOTIFY_TARGETS: ["notify.mobile_app_parent"],
        },
        {
            CONF_WHITE_NOISE: {
                "media_content_id": " ",
                "media_content_type": "audio/mpeg",
            },
            CONF_NOTIFY_TARGETS: ["notify.mobile_app_parent"],
        },
        {
            CONF_WHITE_NOISE: {
                "media_content_id": "media-source://radio_browser/example",
                "media_content_type": "audio/mpeg",
            },
            CONF_NOTIFY_TARGETS: ["notify.mobile_app_parent"],
        },
    ],
)
async def test_setup_rejects_partial_or_invalid_functional_data(
    hass: HomeAssistant,
    functional_data: dict[str, object],
) -> None:
    """Persisted functional data must be complete if any of it is present."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA | functional_data,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )

    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, entry)

    assert error.value.translation_domain == DOMAIN
    assert error.value.translation_key == "invalid_configuration"


async def test_setup_rejects_persisted_device_overlap(
    hass: HomeAssistant,
) -> None:
    """Stored entries cannot bypass the one-controller-per-device invariant."""
    first = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=FUNCTIONAL_CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    first.add_to_hass(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=FUNCTIONAL_CONFIG_DATA,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )

    with pytest.raises(ConfigEntryError) as error:
        await async_setup_entry(hass, second)

    assert error.value.translation_domain == DOMAIN
    assert error.value.translation_key == "duplicate_devices"
