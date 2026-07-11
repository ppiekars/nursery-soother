"""Behavior and playback-safety tests for the Nursery Soother controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_VOLUME_LEVEL,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_STOP,
    SERVICE_VOLUME_SET,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Context, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.nursery_soother.const import (
    CONF_AUTOMATIC_OPERATION,
    CONF_BASELINE_VOLUME,
    CONF_CAMERA,
    CONF_CRY_SENSOR,
    CONF_LEVEL,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_MAX_VOLUME,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_SOUNDS,
    DEFAULT_OPTIONS,
    DOMAIN,
    ENTRY_VERSION,
    EVENT_NOTIFICATION_ACTION,
    NAME,
)
from custom_components.nursery_soother.controller import NurserySootherController
from custom_components.nursery_soother.models import (
    ACTIVE_LEVELS,
    Recommendation,
    SootherState,
    SoothingLevel,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

CRY_SENSOR = "binary_sensor.nursery_crying"
CAMERA = "camera.nursery"
MEDIA_PLAYER = "media_player.nursery"
PARENT_ONE = "notify.parent_one"
PARENT_TWO = "notify.parent_two"
PARENT_COUNT = 2

BASELINE_PERCENT = 10.0
LEVEL_1_PERCENT = 15.0
LEVEL_2_PERCENT = 20.0
LEVEL_3_PERCENT = 25.0
LEVEL_4_PERCENT = 30.0
MAX_PERCENT = 40.0
UPDATED_BASELINE_PERCENT = 11.0
UPDATED_LEVEL_1_PERCENT = 16.0

SOOTHING_MEDIA = {
    "media_content_id": "media-source://media_source/local/white-noise.mp3",
    "media_content_type": "audio/mpeg",
}
CONFIG_DATA = {
    CONF_CRY_SENSOR: CRY_SENSOR,
    CONF_CAMERA: CAMERA,
    CONF_MEDIA_PLAYER: MEDIA_PLAYER,
    CONF_SOUNDS: {level.value: dict(SOOTHING_MEDIA) for level in ACTIVE_LEVELS},
    CONF_NOTIFY_TARGETS: [PARENT_ONE, PARENT_TWO],
}

FAST_OPTIONS = DEFAULT_OPTIONS | {
    CONF_LEVEL: SoothingLevel.BASELINE.value,
    CONF_AUTOMATIC_OPERATION: False,
    CONF_BASELINE_VOLUME: BASELINE_PERCENT,
    CONF_LEVEL_1_VOLUME: LEVEL_1_PERCENT,
    CONF_LEVEL_2_VOLUME: LEVEL_2_PERCENT,
    CONF_LEVEL_3_VOLUME: LEVEL_3_PERCENT,
    CONF_LEVEL_4_VOLUME: LEVEL_4_PERCENT,
    CONF_MAX_VOLUME: MAX_PERCENT,
}

SPEAKER_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.STOP
)
SONOS_NURSERY_URL = "http://192.0.2.1:8123/media/local/white-noise.mp3?authSig=first"
SONOS_REFRESHED_NURSERY_URL = (
    "http://192.0.2.1:8123/media/local/white-noise.mp3?authSig=refreshed"
)
SONOS_BASE_URL = "http://192.0.2.1:8123"
TEST_CLOCK_DATA = f"{DOMAIN}_test_clock"


@dataclass
class RecordedCalls:
    """Service calls made by one controller under test."""

    media: list[ServiceCall] = field(default_factory=list)
    notifications: list[ServiceCall] = field(default_factory=list)


@pytest.fixture(autouse=True)
def _freeze_controller_clock(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Keep controller timestamps and fired timers on one realistic clock."""
    freezer.move_to("2026-07-11 12:00:00+00:00")
    hass.data[TEST_CLOCK_DATA] = freezer


@pytest.fixture
async def started_controller(
    hass: HomeAssistant,
) -> AsyncGenerator[tuple[NurserySootherController, RecordedCalls]]:
    """Create an active Baseline controller with real HA listeners."""
    calls = RecordedCalls()

    @callback
    def _record_media(call: ServiceCall) -> None:
        calls.media.append(call)
        if call.service != SERVICE_PLAY_MEDIA:
            return
        current_state = hass.states.get(MEDIA_PLAYER)
        attributes = dict(current_state.attributes) if current_state is not None else {}
        attributes[ATTR_MEDIA_CONTENT_ID] = call.data[ATTR_MEDIA_CONTENT_ID]
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            attributes,
            context=call.context,
        )

    @callback
    def _record_notification(call: ServiceCall) -> None:
        calls.notifications.append(call)

    for service in (
        SERVICE_VOLUME_SET,
        SERVICE_PLAY_MEDIA,
        SERVICE_MEDIA_STOP,
        SERVICE_MEDIA_PAUSE,
    ):
        hass.services.async_register("media_player", service, _record_media)
    hass.services.async_register("notify", "parent_one", _record_notification)
    hass.services.async_register("notify", "parent_two", _record_notification)

    hass.states.async_set(CRY_SENSOR, "off")
    hass.states.async_set(CAMERA, "idle")
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=FAST_OPTIONS,
        version=ENTRY_VERSION,
    )
    entry.add_to_hass(hass)
    controller = NurserySootherController(hass, entry)
    await controller.async_start()
    await hass.async_block_till_done()
    yield controller, calls
    await controller.async_shutdown()


