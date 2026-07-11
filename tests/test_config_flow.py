"""Tests for the Nursery Soother config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er

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


async def test_user_flow(hass: HomeAssistant) -> None:
    """Test configuring the three nursery entities."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONFIG_DATA
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == CONFIG_DATA


async def test_user_flow_resolves_entity_registry_uuid(hass: HomeAssistant) -> None:
    """Test selector UUIDs are stored as canonical entity IDs."""
    registry_entry = er.async_get(hass).async_get_or_create(
        domain="binary_sensor",
        platform="test",
        unique_id="nursery_crying",
        suggested_object_id="nursery_crying",
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        CONFIG_DATA | {CONF_CRY_SENSOR: registry_entry.id},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == CONFIG_DATA


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
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

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
    """Test unknown selector UUIDs return a form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        CONFIG_DATA | {CONF_CRY_SENSOR: "0123456789abcdef0123456789abcdef"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_CRY_SENSOR: "invalid_entity"}


@pytest.mark.parametrize(
    ("config_key", "invalid_entity_id"),
    [
        (CONF_CRY_SENSOR, "sensor.nursery_crying"),
        (CONF_CAMERA, "image.nursery"),
        (CONF_MEDIA_PLAYER, "switch.nursery_speaker"),
    ],
)
async def test_user_flow_rejects_wrong_domain(
    hass: HomeAssistant,
    config_key: str,
    invalid_entity_id: str,
) -> None:
    """Test that each selector rejects an entity from the wrong domain."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    invalid_data = CONFIG_DATA | {config_key: invalid_entity_id}
    with pytest.raises(InvalidData) as error:
        await hass.config_entries.flow.async_configure(result["flow_id"], invalid_data)

    assert error.value.path == [config_key]
    assert config_key in error.value.schema_errors


async def test_duplicate_aborts(hass: HomeAssistant) -> None:
    """Test that Nursery Soother allows only one config entry."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    await hass.config_entries.flow.async_configure(first["flow_id"], CONFIG_DATA)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
