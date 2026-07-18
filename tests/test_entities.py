"""Tests for Nursery Soother entities."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
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
from custom_components.nursery_soother import select as select_platform
from custom_components.nursery_soother import sensor as sensor_platform
from custom_components.nursery_soother import switch as switch_platform
from custom_components.nursery_soother.const import (
    CONF_AUTOMATIC_OPERATION,
    CONF_BASELINE_PREVIEW,
    CONF_BASELINE_VOLUME,
    CONF_LEVEL,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_LEVEL_LOCK,
    CONF_MAX_VOLUME,
    DOMAIN,
    NAME,
    SERVICE_SIMULATE_CRY_EVENT,
)
from custom_components.nursery_soother.models import (
    ACTIVE_LEVELS,
    Recommendation,
    SootherState,
    SoothingLevel,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity

PERCENTAGE_MAX = 100
ENTITY_COUNT = 14


@dataclass
class _FakeSettings:
    baseline_volume: float = 10.0
    level_1_volume: float = 15.0
    level_2_volume: float = 20.0
    level_3_volume: float = 25.0
    level_4_volume: float = 30.0
    max_volume: float = 40.0


class _FakeController:
    """Small observable implementation of the entity/controller contract."""

    def __init__(self) -> None:
        self.level = SoothingLevel.STANDBY
        self.automatic = False
        self.baseline_previewing = False
        self.locked = False
        self.state = SootherState.STANDBY
        self.recommendation = Recommendation.START
        self.suggested_level: SoothingLevel | None = None
        self.attention_required = False
        self.status_attributes = {
            "explanation": "standby",
            "evidence": {
                "events": 0,
                "active_seconds": 0.0,
                "event_threshold": 2,
                "active_seconds_threshold": 8.0,
                "sensor_active": False,
                "observed_at": "2026-07-11T12:00:00+00:00",
            },
            "countdowns": {},
            "next_countdown": None,
            "next_countdown_at": None,
        }
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

    async def async_set_level(self, level: SoothingLevel) -> None:
        """Record and apply an exact output-level change."""
        self.calls.append((CONF_LEVEL, level))
        self.level = level
        self.emit()

    async def async_set_automatic(self, *, enabled: bool) -> None:
        """Record and apply an operating-mode change."""
        self.calls.append((CONF_AUTOMATIC_OPERATION, enabled))
        self.automatic = enabled
        self.emit()

    async def async_set_locked(self, *, locked: bool) -> None:
        """Record and apply a level-lock change."""
        self.calls.append((CONF_LEVEL_LOCK, locked))
        self.locked = locked
        self.emit()

    async def async_set_baseline_preview(self, *, enabled: bool) -> None:
        """Record and apply an independent Baseline playback change."""
        self.calls.append((CONF_BASELINE_PREVIEW, enabled))
        self.baseline_previewing = enabled
        self.emit()

    async def async_simulate_cry_event(self) -> None:
        """Record one synthetic cry event."""
        self.calls.append((SERVICE_SIMULATE_CRY_EVENT, None))

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
        select_platform.async_setup_entry,
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
    """The fourteen entities share stable metadata and observable updates."""
    entities = await _all_entities(hass, entry)

    expected_keys = {
        "state",
        "recommendation",
        "attention_required",
        CONF_LEVEL,
        CONF_AUTOMATIC_OPERATION,
        CONF_BASELINE_PREVIEW,
        CONF_LEVEL_LOCK,
        SERVICE_SIMULATE_CRY_EVENT,
        CONF_BASELINE_VOLUME,
        CONF_LEVEL_1_VOLUME,
        CONF_LEVEL_2_VOLUME,
        CONF_LEVEL_3_VOLUME,
        CONF_LEVEL_4_VOLUME,
        CONF_MAX_VOLUME,
    }
    assert len(entities) == ENTITY_COUNT
    assert {entity.unique_id for entity in entities} == {
        f"{entry.entry_id}_{key}" for key in expected_keys
    }
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

    controller.dependencies_available = False
    controller.configured = False
    assert all(entity.available for entity in entities)

    entity = entities[0]
    write_state = Mock()
    entity.async_write_ha_state = write_state
    entity.hass = hass
    entity.entity_id = "sensor.listener_test"
    await entity.async_added_to_hass()
    controller.emit()
    write_state.assert_called_once_with()
    await entity.async_remove(force_remove=True)
    assert controller.listeners == []


async def test_level_select_delegates_exact_enum(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """The primary control exposes Standby and every ordered active level."""
    entities: list[select_platform.NurserySootherLevelSelect] = []
    await select_platform.async_setup_entry(hass, entry, entities.extend)
    entity = entities[0]

    assert entity.options == [level.value for level in SoothingLevel]
    assert entity.current_option == SoothingLevel.STANDBY.value
    assert entity.entity_category is None
    assert entity.entity_description.translation_key == "level"

    await entity.async_select_option(SoothingLevel.LEVEL_3.value)
    assert entity.current_option == SoothingLevel.LEVEL_3.value
    assert controller.calls == [(CONF_LEVEL, SoothingLevel.LEVEL_3)]


def test_level_helpers_do_not_skip_or_cross_active_bounds() -> None:
    """Automatic traversal moves exactly one active level at a time."""
    assert SoothingLevel.STANDBY.next_active() is SoothingLevel.BASELINE
    assert SoothingLevel.STANDBY.previous_active() is None
    assert SoothingLevel.BASELINE.previous_active() is None
    assert SoothingLevel.LEVEL_4.next_active() is None
    for lower, upper in pairwise(ACTIVE_LEVELS):
        assert lower.next_active() is upper
        assert upper.previous_active() is lower


async def test_automatic_switch_delegates_to_controller(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Automatic operation is a primary mode control, not an enable switch."""
    entities: list[switch_platform.NurserySootherAutomaticSwitch] = []
    await switch_platform.async_setup_entry(hass, entry, entities.extend)
    automatic = entities[0]

    assert automatic.entity_description.translation_key == "automatic_operation"
    assert automatic.entity_category is None
    assert automatic.is_on is False
    await automatic.async_turn_on()
    await automatic.async_turn_off()
    assert controller.calls == [
        (CONF_AUTOMATIC_OPERATION, True),
        (CONF_AUTOMATIC_OPERATION, False),
    ]