def _media_calls(calls: RecordedCalls, service: str) -> list[ServiceCall]:
    """Return recorded media calls for one service."""
    return [call for call in calls.media if call.service == service]


def _incident_notifications(calls: RecordedCalls) -> list[ServiceCall]:
    """Return notifications other than synchronized clears."""
    return [
        call
        for call in calls.notifications
        if call.data.get("message") != "clear_notification"
    ]


def _clear_notifications(calls: RecordedCalls) -> list[ServiceCall]:
    """Return synchronized notification-clear calls."""
    return [
        call
        for call in calls.notifications
        if call.data.get("message") == "clear_notification"
    ]


async def _advance(hass: HomeAssistant, seconds: int) -> None:
    """Fire every point-in-time listener due within the requested duration."""
    clock: FrozenDateTimeFactory = hass.data[TEST_CLOCK_DATA]
    clock.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def _set_cry(hass: HomeAssistant, state: str) -> None:
    """Set the physical cry input and finish listener work."""
    hass.states.async_set(CRY_SENSOR, state)
    await hass.async_block_till_done()


async def _cry_pulse(hass: HomeAssistant) -> None:
    """Emit one short Reolink-style off-to-on-to-off pulse."""
    await _set_cry(hass, "on")
    await _set_cry(hass, "off")


async def _initial_cry_pulses(hass: HomeAssistant) -> None:
    """Emit the default two-event initial confirmation threshold."""
    for _ in range(2):
        await _cry_pulse(hass)


def _action_containing(call: ServiceCall, text: str) -> str:
    """Return the action token whose displayed title contains text."""
    actions = call.data["data"]["actions"]
    return next(action["action"] for action in actions if text in action["title"])


async def _enable_automatic_and_confirm(
    hass: HomeAssistant, controller: NurserySootherController
) -> None:
    """Enable automatic response and confirm one two-pulse initial stage."""
    await controller.async_set_automatic(enabled=True)
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)


async def _restart_with_sonos_play_state(
    hass: HomeAssistant,
    controller: NurserySootherController,
    calls: RecordedCalls,
    *,
    media_content_id: str | None,
) -> Context:
    """Start a fresh active level with one Sonos state before play returns."""
    await controller.async_set_level(SoothingLevel.STANDBY)
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
    )
    await hass.async_block_till_done()
    hass.config.internal_url = SONOS_BASE_URL

    @callback
    def _record_sonos_play(call: ServiceCall) -> None:
        calls.media.append(call)
        attributes = {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)}
        if media_content_id is not None:
            attributes[ATTR_MEDIA_CONTENT_ID] = media_content_id
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            attributes,
            context=call.context,
        )

    hass.services.async_register("media_player", SERVICE_PLAY_MEDIA, _record_sonos_play)
    await controller.async_set_level(SoothingLevel.BASELINE)
    await hass.async_block_till_done()
    return _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context


async def test_start_recovers_configured_baseline_and_shared_sound(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An active entry restores its exact level, volume, and mapped sound."""
    controller, calls = started_controller

    assert controller.level is SoothingLevel.BASELINE
    assert controller.automatic is False
    assert controller.state is SootherState.SOOTHING
    assert controller.recommendation is Recommendation.NONE
    assert _media_calls(calls, SERVICE_VOLUME_SET)[0].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(BASELINE_PERCENT / 100)
    play_call = _media_calls(calls, SERVICE_PLAY_MEDIA)[0]
    assert (
        play_call.data[ATTR_MEDIA_CONTENT_ID] == SOOTHING_MEDIA[ATTR_MEDIA_CONTENT_ID]
    )


async def test_persisted_active_startup_does_not_replace_existing_spotify(
    hass: HomeAssistant,
) -> None:
    """Startup relinquishes a persisted active level when media is external."""
    calls = RecordedCalls()

    @callback
    def _record_media(call: ServiceCall) -> None:
        calls.media.append(call)

    @callback
    def _record_notification(call: ServiceCall) -> None:
        calls.notifications.append(call)

    for service in (
        SERVICE_VOLUME_SET,
        SERVICE_PLAY_MEDIA,
        SERVICE_MEDIA_STOP,
        SERVICE_MEDIA_PAUSE,
    ):
        hass.services.async_register("media_player", service, _record_media)
    hass.services.async_register("notify", "parent_one", _record_notification)
    hass.services.async_register("notify", "parent_two", _record_notification)
    hass.states.async_set(CRY_SENSOR, "off")
    hass.states.async_set(CAMERA, "idle")
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "x-sonos-vli:RINCON_TEST:2,spotify:parent-session",
            "source": "Spotify Connect",
            "media_title": "Parent music",
        },
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=FAST_OPTIONS,
        version=ENTRY_VERSION,
    )
    entry.add_to_hass(hass)
    controller = NurserySootherController(hass, entry)
    await controller.async_start()
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.STANDBY
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.STANDBY.value
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["playback_owned"] is False
    assert not _media_calls(calls, SERVICE_VOLUME_SET)
    assert not _media_calls(calls, SERVICE_PLAY_MEDIA)
    assert not _media_calls(calls, SERVICE_MEDIA_STOP)
    assert await controller.async_shutdown()


async def test_standby_stops_owned_playback_and_exact_level_starts_it(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The level selector is the only start/stop and output-level control."""
    controller, calls = started_controller
    initial_play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    await controller.async_set_level(SoothingLevel.STANDBY)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.STANDBY
    assert controller.recommendation is Recommendation.START
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.STANDBY.value
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == 1

    await controller.async_set_level(SoothingLevel.LEVEL_2)

    assert controller.level is SoothingLevel.LEVEL_2
    assert controller.state is SootherState.SOOTHING
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.LEVEL_2.value
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_2_PERCENT / 100)
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == initial_play_count + 1


