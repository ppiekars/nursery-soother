"""Tests for Nursery Soother entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.number import NumberMode
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nursery_soother import (
    binary_sensor as binary_sensor_platform,
)
from custom_components.nursery_soother import button as button_platform
from custom_components.nursery_soother import number as number_platform
from custom_components.nursery_soother import sensor as sensor_platform
from custom_components.nursery_soother import switch as switch_platform
from custom_components.nursery_soother.const import (
    CONF_BASELINE_VOLUME,
    CONF_BOOST_VOLUME,
    CONF_MAX_VOLUME,
    DOMAIN,
    NAME,
)
from custom_components.nursery_soother.models import Recommendation, SootherState

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity

PERCENTAGE_MAX = 100


@dataclass
class _FakeSettings:
    baseline_volume: float = 20.0
    boost_volume: float = 30.0
    max_volume: float = 40.0


class _FakeController:
    """Small observable implementation of the entity/controller contract."""

    def __init__(self) -> None:
        self.state = SootherState.BASELINE
        self.recommendation = Recommendation.OBSERVE
        self.attention_required = False
        self.enabled = False
        self.settings = _FakeSettings()
        self.configured = True
        self.dependencies_available = True
        self.calls: list[tuple[str, object | None]] = []
        self.listeners: list[Callable[[], None]] = []

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a state listener and return its unsubscribe callback."""
        self.listeners.append(listener)

        def _unsubscribe() -> None:
            self.listeners.remove(listener)

        return _unsubscribe

    def emit(self) -> None:
        """Notify a snapshot of registered listeners."""
        for listener in tuple(self.listeners):
            listener()

    async def async_set_enabled(self, *, enabled: bool) -> None:
        """Record and apply an enabled change."""
        self.calls.append(("set_enabled", enabled))
        self.enabled = enabled
        self.emit()

    async def async_boost(self) -> None:
        """Record a boost request."""
        self.calls.append(("boost", None))

    async def async_baseline(self) -> None:
        """Record a baseline request."""
        self.calls.append(("baseline", None))

    async def async_acknowledge(self) -> None:
        """Record an acknowledgement."""
        self.calls.append(("acknowledge", None))

    async def async_stop(self) -> None:
        """Record a stop request."""
        self.calls.append(("stop", None))

    async def async_set_volume(self, key: str, value: float) -> None:
        """Record and apply a volume setting."""
        self.calls.append((key, value))
        setattr(self.settings, key, value)
        self.emit()


@pytest.fixture
def controller() -> _FakeController:
    """Return a fresh fake controller."""
    return _FakeController()


@pytest.fixture
def entry(controller: _FakeController) -> MockConfigEntry:
    """Return a config entry carrying the fake runtime controller."""
    config_entry = MockConfigEntry(domain=DOMAIN, title=NAME)
    config_entry.runtime_data = controller
    return config_entry