async def test_level_lock_switch_delegates_to_controller(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Level lock is a primary control that preserves parent exact selection."""
    entities: list[switch_platform.NurserySootherLevelLockSwitch] = []
    await switch_platform.async_setup_entry(hass, entry, entities.extend)
    level_lock = entities[1]

    assert level_lock.entity_description.translation_key == "level_lock"
    assert level_lock.entity_category is None
    assert level_lock.is_on is False
    await level_lock.async_turn_on()
    await level_lock.async_turn_off()
    assert controller.calls == [
        (CONF_LEVEL_LOCK, True),
        (CONF_LEVEL_LOCK, False),
    ]


async def test_baseline_preview_switch_delegates_without_changing_level(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Baseline preview is an independent playback control."""
    entities: list[switch_platform.NurserySootherBaselinePreviewSwitch] = []
    await switch_platform.async_setup_entry(hass, entry, entities.extend)
    preview = entities[2]

    assert preview.entity_description.translation_key == "baseline_preview"
    assert preview.entity_category is None
    assert preview.is_on is False
    await preview.async_turn_on()
    await preview.async_turn_off()
    assert controller.level is SoothingLevel.STANDBY
    assert controller.calls == [
        (CONF_BASELINE_PREVIEW, True),
        (CONF_BASELINE_PREVIEW, False),
    ]


async def test_enum_sensors_and_attention_binary_sensor(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Policy status remains separate from output level and attention."""
    sensors: list[sensor_platform.NurserySootherSensor] = []
    await sensor_platform.async_setup_entry(hass, entry, sensors.extend)
    by_key = {entity.entity_description.key: entity for entity in sensors}
    attention: list[
        binary_sensor_platform.NurserySootherAttentionRequiredBinarySensor
    ] = []
    await binary_sensor_platform.async_setup_entry(hass, entry, attention.extend)

    assert by_key["state"].native_value == SootherState.STANDBY.value
    assert by_key["state"].device_class is SensorDeviceClass.ENUM
    assert by_key["state"].options == [state.value for state in SootherState]
    assert by_key["recommendation"].native_value == Recommendation.START.value
    assert by_key["recommendation"].options == [
        recommendation.value for recommendation in Recommendation
    ]
    assert attention[0].device_class is BinarySensorDeviceClass.PROBLEM
    assert attention[0].is_on is False
    assert by_key["recommendation"].extra_state_attributes == {"suggested_level": None}
    assert by_key["state"].extra_state_attributes == controller.status_attributes

    controller.state = SootherState.ATTENTION_REQUIRED
    controller.recommendation = Recommendation.INCREASE_LEVEL
    controller.suggested_level = SoothingLevel.LEVEL_1
    controller.attention_required = True
    assert by_key["state"].native_value == SootherState.ATTENTION_REQUIRED.value
    assert by_key["recommendation"].native_value == Recommendation.INCREASE_LEVEL.value
    assert by_key["recommendation"].extra_state_attributes == {
        "suggested_level": SoothingLevel.LEVEL_1.value
    }
    assert attention[0].is_on is True

    controller.recommendation = Recommendation.ATTEND
    assert by_key["recommendation"].extra_state_attributes == {"suggested_level": None}


async def test_six_volume_numbers_delegate_safe_settings(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Every active level and the hard cap has one configuration number."""
    entities: list[number_platform.NurserySootherVolumeNumber] = []
    await number_platform.async_setup_entry(hass, entry, entities.extend)
    by_key = {entity.entity_description.key: entity for entity in entities}

    expected = {
        CONF_BASELINE_VOLUME: 10.0,
        CONF_LEVEL_1_VOLUME: 15.0,
        CONF_LEVEL_2_VOLUME: 20.0,
        CONF_LEVEL_3_VOLUME: 25.0,
        CONF_LEVEL_4_VOLUME: 30.0,
        CONF_MAX_VOLUME: 40.0,
    }
    assert set(by_key) == set(expected)
    for key, initial in expected.items():
        entity = by_key[key]
        assert entity.native_value == initial
        assert entity.native_min_value == 0
        assert entity.native_max_value == PERCENTAGE_MAX
        assert entity.native_step == 1
        assert entity.native_unit_of_measurement == PERCENTAGE
        assert entity.mode is NumberMode.SLIDER
        assert entity.entity_category is EntityCategory.CONFIG
        assert entity.entity_description.translation_key == key

        await entity.async_set_native_value(initial + 1)
        assert entity.native_value == initial + 1

    assert controller.calls == [(key, value + 1) for key, value in expected.items()]


async def test_simulator_is_the_only_button(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: _FakeController,
) -> None:
    """Legacy Boost, Baseline, Stop, and Acknowledge controls are absent."""
    entities: list[button_platform.NurserySootherSimulateCryButton] = []
    await button_platform.async_setup_entry(hass, entry, entities.extend)

    assert len(entities) == 1
    assert entities[0].entity_description.key == SERVICE_SIMULATE_CRY_EVENT
    assert entities[0].entity_category is EntityCategory.CONFIG
    await entities[0].async_press()
    assert controller.calls == [(SERVICE_SIMULATE_CRY_EVENT, None)]