async def test_active_levels_using_same_sound_change_volume_without_restart(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The initial shared MP3 remains continuous across active level changes."""
    controller, calls = started_controller
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    await controller.async_set_level(SoothingLevel.LEVEL_3)

    assert controller.level is SoothingLevel.LEVEL_3
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_3_PERCENT / 100)
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count


async def test_active_level_can_use_a_distinct_soothing_sound(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The level-ready map safely replaces owned media when sources differ."""
    controller, calls = started_controller
    distinct_media = {
        ATTR_MEDIA_CONTENT_ID: (
            "media-source://media_source/local/level-1-white-noise.mp3"
        ),
        "media_content_type": "audio/mpeg",
    }
    controller.sounds[SoothingLevel.LEVEL_1] = distinct_media
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    await controller.async_set_level(SoothingLevel.LEVEL_1)

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count + 1
    assert (
        _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].data[ATTR_MEDIA_CONTENT_ID]
        == distinct_media[ATTR_MEDIA_CONTENT_ID]
    )


async def test_failed_active_level_volume_change_rolls_back_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A rejected same-sound volume effect cannot publish the requested level."""
    controller, calls = started_controller

    @callback
    def _fail_volume(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected volume"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_VOLUME_SET, _fail_volume)

    await controller.async_set_level(SoothingLevel.LEVEL_1)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.BASELINE.value
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    hass.services.async_register("media_player", SERVICE_VOLUME_SET, calls.media.append)


async def test_failed_active_level_media_change_rolls_back_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A rejected replacement sound cannot publish the requested active level."""
    controller, _ = started_controller
    controller.sounds[SoothingLevel.LEVEL_1] = {
        ATTR_MEDIA_CONTENT_ID: (
            "media-source://media_source/local/level-1-white-noise.mp3"
        ),
        "media_content_type": "audio/mpeg",
    }

    @callback
    def _fail_play(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected replacement sound"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_PLAY_MEDIA, _fail_play)

    await controller.async_set_level(SoothingLevel.LEVEL_1)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.STANDBY.value
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    await _advance(hass, 16)


async def test_manual_two_pulse_evidence_suggests_exact_next_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Manual mode explains two events and never changes speaker level."""
    controller, calls = started_controller
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await _initial_cry_pulses(hass)

    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.WAIT
    assert not _incident_notifications(calls)

    await _advance(hass, 9)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count
    notifications = _incident_notifications(calls)
    assert len(notifications) == PARENT_COUNT
    for notification in notifications:
        assert "2" in notification.data["message"]
        assert "Level 1" in notification.data["message"]
        assert ATTR_ENTITY_ID not in notification.data["data"]
        assert notification.data["data"]["image"] == f"/api/camera_proxy/{CAMERA}"
        assert notification.data["data"]["url"] == f"entityId:{CAMERA}"
        assert notification.data["data"]["clickAction"] == f"entityId:{CAMERA}"
        titles = {action["title"] for action in notification.data["data"]["actions"]}
        assert any("Level 1" in title for title in titles)
        assert "Acknowledge" not in titles


async def test_manual_suggestion_action_selects_exact_level_and_clears_all_phones(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One parent's exact selection is the shared decision; no Ack is needed."""
    controller, calls = started_controller
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)
    action = _action_containing(_incident_notifications(calls)[0], "Level 1")

    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": action})
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.LEVEL_1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_1_PERCENT / 100)
    assert len(_clear_notifications(calls)) == PARENT_COUNT


async def test_parent_level_selection_invalidates_stale_notification_actions(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An old phone action cannot override a newer shared level decision."""
    controller, calls = started_controller
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)
    stale_standby = _action_containing(_incident_notifications(calls)[0], "Standby")

    await controller.async_set_level(SoothingLevel.LEVEL_1)
    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": stale_standby})
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.LEVEL_1


async def test_automatic_two_pulse_evidence_raises_exactly_one_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Automatic mode consumes one qualified stage for one level change."""
    controller, calls = started_controller

    await _enable_automatic_and_confirm(hass, controller)

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.state is SootherState.RESPONDING
    assert controller.recommendation is Recommendation.OBSERVE
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_1_PERCENT / 100)
    assert len(_incident_notifications(calls)) == PARENT_COUNT
    assert all(
        "Level 1" in call.data["message"] for call in _incident_notifications(calls)
    )
    assert controller.diagnostics["provisional_level_1"] is False
    assert controller.diagnostics["timers"]["provisional"] is False


