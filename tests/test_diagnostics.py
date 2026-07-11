"""Tests for privacy-safe Nursery Soother diagnostics."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from homeassistant.components.diagnostics import REDACTED
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nursery_soother.const import (
    CONF_CAMERA,
    CONF_CRY_SENSOR,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_WHITE_NOISE,
    DEFAULT_OPTIONS,
    DOMAIN,
    ENTRY_VERSION,
    NAME,
)
from custom_components.nursery_soother.controller import NurserySootherController
from custom_components.nursery_soother.diagnostics import (
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_diagnostics_redact_every_external_reference(
    hass: HomeAssistant,
) -> None:
    """Diagnostics expose policy state but no entity, media, or parent details."""
    secrets = {
        CONF_CRY_SENSOR: "binary_sensor.private_nursery_cry",
        CONF_CAMERA: "camera.private_nursery",
        CONF_MEDIA_PLAYER: "media_player.private_nursery",
        CONF_WHITE_NOISE: {
            "media_content_id": "media-source://media_source/local/secret.mp3",
            "media_content_type": "audio/mpeg",
        },
        CONF_NOTIFY_TARGETS: [
            "notify.mobile_app_private_parent_one",
            "notify.mobile_app_private_parent_two",
        ],
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=secrets,
        options=DEFAULT_OPTIONS,
        version=ENTRY_VERSION,
    )
    entry.runtime_data = NurserySootherController(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry_data"] == dict.fromkeys(secrets, REDACTED)
    assert diagnostics["entry_options"] == DEFAULT_OPTIONS
    assert diagnostics["runtime"]["configured"] is True
    serialized = json.dumps(diagnostics)
    for sensitive_value in (
        "private_nursery",
        "secret.mp3",
        "private_parent_one",
        "private_parent_two",
    ):
        assert sensitive_value not in serialized
