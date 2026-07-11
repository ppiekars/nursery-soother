"""Tests for the Nursery Soother config-entry lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryError, ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nursery_soother import async_setup_entry
from custom_components.nursery_soother.const import (
    CONF_CAMERA,
    CONF_CRY_SENSOR,
    CONF_MEDIA_PLAYER,
    DOMAIN,
    NAME,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CONFIG_DATA = {
    CONF_CRY_SENSOR: "binary_sensor.nursery_crying",
    CONF_CAMERA: "camera.nursery",
    CONF_MEDIA_PLAYER: "media_player.nursery",
}


async def test_setup_reload_and_unload(hass: HomeAssistant) -> None:
    """Test that the inert entry loads, reloads, and unloads cleanly."""
    entry = MockConfigEntry(domain=DOMAIN, title=NAME, data=CONFIG_DATA)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


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