async def test_first_cry_event_immediately_starts_provisional_level_1(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One physical event gets a mild response before confirmation."""
    controller, calls = started_controller
    await controller.async_set_automatic(enabled=True)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await _cry_pulse(hass)

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.WAIT
    assert controller.diagnostics["provisional_level_1"] is True
    assert controller.diagnostics["timers"]["provisional"] is True
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_1_PERCENT / 100)
    assert not _incident_notifications(calls)


async def test_unconfirmed_provisional_level_1_returns_to_baseline_after_dwell(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A lone event gets only one bounded 20-second Level 1 response."""
    controller, calls = started_controller
    await controller.async_set_automatic(enabled=True)
    await _cry_pulse(hass)

    await _advance(hass, 19)
    assert controller.level is SoothingLevel.LEVEL_1

    await _advance(hass, 2)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.WAIT
    assert controller.diagnostics["provisional_level_1"] is False
    assert controller.diagnostics["timers"]["provisional"] is False
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(BASELINE_PERCENT / 100)
    assert not _incident_notifications(calls)


async def test_disabling_automatic_rolls_back_provisional_level_1(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Turning off automatic operation immediately ends its provisional effect."""
    controller, _ = started_controller
    await controller.async_set_automatic(enabled=True)
    await _cry_pulse(hass)

    await controller.async_set_automatic(enabled=False)

    assert controller.automatic is False
    assert controller.level is SoothingLevel.BASELINE
    assert controller.diagnostics["provisional_level_1"] is False
    assert controller.diagnostics["timers"]["provisional"] is False


async def test_automatic_does_not_reuse_evidence_for_another_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The two initial events consumed by Level 1 cannot authorize Level 2."""
    controller, calls = started_controller
    await _enable_automatic_and_confirm(hass, controller)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await _advance(hass, 21)

    assert controller.level is SoothingLevel.LEVEL_1
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_automatic_requires_fresh_evidence_and_level_dwell(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One fresh post-change event waits for the 20-second Level 1 dwell."""
    controller, calls = started_controller
    await _enable_automatic_and_confirm(hass, controller)
    await _advance(hass, 5)
    await _cry_pulse(hass)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    assert controller.level is SoothingLevel.LEVEL_1
    await _advance(hass, 5)
    assert controller.level is SoothingLevel.LEVEL_1
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    await _advance(hass, 2)

    assert controller.level is SoothingLevel.LEVEL_2
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_2_PERCENT / 100)


async def test_held_cry_uses_fresh_active_time_after_each_increase(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A held sensor responds now, confirms at 8 seconds, then needs 6 fresh."""
    controller, calls = started_controller
    controller.settings.level_up_seconds = 1
    await controller.async_set_automatic(enabled=True)
    await _set_cry(hass, "on")

    assert controller.level is SoothingLevel.LEVEL_1
    await _advance(hass, 7)
    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.diagnostics["provisional_level_1"] is True

    await _advance(hass, 2)
    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.diagnostics["provisional_level_1"] is False
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await _advance(hass, 5)
    assert controller.level is SoothingLevel.LEVEL_1
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    await _advance(hass, 2)
    assert controller.level is SoothingLevel.LEVEL_2
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1

    await _set_cry(hass, "off")


async def test_quiet_interval_decreases_only_one_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One uninterrupted quiet timer moves Level 3 to Level 2, never Standby."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.LEVEL_3)
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await _advance(hass, 52)
    assert controller.level is SoothingLevel.LEVEL_3
    await _advance(hass, 60)

    assert controller.level is SoothingLevel.LEVEL_2
    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_2_PERCENT / 100)


async def test_quiet_downshift_stops_at_baseline(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Quiet policy never turns soothing off without an attention timeout."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.LEVEL_1)
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)

    await _advance(hass, 52)
    await _advance(hass, 60)
    assert controller.level is SoothingLevel.BASELINE
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await _advance(hass, 121)

    assert controller.level is SoothingLevel.BASELINE
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_cry_gap_tied_with_attention_deadline_resolves_as_quiet(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """At an exact deadline tie, established quiet wins over attention."""
    controller, calls = started_controller
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await _advance(hass, 40)
    await controller.async_simulate_cry_event()
    await _advance(hass, 50)
    await controller.async_simulate_cry_event()
    await _advance(hass, 60)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING
    assert not controller.attention_required
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert controller.diagnostics["timers"]["attention"] is False


async def test_unresolved_150_second_episode_enters_standby_and_attention(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A held cry reaches the fixed safety cutoff and stops owned playback."""
    controller, calls = started_controller
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await _set_cry(hass, "on")
    await _advance(hass, 7)
    assert controller.recommendation is Recommendation.WAIT

    await _advance(hass, 2)
    assert controller.recommendation is Recommendation.INCREASE_LEVEL

    await _advance(hass, 151)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.ATTEND
    assert controller.attention_required
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_simulated_cry_is_one_point_event_not_a_virtual_state(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One test press contributes one event and cannot meet the threshold alone."""
    controller, calls = started_controller

    await controller.async_simulate_cry_event()
    await _advance(hass, 9)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.recommendation is Recommendation.WAIT
    assert not _incident_notifications(calls)
    assert "simulated_cry_event" not in controller.diagnostics["timers"]


async def test_simulated_cry_in_standby_is_a_no_op(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A diagnostic event cannot start policy or output while explicitly off."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.STANDBY)
    await controller.async_set_automatic(enabled=True)
    media_count = len(calls.media)
    notification_count = len(calls.notifications)

    await controller.async_simulate_cry_event()
    await _advance(hass, 200)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.STANDBY
    assert controller.recommendation is Recommendation.START
    assert len(calls.media) == media_count
    assert len(calls.notifications) == notification_count
    assert not any(controller.diagnostics["timers"].values())


async def test_two_simulated_point_events_use_normal_manual_evidence_path(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Two test presses qualify exactly like two physical rising edges."""
    controller, calls = started_controller

    for _ in range(2):
        await controller.async_simulate_cry_event()
    await _advance(hass, 9)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    notifications = _incident_notifications(calls)
    assert len(notifications) == PARENT_COUNT
    assert all("Simulated" in call.data["message"] for call in notifications)
    assert all("Level 1" in call.data["message"] for call in notifications)


async def test_two_simulated_events_can_raise_one_automatic_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The diagnostic button exercises real automatic policy and speaker effects."""
    controller, calls = started_controller
    await controller.async_set_automatic(enabled=True)

    for _ in range(2):
        await controller.async_simulate_cry_event()
    await _advance(hass, 9)

    assert controller.level is SoothingLevel.LEVEL_1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_1_PERCENT / 100)


async def test_automatic_toggle_persists_without_changing_output(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Automatic operation is an orthogonal mode switch, not an enable switch."""
    controller, calls = started_controller
    media_count = len(calls.media)

    await controller.async_set_automatic(enabled=True)
    assert controller.automatic is True
    assert controller.entry.options[CONF_AUTOMATIC_OPERATION] is True
    assert controller.level is SoothingLevel.BASELINE

    await controller.async_set_automatic(enabled=False)
    assert controller.automatic is False
    assert controller.entry.options[CONF_AUTOMATIC_OPERATION] is False
    assert controller.level is SoothingLevel.BASELINE
    assert len(calls.media) == media_count


async def test_disabling_automatic_waits_for_fresh_manual_evidence(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Automatic-to-manual mode change cannot invent a zero-event suggestion."""
    controller, calls = started_controller
    await _enable_automatic_and_confirm(hass, controller)
    notification_count = len(_incident_notifications(calls))

    await controller.async_set_automatic(enabled=False)

    assert controller.automatic is False
    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.state is SootherState.RESPONDING
    assert controller.recommendation is Recommendation.WAIT
    assert len(_incident_notifications(calls)) == notification_count

    await _advance(hass, 20)
    await _cry_pulse(hass)

    new_notifications = _incident_notifications(calls)[notification_count:]
    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    assert len(new_notifications) == PARENT_COUNT
    assert all("1 cry event" in call.data["message"] for call in new_notifications)
    assert all("Level 2" in call.data["message"] for call in new_notifications)
    assert all("0 cry" not in call.data["message"] for call in new_notifications)


@pytest.mark.parametrize(
    ("key", "invalid_value"),
    [
        (CONF_BASELINE_VOLUME, LEVEL_1_PERCENT + 1),
        (CONF_LEVEL_1_VOLUME, BASELINE_PERCENT - 1),
        (CONF_LEVEL_2_VOLUME, LEVEL_1_PERCENT - 1),
        (CONF_LEVEL_3_VOLUME, LEVEL_2_PERCENT - 1),
        (CONF_LEVEL_4_VOLUME, LEVEL_3_PERCENT - 1),
        (CONF_MAX_VOLUME, LEVEL_4_PERCENT - 1),
    ],
)
async def test_non_monotonic_volume_updates_are_rejected(
    started_controller: tuple[NurserySootherController, RecordedCalls],
    key: str,
    invalid_value: float,
) -> None:
    """Runtime number controls cannot violate ordered level volumes or the cap."""
    controller, calls = started_controller
    old_value = getattr(controller.settings, key)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    with pytest.raises(ServiceValidationError):
        await controller.async_set_volume(key, invalid_value)

    assert getattr(controller.settings, key) == old_value
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_valid_volume_updates_persist_and_only_current_level_is_applied(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Known monotonic settings persist; only Baseline changes live output."""
    controller, calls = started_controller
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await controller.async_set_volume(CONF_LEVEL_1_VOLUME, UPDATED_LEVEL_1_PERCENT)
    assert controller.settings.level_1_volume == UPDATED_LEVEL_1_PERCENT
    assert controller.entry.options[CONF_LEVEL_1_VOLUME] == UPDATED_LEVEL_1_PERCENT
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    await controller.async_set_volume(CONF_BASELINE_VOLUME, UPDATED_BASELINE_PERCENT)
    assert controller.settings.baseline_volume == UPDATED_BASELINE_PERCENT
    assert controller.entry.options[CONF_BASELINE_VOLUME] == UPDATED_BASELINE_PERCENT
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(UPDATED_BASELINE_PERCENT / 100)

    with pytest.raises(ServiceValidationError):
        await controller.async_set_volume("not_a_volume", 10)


async def test_attribute_updates_do_not_inflate_cry_event_count(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Only off-to-on edges count; same-state camera updates are not events."""
    controller, calls = started_controller
    await _set_cry(hass, "on")
    hass.states.async_set(CRY_SENSOR, "on", {"heartbeat": 1})
    hass.states.async_set(CRY_SENSOR, "on", {"heartbeat": 2})
    await hass.async_block_till_done()
    await _set_cry(hass, "off")

    await _advance(hass, 9)

    assert controller.recommendation is Recommendation.WAIT
    assert not _incident_notifications(calls)


async def test_notification_failure_does_not_block_other_parent(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One offline phone cannot prevent the other parent receiving a suggestion."""
    _, calls = started_controller

    @callback
    def _fail_notification(call: ServiceCall) -> None:
        del call
        error_message = "phone offline"
        raise HomeAssistantError(error_message)

    hass.services.async_register("notify", "parent_one", _fail_notification)
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)

    notifications = _incident_notifications(calls)
    assert len(notifications) == 1
    assert notifications[0].service == "parent_two"


@pytest.mark.parametrize("mode", ["manual", "automatic"])
async def test_all_notification_failures_stop_in_standby(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
    mode: str,
) -> None:
    """No caregiver visibility immediately stops owned soothing output."""
    controller, calls = started_controller
    if mode == "automatic":
        await controller.async_set_automatic(enabled=True)
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    @callback
    def _fail_notification(call: ServiceCall) -> None:
        del call
        error_message = "unexpected third-party notification failure"
        raise RuntimeError(error_message)

    hass.services.async_register("notify", "parent_one", _fail_notification)
    hass.services.async_register("notify", "parent_two", _fail_notification)
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.level is SoothingLevel.STANDBY
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.STANDBY.value
    assert controller.diagnostics["playback_owned"] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_dependency_loss_blocks_automatic_increase_and_recovers_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Unavailable context cancels policy effects without changing parent intent."""
    controller, calls = started_controller
    await controller.async_set_automatic(enabled=True)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    hass.states.async_set(CAMERA, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert not controller.dependencies_available
    assert controller.level is SoothingLevel.BASELINE
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    hass.states.async_set(CAMERA, "idle")
    await hass.async_block_till_done()

    assert controller.dependencies_available
    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.SOOTHING


async def test_shutdown_cancels_policy_timers_and_listeners(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """No queued evidence callback can act after config-entry unload."""
    controller, calls = started_controller
    await _initial_cry_pulses(hass)
    assert await controller.async_shutdown()
    media_count = len(calls.media)

    await _advance(hass, 200)
    await _cry_pulse(hass)

    assert not _incident_notifications(calls)
    assert len(calls.media) == media_count


async def test_continuous_owned_playback_restarts_after_idle(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An active level guards playback that the integration owns."""
    _, calls = started_controller
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
    )
    await hass.async_block_till_done()

    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count + 1


async def test_sonos_auth_signature_rotation_keeps_playback_owned(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A refreshed HA authSig cannot turn unchanged nursery media external."""
    controller, calls = started_controller
    await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id=SONOS_NURSERY_URL,
    )
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: SONOS_REFRESHED_NURSERY_URL,
        },
    )
    await hass.async_block_till_done()

    assert controller.state is SootherState.SOOTHING
    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["playback_interrupted"] is False
    await controller.async_set_level(SoothingLevel.LEVEL_1)
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count

    await controller.async_set_level(SoothingLevel.STANDBY)
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_fresh_sonos_session_waits_for_signed_media_confirmation(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A no-ID Sonos play is owned but cannot be modified until identified."""
    controller, calls = started_controller
    play_context = await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id=None,
    )
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    with pytest.raises(ServiceValidationError):
        await controller.async_set_level(SoothingLevel.LEVEL_1)
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: SONOS_NURSERY_URL,
        },
        context=Context(parent_id=play_context.id),
    )
    await hass.async_block_till_done()

    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["playback_interrupted"] is False
    await controller.async_set_level(SoothingLevel.LEVEL_1)
    assert controller.level is SoothingLevel.LEVEL_1


async def test_stale_idle_signed_url_cannot_confirm_new_play(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The previous idle URL cannot preempt Sonos no-ID then signed events."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.STANDBY)
    hass.config.internal_url = SONOS_BASE_URL
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: SONOS_NURSERY_URL,
        },
    )
    await hass.async_block_till_done()

    @callback
    def _record_play_without_state(call: ServiceCall) -> None:
        calls.media.append(call)

    hass.services.async_register(
        "media_player", SERVICE_PLAY_MEDIA, _record_play_without_state
    )
    await controller.async_set_level(SoothingLevel.BASELINE)
    play_context = _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
        context=play_context,
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: SONOS_REFRESHED_NURSERY_URL,
        },
        context=Context(parent_id=play_context.id),
    )
    await hass.async_block_till_done()

    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["playback_interrupted"] is False
    await controller.async_set_level(SoothingLevel.LEVEL_1)


async def test_play_context_cannot_adopt_different_media(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An integration play context cannot make parent media ours."""
    controller, calls = started_controller
    await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id="x-sonos-vli:RINCON_TEST:2,spotify:parent-session",
    )
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.diagnostics["playback_owned"] is False
    await controller.async_set_level(SoothingLevel.STANDBY)
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_user_replaying_same_local_file_is_external(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An explicit user context distinguishes a manual same-file replay."""
    controller, calls = started_controller
    await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id=SONOS_NURSERY_URL,
    )
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: SONOS_REFRESHED_NURSERY_URL,
        },
        context=Context(user_id="parent-user"),
    )
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.diagnostics["playback_owned"] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_user_replaying_exact_same_raw_media_id_is_external(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """User context makes even a byte-identical replay an external takeover."""
    controller, calls = started_controller
    await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id=SONOS_NURSERY_URL,
    )
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: SONOS_NURSERY_URL,
            "media_position": 1,
        },
        context=Context(user_id="parent-user"),
    )
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["playback_owned"] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


@pytest.mark.parametrize(
    "replacement_id",
    [
        "http://192.0.2.1:8123/media/local/parent-music.mp3?authSig=other",
        (
            "http://192.0.2.1:8123/media/local/white-noise.mp3"
            "?authSig=refreshed&variant=parent"
        ),
        "http://198.51.100.1:8123/media/local/white-noise.mp3?authSig=other-host",
        "//evil.example/media/local/white-noise.mp3?authSig=other-host",
    ],
)
async def test_sonos_different_local_media_identity_is_external(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
    replacement_id: str,
) -> None:
    """Only the exact HA local path and auth-only query remain owned."""
    controller, calls = started_controller
    await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id=SONOS_NURSERY_URL,
    )
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: replacement_id,
        },
    )
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["playback_owned"] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_explicit_active_level_after_spotify_takeover_starts_fresh(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Takeover shows Standby; one active selection authorizes a fresh session."""
    controller, calls = started_controller
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: ("x-sonos-vli:RINCON_TEST:2,spotify:parent-session"),
            "source": "Spotify Connect",
            "media_title": "Parent music",
        },
    )
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["playback_owned"] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    await controller.async_set_volume(CONF_LEVEL_1_VOLUME, UPDATED_LEVEL_1_PERCENT)
    clear_count = len(_clear_notifications(calls))
    await controller.async_set_level(SoothingLevel.LEVEL_1)

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.state is SootherState.SOOTHING
    assert controller.recommendation is Recommendation.NONE
    assert controller.diagnostics["playback_owned"] is True
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count + 1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(UPDATED_LEVEL_1_PERCENT / 100)
    assert len(_clear_notifications(calls)) == clear_count + PARENT_COUNT
    assert all(
        call.data["data"]["tag"] == controller.notification_tag
        for call in _clear_notifications(calls)[-PARENT_COUNT:]
    )


async def test_live_takeover_is_reconciled_before_queued_level_command(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An explicit level may authorize a fresh session after live reconciliation."""
    controller, calls = started_controller
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "resolved://parent-music",
        },
    )
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await controller.async_set_level(SoothingLevel.LEVEL_1)
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.state is SootherState.SOOTHING
    assert controller.diagnostics["playback_owned"] is True
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count + 1


async def test_queued_automatic_increase_cannot_touch_external_media(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Queued policy work relinquishes a takeover without any media effect."""
    controller, calls = started_controller
    await controller.async_set_automatic(enabled=True)
    await _initial_cry_pulses(hass)
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "resolved://parent-music",
        },
    )
    await _advance(hass, 9)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["playback_owned"] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count


async def test_external_media_without_content_id_is_not_stopped(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A no-ID source without our play context is an uncertain takeover."""
    controller, calls = started_controller
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
    )
    await hass.async_block_till_done()
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    assert controller.state is SootherState.ATTENTION_REQUIRED
    await controller.async_set_level(SoothingLevel.STANDBY)
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_queued_no_id_takeover_is_reconciled_before_private_stop(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Even unload cannot halt queued external no-ID audio."""
    controller, calls = started_controller
    controller._owned_media_content_id = None  # noqa: SLF001
    controller._awaiting_playback_confirmation = False  # noqa: SLF001

    async with controller._lock:  # noqa: SLF001
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
        )
        stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
        assert await controller._async_stop_playback()  # noqa: SLF001

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    await hass.async_block_till_done()


async def test_no_id_ownership_survives_media_context_history_eviction(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The dedicated play context keeps no-ID nursery audio safely stoppable."""
    controller, calls = started_controller
    play_context = _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context
    controller._owned_media_content_id = None  # noqa: SLF001
    controller._awaiting_playback_confirmation = False  # noqa: SLF001
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
        context=play_context,
    )
    await hass.async_block_till_done()

    for _ in range(20):
        assert await controller._async_call_media(  # noqa: SLF001
            SERVICE_VOLUME_SET,
            {ATTR_MEDIA_VOLUME_LEVEL: BASELINE_PERCENT / 100},
        )
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await controller.async_set_level(SoothingLevel.STANDBY)

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_runtime_media_capability_loss_fails_safe(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A player that can no longer be stopped is an unhealthy dependency."""
    controller, _ = started_controller
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.VOLUME_SET
            ),
            ATTR_MEDIA_CONTENT_ID: SOOTHING_MEDIA[ATTR_MEDIA_CONTENT_ID],
        },
    )
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert not controller.dependencies_available

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: SOOTHING_MEDIA[ATTR_MEDIA_CONTENT_ID],
        },
    )
    await hass.async_block_till_done()

    assert controller.dependencies_available
    assert controller.level is SoothingLevel.BASELINE


async def test_speaker_recovery_does_not_overwrite_external_media(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A reconnect already playing parent media remains externally owned."""
    controller, calls = started_controller
    hass.states.async_set(MEDIA_PLAYER, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "resolved://parent-music",
        },
    )
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count


async def test_camera_recovery_reconciles_takeover_during_outage(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Recovery cannot overwrite parent media started during another outage."""
    controller, calls = started_controller
    hass.states.async_set(CAMERA, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "resolved://parent-music",
        },
    )
    await hass.async_block_till_done()
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    hass.states.async_set(CAMERA, "idle")
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count


async def test_standby_uses_pause_fallback_for_owned_playback(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Standby falls back to pause when the accepted player lacks media_stop."""
    controller, calls = started_controller
    pause_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.PAUSE
    )
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(pause_features),
            ATTR_MEDIA_CONTENT_ID: SOOTHING_MEDIA[ATTR_MEDIA_CONTENT_ID],
        },
    )
    await hass.async_block_till_done()

    await controller.async_set_level(SoothingLevel.STANDBY)

    assert controller.level is SoothingLevel.STANDBY
    assert len(_media_calls(calls, SERVICE_MEDIA_PAUSE)) == 1


async def test_failed_standby_stop_remains_attention_required(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A failed stop cannot claim that integration audio is safely off."""
    controller, calls = started_controller

    @callback
    def _fail_stop(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected stop"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_MEDIA_STOP, _fail_stop)

    await controller.async_set_level(SoothingLevel.STANDBY)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES

    hass.services.async_register("media_player", SERVICE_MEDIA_STOP, calls.media.append)


async def test_commands_cannot_cross_successful_shutdown(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Queued entity commands cannot issue effects after unload begins."""
    controller, calls = started_controller
    assert await controller.async_shutdown()
    media_count = len(calls.media)

    with pytest.raises(ServiceValidationError):
        await controller.async_set_level(SoothingLevel.LEVEL_1)
    with pytest.raises(ServiceValidationError):
        await controller.async_set_automatic(enabled=True)
    with pytest.raises(ServiceValidationError):
        await controller.async_set_volume(CONF_LEVEL_1_VOLUME, 16)
    with pytest.raises(ServiceValidationError):
        await controller.async_simulate_cry_event()

    assert len(calls.media) == media_count


async def test_arbitrary_play_failure_is_isolated_and_compensated(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An ambiguous third-party play failure triggers a compensating stop."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.STANDBY)
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    @callback
    def _start_then_fail(call: ServiceCall) -> None:
        calls.media.append(call)
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            {
                ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
                ATTR_MEDIA_CONTENT_ID: (
                    "/media/local/white-noise.mp3?authSig=failed-play"
                ),
            },
            context=call.context,
        )
        error_message = "unexpected third-party failure"
        raise RuntimeError(error_message)

    hass.services.async_register("media_player", SERVICE_PLAY_MEDIA, _start_then_fail)

    await controller.async_set_level(SoothingLevel.BASELINE)
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_immediate_play_rejection_does_not_stop_parent_media(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A rejected play cannot claim or stop unchanged parent audio."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.STANDBY)
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "resolved://parent-music",
        },
    )
    await hass.async_block_till_done()
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    @callback
    def _reject_play(call: ServiceCall) -> None:
        calls.media.append(call)
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            {
                ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
                ATTR_MEDIA_CONTENT_ID: "resolved://parent-music",
            },
            context=call.context,
        )
        error_message = "speaker rejected play before changing state"
        raise RuntimeError(error_message)

    hass.services.async_register("media_player", SERVICE_PLAY_MEDIA, _reject_play)

    await controller.async_set_level(SoothingLevel.BASELINE)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    await _advance(hass, 16)
    assert await controller.async_shutdown()


async def test_delayed_failed_play_context_is_compensated(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Late audio from a failed play call remains covered by compensation."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.STANDBY)
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    @callback
    def _reject_then_start_later(call: ServiceCall) -> None:
        calls.media.append(call)
        error_message = "speaker reported failure before its delayed state"
        raise RuntimeError(error_message)

    hass.services.async_register(
        "media_player", SERVICE_PLAY_MEDIA, _reject_then_start_later
    )
    await controller.async_set_level(SoothingLevel.BASELINE)
    failed_context = _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: SOOTHING_MEDIA[ATTR_MEDIA_CONTENT_ID],
            "media_position": 1,
        },
        context=Context(parent_id=failed_context.id),
    )
    await hass.async_block_till_done()

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1