async def _all_entities(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> list[Entity]:
    """Set up every entity platform into one collection."""
    entities: list[Entity] = []

    def _add_entities(new_entities: Iterable[Entity]) -> None:
        entities.extend(new_entities)

    for setup_entry in (
        sensor_platform.async_setup_entry,
        binary_sensor_platform.async_setup_entry,
        switch_platform.async_setup_entry,
        number_platform.async_setup_entry,
        button_platform.async_setup_entry,
    ):
        await setup_entry(hass, entry, _add_entities)

    return entities


async def test_shared_metadata_availability_and_listener_lifecycle(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Entities share stable metadata and unsubscribe from controller changes."""
    entities = await _all_entities(hass, entry)

    expected_unique_ids = {
        f"{entry.entry_id}_{key}"
        for key in {
            "state",
            "recommendation",
            "attention_required",
            "enabled",
            CONF_BASELINE_VOLUME,
            CONF_BOOST_VOLUME,
            CONF_MAX_VOLUME,
            "boost",
            "baseline",
            "acknowledge",
            "stop",
        }
    }
    assert len(entities) == len(expected_unique_ids)
    assert {entity.unique_id for entity in entities} == expected_unique_ids
    for entity in entities:
        assert entity.should_poll is False
        assert entity.has_entity_name is True
        assert entity.available is True
        assert entity.device_info == {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "entry_type": DeviceEntryType.SERVICE,
            "manufacturer": NAME,
            "model": NAME,
            "name": NAME,
        }

    # Controller entities remain usable during an outage so parents can see
    # the fail-safe state, acknowledge it, or stop the integration.
    controller.dependencies_available = False
    controller.configured = False
    assert all(entity.available for entity in entities)

    entity = entities[0]
    write_state = Mock()
    entity.async_write_ha_state = write_state
    entity.hass = hass
    entity.entity_id = "sensor.listener_test"
    await entity.async_added_to_hass()
    assert len(controller.listeners) == 1

    controller.emit()
    write_state.assert_called_once_with()

    await entity.async_remove(force_remove=True)
    assert controller.listeners == []


async def test_enum_sensors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """State and recommendation sensors expose complete enum metadata."""
    entities: list[sensor_platform.NurserySootherSensor] = []
    await sensor_platform.async_setup_entry(hass, entry, entities.extend)
    by_key = {entity.entity_description.key: entity for entity in entities}

    assert by_key["state"].native_value == SootherState.BASELINE.value
    assert by_key["state"].device_class is SensorDeviceClass.ENUM
    assert by_key["state"].options == [state.value for state in SootherState]
    assert by_key["state"].entity_description.translation_key == "state"

    assert by_key["recommendation"].native_value == Recommendation.OBSERVE.value
    assert by_key["recommendation"].device_class is SensorDeviceClass.ENUM
    assert by_key["recommendation"].options == [
        recommendation.value for recommendation in Recommendation
    ]
    assert (
        by_key["recommendation"].entity_description.translation_key == "recommendation"
    )

    controller.state = SootherState.ATTENTION_REQUIRED
    controller.recommendation = Recommendation.ATTEND
    assert by_key["state"].native_value == SootherState.ATTENTION_REQUIRED.value
    assert by_key["recommendation"].native_value == Recommendation.ATTEND.value


async def test_attention_required_binary_sensor(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Attention-required state is exposed as a problem binary sensor."""
    entities: list[
        binary_sensor_platform.NurserySootherAttentionRequiredBinarySensor
    ] = []
    await binary_sensor_platform.async_setup_entry(hass, entry, entities.extend)
    entity = entities[0]

    assert entity.entity_description.translation_key == "attention_required"
    assert entity.device_class is BinarySensorDeviceClass.PROBLEM
    assert entity.is_on is False

    controller.attention_required = True
    assert entity.is_on is True


async def test_enabled_switch_delegates_to_controller(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """The config switch delegates enable and disable operations."""
    entities: list[switch_platform.NurserySootherEnabledSwitch] = []
    await switch_platform.async_setup_entry(hass, entry, entities.extend)
    entity = entities[0]

    assert entity.entity_description.translation_key == "enabled"
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.is_on is False

    await entity.async_turn_on()
    assert entity.is_on is True
    await entity.async_turn_off()
    assert entity.is_on is False
    assert controller.calls == [
        ("set_enabled", True),
        ("set_enabled", False),
    ]


async def test_volume_numbers_delegate_safe_settings(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Volume numbers use percentages and delegate validation to the controller."""
    entities: list[number_platform.NurserySootherVolumeNumber] = []
    await number_platform.async_setup_entry(hass, entry, entities.extend)
    by_key = {entity.entity_description.key: entity for entity in entities}

    expected = {
        CONF_BASELINE_VOLUME: (20.0, 21.0, "baseline_volume"),
        CONF_BOOST_VOLUME: (30.0, 31.0, "boost_volume"),
        CONF_MAX_VOLUME: (40.0, 41.0, "max_volume"),
    }
    for key, (initial, updated, translation_key) in expected.items():
        entity = by_key[key]
        assert entity.native_value == initial
        assert entity.native_min_value == 0
        assert entity.native_max_value == PERCENTAGE_MAX
        assert entity.native_step == 1
        assert entity.native_unit_of_measurement == PERCENTAGE
        assert entity.mode is NumberMode.SLIDER
        assert entity.entity_category is EntityCategory.CONFIG
        assert entity.entity_description.translation_key == translation_key

        await entity.async_set_native_value(updated)
        assert entity.native_value == updated

    assert controller.calls == [
        (CONF_BASELINE_VOLUME, 21.0),
        (CONF_BOOST_VOLUME, 31.0),
        (CONF_MAX_VOLUME, 41.0),
    ]


async def test_action_buttons_delegate_to_controller(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Each action button invokes exactly one controller command."""
    entities: list[button_platform.NurserySootherActionButton] = []
    await button_platform.async_setup_entry(hass, entry, entities.extend)

    for entity in entities:
        assert (
            entity.entity_description.translation_key == entity.entity_description.key
        )
        assert entity.entity_category is None
        await entity.async_press()

    assert controller.calls == [
        ("boost", None),
        ("baseline", None),
        ("acknowledge", None),
        ("stop", None),
    ]
