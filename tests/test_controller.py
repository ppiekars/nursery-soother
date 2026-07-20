"""Behavior and playback-safety tests for the Nursery Soother controller."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.media_player import MediaPlayerEnqueue, RepeatMode
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_REPEAT,
    ATTR_MEDIA_VOLUME_LEVEL,
    SERVICE_CLEAR_PLAYLIST,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
    MediaType,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_STOP,
    SERVICE_REPEAT_SET,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_SET,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Context, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

import custom_components.nursery_soother.controller as controller_module
from custom_components.nursery_soother.const import (
    CONF_ATTENTION_SECONDS,
    CONF_AUTOMATIC_OPERATION,
    CONF_BASELINE_VOLUME,
    CONF_CAMERA,
    CONF_CRY_SENSOR,
    CONF_LEVEL,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_LEVEL_LOCK,
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
    PolicyExplanation,
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
RECOVERY_CALL_COUNT = 2
TRACK_CHANGE_CLEAR_COUNT = 3

BASELINE_PERCENT = 10.0
LEVEL_1_PERCENT = 15.0
LEVEL_2_PERCENT = 20.0
LEVEL_3_PERCENT = 25.0
LEVEL_4_PERCENT = 30.0
MAX_PERCENT = 40.0
UPDATED_BASELINE_PERCENT = 11.0
UPDATED_LEVEL_1_PERCENT = 16.0
UPDATED_ATTENTION_MINUTES = 3.5
UPDATED_ATTENTION_SECONDS = 210

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
SONOS_SPEAKER_FEATURES = (
    SPEAKER_FEATURES
    | MediaPlayerEntityFeature.CLEAR_PLAYLIST
    | MediaPlayerEntityFeature.MEDIA_ENQUEUE
    | MediaPlayerEntityFeature.REPEAT_SET
)
SONOS_UNIQUE_ID = "RINCON_TEST"
SONOS_CROSSFADE = "switch.nursery_crossfade"
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
    switches: list[ServiceCall] = field(default_factory=list)
    effects: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SonosTestBehavior:
    """Optional delayed-state and takeover behavior for the Sonos test double."""

    confirm_crossfade: bool = True
    confirm_repeat: bool = True
    crossfade_requires_queue_transport: bool = False
    defer_crossfade_state_until_refresh: bool = False
    defer_stop_state_until_next_loop: bool = False
    stop_results_in_pause: bool = False
    takeover_on_crossfade: bool = False
    takeover_on_play: bool = False


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


async def _start_sonos_controller(  # noqa: C901, PLR0915
    hass: HomeAssistant,
    *,
    initial_level: SoothingLevel = SoothingLevel.BASELINE,
    initial_repeat: str = RepeatMode.OFF,
    initial_crossfade: str = STATE_OFF,
    behavior: SonosTestBehavior | None = None,
) -> tuple[NurserySootherController, RecordedCalls]:
    """Start a controller against a registry-paired Sonos test double."""
    behavior = behavior or SonosTestBehavior()
    calls = RecordedCalls()
    actual_crossfade = initial_crossfade
    queue_transport_active = False
    registry = er.async_get(hass)
    media_entry = registry.async_get_or_create(
        domain="media_player",
        platform="sonos",
        unique_id=SONOS_UNIQUE_ID,
        suggested_object_id="nursery",
    )
    crossfade_entry = registry.async_get_or_create(
        domain="switch",
        platform="sonos",
        unique_id=f"{SONOS_UNIQUE_ID}-cross_fade",
        suggested_object_id="nursery_crossfade",
    )
    assert media_entry.entity_id == MEDIA_PLAYER
    assert crossfade_entry.entity_id == SONOS_CROSSFADE

    @callback
    def _record_media(call: ServiceCall) -> None:
        nonlocal queue_transport_active
        calls.media.append(call)
        calls.effects.append(("media_player", call.service))
        current_state = hass.states.get(MEDIA_PLAYER)
        assert current_state is not None
        attributes = dict(current_state.attributes)
        state = current_state.state
        context = call.context
        if call.service == SERVICE_PLAY_MEDIA:
            if call.data[ATTR_MEDIA_CONTENT_TYPE] != MediaType.MUSIC:
                error_message = "Sonos requires a music media type"
                raise ServiceValidationError(error_message)
            if behavior.takeover_on_play:
                attributes[ATTR_MEDIA_CONTENT_ID] = "resolved://parent-race"
                context = Context(user_id="parent-user")
            else:
                attributes[ATTR_MEDIA_CONTENT_ID] = call.data[ATTR_MEDIA_CONTENT_ID]
                queue_transport_active = (
                    call.data.get(ATTR_MEDIA_ENQUEUE) == MediaPlayerEnqueue.PLAY
                )
            state = "playing"
        elif call.service == SERVICE_REPEAT_SET:
            if (
                not behavior.confirm_repeat
                and call.data[ATTR_MEDIA_REPEAT] == RepeatMode.ALL
            ):
                return
            attributes[ATTR_MEDIA_REPEAT] = call.data[ATTR_MEDIA_REPEAT]
        elif call.service == SERVICE_MEDIA_STOP:
            if behavior.defer_stop_state_until_next_loop:

                @callback
                def _confirm_stop() -> None:
                    hass.states.async_set(
                        MEDIA_PLAYER,
                        "idle",
                        attributes,
                        context=context,
                    )

                hass.loop.call_soon(_confirm_stop)
                return
            state = "paused" if behavior.stop_results_in_pause else "idle"
        elif call.service == SERVICE_MEDIA_PAUSE:
            state = "paused"
        else:
            return
        hass.states.async_set(
            MEDIA_PLAYER,
            state,
            attributes,
            context=context,
        )

    @callback
    def _record_switch(call: ServiceCall) -> None:
        nonlocal actual_crossfade
        calls.switches.append(call)
        calls.effects.append(("switch", call.service))
        if call.service == SERVICE_TURN_ON and not behavior.confirm_crossfade:
            return
        if (
            call.service == SERVICE_TURN_ON
            and behavior.crossfade_requires_queue_transport
            and not queue_transport_active
        ):
            return
        actual_crossfade = STATE_ON if call.service == SERVICE_TURN_ON else STATE_OFF
        if not behavior.defer_crossfade_state_until_refresh:
            hass.states.async_set(
                SONOS_CROSSFADE,
                actual_crossfade,
                context=call.context,
            )
        if call.service == SERVICE_TURN_ON and behavior.takeover_on_crossfade:
            current_state = hass.states.get(MEDIA_PLAYER)
            assert current_state is not None
            attributes = dict(current_state.attributes)
            attributes[ATTR_MEDIA_CONTENT_ID] = "resolved://parent-race"
            hass.states.async_set(
                MEDIA_PLAYER,
                "playing",
                attributes,
                context=Context(user_id="parent-user"),
            )

    @callback
    def _record_entity_refresh(call: ServiceCall) -> None:
        entity_ids = call.data[ATTR_ENTITY_ID]
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        if SONOS_CROSSFADE in entity_ids:
            hass.states.async_set(
                SONOS_CROSSFADE,
                actual_crossfade,
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
        SERVICE_CLEAR_PLAYLIST,
        SERVICE_REPEAT_SET,
    ):
        hass.services.async_register("media_player", service, _record_media)
    for service in (SERVICE_TURN_ON, SERVICE_TURN_OFF):
        hass.services.async_register("switch", service, _record_switch)
    hass.services.async_register(
        "homeassistant", "update_entity", _record_entity_refresh
    )
    hass.services.async_register("notify", "parent_one", _record_notification)
    hass.services.async_register("notify", "parent_two", _record_notification)

    hass.states.async_set(CRY_SENSOR, "off")
    hass.states.async_set(CAMERA, "idle")
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {
            ATTR_SUPPORTED_FEATURES: int(SONOS_SPEAKER_FEATURES),
            ATTR_MEDIA_REPEAT: initial_repeat,
        },
    )
    hass.states.async_set(SONOS_CROSSFADE, initial_crossfade)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data=CONFIG_DATA,
        options=FAST_OPTIONS | {CONF_LEVEL: initial_level.value},
        version=ENTRY_VERSION,
    )
    entry.add_to_hass(hass)
    controller = NurserySootherController(hass, entry)
    await controller.async_start()
    await hass.async_block_till_done()
    return controller, calls


@pytest.fixture
async def started_sonos_controller(
    hass: HomeAssistant,
) -> AsyncGenerator[tuple[NurserySootherController, RecordedCalls]]:
    """Create an active native-loop Sonos controller."""
    controller, calls = await _start_sonos_controller(hass)
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
    assert controller.status_attributes["session_started_at"] == (
        "2026-07-11T12:00:00+00:00"
    )
    assert _media_calls(calls, SERVICE_VOLUME_SET)[0].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(BASELINE_PERCENT / 100)
    play_call = _media_calls(calls, SERVICE_PLAY_MEDIA)[0]
    assert (
        play_call.data[ATTR_MEDIA_CONTENT_ID] == SOOTHING_MEDIA[ATTR_MEDIA_CONTENT_ID]
    )


async def test_generic_player_uses_direct_playback_fallback(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A non-Sonos player keeps the established direct-play behavior."""
    controller, calls = started_controller

    play_calls = _media_calls(calls, SERVICE_PLAY_MEDIA)

    assert len(play_calls) == 1
    assert ATTR_MEDIA_ENQUEUE not in play_calls[0].data
    assert (
        play_calls[0].data[ATTR_MEDIA_CONTENT_TYPE]
        == SOOTHING_MEDIA[ATTR_MEDIA_CONTENT_TYPE]
    )
    assert not _media_calls(calls, SERVICE_CLEAR_PLAYLIST)
    assert not _media_calls(calls, SERVICE_REPEAT_SET)
    assert controller.diagnostics["native_crossfade_loop_active"] is False


async def test_sonos_registry_pair_starts_one_item_crossfade_loop(
    hass: HomeAssistant,
    started_sonos_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A paired Sonos switch enables the proven one-item native loop."""
    controller, calls = started_sonos_controller
    registry = er.async_get(hass)

    assert (
        registry.async_get_entity_id("switch", "sonos", f"{SONOS_UNIQUE_ID}-cross_fade")
        == SONOS_CROSSFADE
    )
    assert calls.effects == [
        ("media_player", SERVICE_VOLUME_SET),
        ("media_player", SERVICE_CLEAR_PLAYLIST),
        ("media_player", SERVICE_PLAY_MEDIA),
        ("switch", SERVICE_TURN_ON),
        ("media_player", SERVICE_REPEAT_SET),
    ]
    play_calls = _media_calls(calls, SERVICE_PLAY_MEDIA)
    assert len(play_calls) == 1
    assert play_calls[0].data[ATTR_MEDIA_ENQUEUE] == MediaPlayerEnqueue.PLAY
    assert play_calls[0].data[ATTR_MEDIA_CONTENT_TYPE] == MediaType.MUSIC
    assert (
        _media_calls(calls, SERVICE_REPEAT_SET)[0].data[ATTR_MEDIA_REPEAT]
        == RepeatMode.ALL
    )
    assert [call.service for call in calls.switches] == [SERVICE_TURN_ON]
    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_REPEAT] == RepeatMode.ALL
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_ON
    assert controller.diagnostics["native_crossfade_loop_active"] is True


async def test_sonos_refreshes_stale_crossfade_state_before_confirmation(
    hass: HomeAssistant,
) -> None:
    """A successful Sonos toggle is confirmed through a forced entity refresh."""
    controller, calls = await _start_sonos_controller(
        hass,
        behavior=SonosTestBehavior(defer_crossfade_state_until_refresh=True),
    )

    assert [call.service for call in calls.switches] == [SERVICE_TURN_ON]
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_ON
    assert controller.diagnostics["native_crossfade_loop_active"] is True
    assert await controller.async_shutdown()


@pytest.mark.parametrize("changed_control", ["crossfade", "repeat"])
async def test_sonos_active_loop_fails_safe_when_required_control_changes(
    hass: HomeAssistant,
    started_sonos_controller: tuple[NurserySootherController, RecordedCalls],
    changed_control: str,
) -> None:
    """The controller cannot claim seamless playback after a mode is disabled."""
    controller, _ = started_sonos_controller
    if changed_control == "crossfade":
        await hass.services.async_call(
            "switch",
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: SONOS_CROSSFADE},
            blocking=True,
        )
    else:
        media_state = hass.states.get(MEDIA_PLAYER)
        assert media_state is not None
        attributes = dict(media_state.attributes)
        attributes[ATTR_MEDIA_REPEAT] = RepeatMode.OFF
        hass.states.async_set(MEDIA_PLAYER, media_state.state, attributes)
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["native_crossfade_loop_active"] is False
    assert controller.diagnostics["last_error_type"] == "native_loop_controls_changed"


async def test_sonos_owned_idle_rebuilds_one_item_without_periodic_refill(
    hass: HomeAssistant,
    started_sonos_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Only an unexpected owned idle state rebuilds the persistent Sonos queue."""
    controller, calls = started_sonos_controller
    current_state = hass.states.get(MEDIA_PLAYER)
    assert current_state is not None

    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        dict(current_state.attributes),
    )
    await hass.async_block_till_done()

    play_calls = _media_calls(calls, SERVICE_PLAY_MEDIA)
    assert len(play_calls) == RECOVERY_CALL_COUNT
    assert all(
        call.data[ATTR_MEDIA_ENQUEUE] == MediaPlayerEnqueue.PLAY for call in play_calls
    )
    assert len(_media_calls(calls, SERVICE_CLEAR_PLAYLIST)) == RECOVERY_CALL_COUNT
    assert len(_media_calls(calls, SERVICE_REPEAT_SET)) == RECOVERY_CALL_COUNT
    assert [call.service for call in calls.switches] == [SERVICE_TURN_ON]
    assert controller.diagnostics["native_crossfade_loop_active"] is True


async def test_sonos_idle_rebuild_failure_enters_attention_not_soothing(
    hass: HomeAssistant,
    started_sonos_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A lost loop capability cannot leave a silent session visibly healthy."""
    controller, calls = started_sonos_controller
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))
    current_state = hass.states.get(MEDIA_PLAYER)
    assert current_state is not None
    hass.services.async_remove("media_player", SERVICE_REPEAT_SET)

    hass.states.async_set(MEDIA_PLAYER, "idle", dict(current_state.attributes))
    await hass.async_block_till_done()

    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["playback_owned"] is False

    @callback
    def _restore_repeat_service(call: ServiceCall) -> None:
        calls.media.append(call)
        media_state = hass.states.get(MEDIA_PLAYER)
        assert media_state is not None
        attributes = dict(media_state.attributes)
        attributes[ATTR_MEDIA_REPEAT] = call.data[ATTR_MEDIA_REPEAT]
        hass.states.async_set(MEDIA_PLAYER, media_state.state, attributes)

    hass.services.async_register(
        "media_player", SERVICE_REPEAT_SET, _restore_repeat_service
    )
    assert await controller.async_shutdown()


async def test_sonos_distinct_level_track_reuses_one_native_loop_session(
    hass: HomeAssistant,
    started_sonos_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A per-level track change replaces one item without recapturing modes."""
    controller, calls = started_sonos_controller
    level_one_media = {
        "media_content_id": "media-source://media_source/local/brown-noise.mp3",
        "media_content_type": "audio/mpeg",
    }
    controller.sounds[SoothingLevel.LEVEL_1] = level_one_media

    await controller.async_set_level(SoothingLevel.LEVEL_1)
    await hass.async_block_till_done()

    play_calls = _media_calls(calls, SERVICE_PLAY_MEDIA)
    assert len(play_calls) == RECOVERY_CALL_COUNT
    assert (
        play_calls[-1].data[ATTR_MEDIA_CONTENT_ID]
        == level_one_media[ATTR_MEDIA_CONTENT_ID]
    )
    assert play_calls[-1].data[ATTR_MEDIA_ENQUEUE] == MediaPlayerEnqueue.PLAY
    assert len(_media_calls(calls, SERVICE_CLEAR_PLAYLIST)) == TRACK_CHANGE_CLEAR_COUNT
    assert [call.service for call in calls.switches] == [SERVICE_TURN_ON]
    assert [
        call.data[ATTR_MEDIA_REPEAT] for call in _media_calls(calls, SERVICE_REPEAT_SET)
    ] == [RepeatMode.ALL, RepeatMode.ALL]
    assert controller.diagnostics["native_crossfade_loop_active"] is True


async def test_sonos_track_change_waits_for_delayed_stop_confirmation(
    hass: HomeAssistant,
) -> None:
    """A Sonos push-state delay cannot turn our own stop into a takeover."""
    controller, _ = await _start_sonos_controller(
        hass,
        behavior=SonosTestBehavior(defer_stop_state_until_next_loop=True),
    )
    controller.sounds[SoothingLevel.LEVEL_1] = {
        ATTR_MEDIA_CONTENT_ID: "media-source://media_source/local/brown-noise.mp3",
        ATTR_MEDIA_CONTENT_TYPE: "audio/mpeg",
    }

    await controller.async_set_level(SoothingLevel.LEVEL_1)

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.state is SootherState.SOOTHING
    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["native_crossfade_loop_active"] is True
    assert await controller.async_shutdown()


async def test_sonos_standby_clears_queue_and_restores_settings(
    hass: HomeAssistant,
    started_sonos_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Standby removes Nursery audio and restores the prior Sonos modes."""
    controller, calls = started_sonos_controller
    startup_effect_count = len(calls.effects)

    await controller.async_set_level(SoothingLevel.STANDBY)
    await hass.async_block_till_done()

    assert calls.effects[startup_effect_count:] == [
        ("media_player", SERVICE_MEDIA_STOP),
        ("media_player", SERVICE_CLEAR_PLAYLIST),
        ("media_player", SERVICE_REPEAT_SET),
        ("switch", SERVICE_TURN_OFF),
    ]
    assert [
        call.data[ATTR_MEDIA_REPEAT] for call in _media_calls(calls, SERVICE_REPEAT_SET)
    ] == [RepeatMode.ALL, RepeatMode.OFF]
    assert [call.service for call in calls.switches] == [
        SERVICE_TURN_ON,
        SERVICE_TURN_OFF,
    ]
    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_REPEAT] == RepeatMode.OFF
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_OFF
    assert controller.diagnostics["native_crossfade_loop_active"] is False


async def test_sonos_preserves_preexisting_repeat_all_and_crossfade(
    hass: HomeAssistant,
) -> None:
    """Modes already enabled by a parent remain enabled after Standby."""
    controller, calls = await _start_sonos_controller(
        hass,
        initial_repeat=RepeatMode.ALL,
        initial_crossfade=STATE_ON,
    )

    await controller.async_set_level(SoothingLevel.STANDBY)
    await hass.async_block_till_done()

    assert [
        call.data[ATTR_MEDIA_REPEAT] for call in _media_calls(calls, SERVICE_REPEAT_SET)
    ] == [RepeatMode.ALL]
    assert not calls.switches
    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_REPEAT] == RepeatMode.ALL
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_ON
    assert await controller.async_shutdown()


async def test_sonos_requires_crossfade_state_confirmation(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfirmed crossfade stops and cleans the newly established queue."""
    monkeypatch.setattr(controller_module, "NATIVE_LOOP_CONFIRMATION_SECONDS", 0)
    controller, calls = await _start_sonos_controller(
        hass,
        behavior=SonosTestBehavior(confirm_crossfade=False),
    )

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == 1
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == 1
    assert len(_media_calls(calls, SERVICE_CLEAR_PLAYLIST)) == RECOVERY_CALL_COUNT
    assert [
        call.data[ATTR_MEDIA_REPEAT] for call in _media_calls(calls, SERVICE_REPEAT_SET)
    ] == [RepeatMode.OFF]
    assert [call.service for call in calls.switches] == [
        SERVICE_TURN_ON,
        SERVICE_TURN_OFF,
    ]
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_OFF
    assert controller.diagnostics["native_crossfade_loop_active"] is False
    assert await controller.async_shutdown()


async def test_failed_parent_start_publishes_rolled_back_standby_level(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed physical effect cannot leave controller-backed entities stale."""
    monkeypatch.setattr(controller_module, "NATIVE_LOOP_CONFIRMATION_SECONDS", 0)
    controller, _ = await _start_sonos_controller(
        hass,
        initial_level=SoothingLevel.STANDBY,
        behavior=SonosTestBehavior(confirm_crossfade=False),
    )
    published_levels: list[SoothingLevel] = []
    controller.async_add_listener(lambda: published_levels.append(controller.level))

    with pytest.raises(HomeAssistantError):
        await controller.async_set_level(SoothingLevel.BASELINE)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.STANDBY.value
    assert published_levels[-1] is SoothingLevel.STANDBY
    assert await controller.async_shutdown()


async def test_sonos_requires_repeat_all_state_confirmation(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfirmed repeat command is stopped and cleaned up immediately."""
    monkeypatch.setattr(controller_module, "NATIVE_LOOP_CONFIRMATION_SECONDS", 0)
    controller, calls = await _start_sonos_controller(
        hass,
        behavior=SonosTestBehavior(confirm_repeat=False),
    )

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == 1
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == 1
    assert len(_media_calls(calls, SERVICE_CLEAR_PLAYLIST)) == RECOVERY_CALL_COUNT
    assert [
        call.data[ATTR_MEDIA_REPEAT] for call in _media_calls(calls, SERVICE_REPEAT_SET)
    ] == [RepeatMode.ALL, RepeatMode.OFF]
    assert [call.service for call in calls.switches] == [
        SERVICE_TURN_ON,
        SERVICE_TURN_OFF,
    ]
    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_REPEAT] == RepeatMode.OFF
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_OFF
    assert controller.diagnostics["native_crossfade_loop_active"] is False
    assert await controller.async_shutdown()


@pytest.mark.parametrize(
    (
        "behavior",
        "expected_clear_count",
        "expected_play_count",
        "expected_crossfade",
    ),
    [
        (SonosTestBehavior(takeover_on_crossfade=True), 1, 1, STATE_ON),
        (SonosTestBehavior(takeover_on_play=True), 1, 1, STATE_OFF),
    ],
    ids=("during-crossfade", "before-crossfade"),
)
async def test_sonos_setup_takeover_never_mutates_parent_queue_or_repeat(
    hass: HomeAssistant,
    behavior: SonosTestBehavior,
    expected_clear_count: int,
    expected_play_count: int,
    expected_crossfade: str,
) -> None:
    """A parent winning either setup race becomes the untouched owner."""
    controller, calls = await _start_sonos_controller(hass, behavior=behavior)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.diagnostics["playback_owned"] is False
    assert controller.diagnostics["native_crossfade_loop_active"] is False
    assert len(_media_calls(calls, SERVICE_CLEAR_PLAYLIST)) == expected_clear_count
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == expected_play_count
    assert not _media_calls(calls, SERVICE_REPEAT_SET)
    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_CONTENT_ID] == (
        "resolved://parent-race"
    )
    assert hass.states.get(SONOS_CROSSFADE).state == expected_crossfade
    assert await controller.async_shutdown()


async def test_sonos_external_takeover_leaves_queue_and_modes_untouched(
    hass: HomeAssistant,
    started_sonos_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A parent takeover owns the queue and current Sonos mode settings."""
    controller, calls = started_sonos_controller
    effects_before_takeover = list(calls.effects)

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SONOS_SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "x-sonos-vli:RINCON_TEST:2,spotify:parent-session",
            ATTR_MEDIA_REPEAT: RepeatMode.ALL,
        },
        context=Context(user_id="parent-user"),
    )
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.diagnostics["playback_owned"] is False
    assert controller.diagnostics["native_crossfade_loop_active"] is False
    assert calls.effects == effects_before_takeover
    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_REPEAT] == RepeatMode.ALL
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_ON


async def test_explicit_level_after_sonos_takeover_replaces_queue_and_owns_session(
    hass: HomeAssistant,
    started_sonos_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An active selection from Standby authorizes replacing external Sonos audio."""
    controller, calls = started_sonos_controller
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SONOS_SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "x-sonos-vli:RINCON_TEST:2,spotify:parent-session",
            ATTR_MEDIA_REPEAT: RepeatMode.OFF,
        },
        context=Context(user_id="parent-user"),
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: SONOS_CROSSFADE},
        blocking=True,
    )

    assert controller.level is SoothingLevel.STANDBY
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
    clear_count = len(_media_calls(calls, SERVICE_CLEAR_PLAYLIST))
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    await controller.async_set_level(SoothingLevel.LEVEL_1)
    await hass.async_block_till_done()

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.state is SootherState.SOOTHING
    assert controller.recommendation is Recommendation.NONE
    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["native_crossfade_loop_active"] is True
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1
    assert len(_media_calls(calls, SERVICE_CLEAR_PLAYLIST)) == clear_count + 1
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count + 1
    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_REPEAT] == RepeatMode.ALL
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_ON

    await controller.async_set_level(SoothingLevel.STANDBY)

    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_REPEAT] == RepeatMode.OFF
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_OFF


@pytest.mark.parametrize(
    ("initial_state", "behavior"),
    [
        (
            "playing",
            SonosTestBehavior(crossfade_requires_queue_transport=True),
        ),
        (
            "playing",
            SonosTestBehavior(
                crossfade_requires_queue_transport=True,
                stop_results_in_pause=True,
            ),
        ),
        ("idle", SonosTestBehavior(crossfade_requires_queue_transport=True)),
        ("paused", SonosTestBehavior(crossfade_requires_queue_transport=True)),
    ],
    ids=("playing-to-idle", "playing-to-paused", "already-idle", "already-paused"),
)
async def test_explicit_sonos_takeover_establishes_queue_before_crossfade(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str,
    behavior: SonosTestBehavior,
) -> None:
    """A current or inactive Spotify transport cannot block explicit takeover."""
    monkeypatch.setattr(controller_module, "NATIVE_LOOP_CONFIRMATION_SECONDS", 0)
    controller, calls = await _start_sonos_controller(
        hass,
        initial_level=SoothingLevel.STANDBY,
        behavior=behavior,
    )
    hass.states.async_set(
        MEDIA_PLAYER,
        initial_state,
        {
            ATTR_SUPPORTED_FEATURES: int(SONOS_SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: ("x-sonos-vli:RINCON_TEST:2,spotify:parent-session"),
            ATTR_MEDIA_REPEAT: RepeatMode.OFF,
            "source": "Spotify Connect",
        },
        context=Context(user_id="parent-user"),
    )
    await hass.async_block_till_done()

    await controller.async_set_level(SoothingLevel.LEVEL_1)
    await hass.async_block_till_done()

    expected_effects = [
        ("media_player", SERVICE_VOLUME_SET),
        ("media_player", SERVICE_CLEAR_PLAYLIST),
        ("media_player", SERVICE_PLAY_MEDIA),
        ("switch", SERVICE_TURN_ON),
        ("media_player", SERVICE_REPEAT_SET),
    ]
    if initial_state == "playing":
        expected_effects.insert(0, ("media_player", SERVICE_MEDIA_STOP))
    assert calls.effects == expected_effects
    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.state is SootherState.SOOTHING
    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["native_crossfade_loop_active"] is True
    assert hass.states.get(MEDIA_PLAYER).attributes[ATTR_MEDIA_REPEAT] == RepeatMode.ALL
    assert hass.states.get(SONOS_CROSSFADE).state == STATE_ON
    assert await controller.async_shutdown()


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
    assert controller.status_attributes["session_started_at"] is None
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.STANDBY.value
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == 1

    await controller.async_set_level(SoothingLevel.LEVEL_2)

    assert controller.level is SoothingLevel.LEVEL_2
    assert controller.state is SootherState.SOOTHING
    assert controller.status_attributes["session_started_at"] == (
        "2026-07-11T12:00:00+00:00"
    )
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.LEVEL_2.value
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(LEVEL_2_PERCENT / 100)
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == initial_play_count + 1


async def test_baseline_preview_plays_in_standby_without_starting_session(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Independent Baseline playback leaves policy state and timer in Standby."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.STANDBY)
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await controller.async_set_baseline_preview(enabled=True)

    assert controller.baseline_previewing is True
    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.STANDBY
    assert controller.status_attributes["session_started_at"] is None
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count + 1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(BASELINE_PERCENT / 100)

    await controller.async_set_baseline_preview(enabled=False)

    assert controller.baseline_previewing is False
    assert controller.level is SoothingLevel.STANDBY
    assert controller.status_attributes["session_started_at"] is None
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_baseline_preview_cannot_run_beside_an_active_session(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The preview path never layers a second control mode onto a full session."""
    controller, calls = started_controller
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    with pytest.raises(ServiceValidationError):
        await controller.async_set_baseline_preview(enabled=True)

    assert controller.baseline_previewing is False
    assert controller.level is SoothingLevel.BASELINE
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count


async def test_starting_session_adopts_running_baseline_preview(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Starting Baseline promotes preview playback into a timed full session."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.STANDBY)
    await controller.async_set_baseline_preview(enabled=True)
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    await controller.async_set_level(SoothingLevel.BASELINE)

    assert controller.baseline_previewing is False
    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.SOOTHING
    assert controller.status_attributes["session_started_at"] == (
        "2026-07-11T12:00:00+00:00"
    )
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count


@pytest.mark.parametrize("initial_level", ACTIVE_LEVELS, ids=lambda level: level.value)
async def test_trigger_toggle_sends_every_active_level_to_standby(
    started_controller: tuple[NurserySootherController, RecordedCalls],
    initial_level: SoothingLevel,
) -> None:
    """A configured toggle turns off every active soothing level."""
    controller, calls = started_controller
    await controller.async_set_level(initial_level)
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await controller.async_toggle_from_trigger()

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.STANDBY
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.STANDBY.value
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_trigger_toggle_starts_baseline_from_standby(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A configured toggle starts the conservative Baseline output when off."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.STANDBY)
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await controller.async_toggle_from_trigger()

    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.SOOTHING
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.BASELINE.value
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count + 1
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(BASELINE_PERCENT / 100)


@pytest.mark.parametrize(
    ("initial_level", "expected_level", "expected_volume"),
    [
        pytest.param(
            SoothingLevel.BASELINE,
            SoothingLevel.LEVEL_1,
            LEVEL_1_PERCENT,
            id="baseline-to-level-1",
        ),
        pytest.param(
            SoothingLevel.LEVEL_1,
            SoothingLevel.LEVEL_2,
            LEVEL_2_PERCENT,
            id="level-1-to-level-2",
        ),
        pytest.param(
            SoothingLevel.LEVEL_2,
            SoothingLevel.LEVEL_3,
            LEVEL_3_PERCENT,
            id="level-2-to-level-3",
        ),
        pytest.param(
            SoothingLevel.LEVEL_3,
            SoothingLevel.LEVEL_4,
            LEVEL_4_PERCENT,
            id="level-3-to-level-4",
        ),
    ],
)
async def test_trigger_increase_advances_one_level_while_locked(
    started_controller: tuple[NurserySootherController, RecordedCalls],
    initial_level: SoothingLevel,
    expected_level: SoothingLevel,
    expected_volume: float,
) -> None:
    """A configured increase directly advances exactly one level."""
    controller, calls = started_controller
    await controller.async_set_level(initial_level)
    await controller.async_set_locked(locked=True)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await controller.async_increase_from_trigger()

    assert controller.locked is True
    assert controller.level is expected_level
    assert controller.entry.options[CONF_LEVEL] == expected_level.value
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(expected_volume / 100)


@pytest.mark.parametrize(
    "boundary_level",
    [SoothingLevel.STANDBY, SoothingLevel.LEVEL_4],
    ids=("standby", "level-4"),
)
async def test_trigger_increase_is_a_no_op_at_boundaries(
    started_controller: tuple[NurserySootherController, RecordedCalls],
    boundary_level: SoothingLevel,
) -> None:
    """An increase neither starts playback nor exceeds the maximum level."""
    controller, calls = started_controller
    await controller.async_set_level(boundary_level)
    media_count = len(calls.media)

    await controller.async_increase_from_trigger()

    assert controller.level is boundary_level
    assert controller.entry.options[CONF_LEVEL] == boundary_level.value
    assert len(calls.media) == media_count


async def test_concurrent_trigger_increases_serialize_level_selection(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Queued increases each select the next level under the controller lock."""
    controller, calls = started_controller
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    async with controller._lock:  # noqa: SLF001
        increases = [
            asyncio.create_task(controller.async_increase_from_trigger())
            for _ in range(2)
        ]
        await asyncio.sleep(0)
    await asyncio.gather(*increases)

    assert controller.level is SoothingLevel.LEVEL_2
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.LEVEL_2.value
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 2


@pytest.mark.parametrize(
    ("initial_level", "expected_level", "expected_volume"),
    [
        pytest.param(
            SoothingLevel.LEVEL_1,
            SoothingLevel.BASELINE,
            BASELINE_PERCENT,
            id="level-1-to-baseline",
        ),
        pytest.param(
            SoothingLevel.LEVEL_2,
            SoothingLevel.LEVEL_1,
            LEVEL_1_PERCENT,
            id="level-2-to-level-1",
        ),
        pytest.param(
            SoothingLevel.LEVEL_3,
            SoothingLevel.LEVEL_2,
            LEVEL_2_PERCENT,
            id="level-3-to-level-2",
        ),
        pytest.param(
            SoothingLevel.LEVEL_4,
            SoothingLevel.LEVEL_3,
            LEVEL_3_PERCENT,
            id="level-4-to-level-3",
        ),
    ],
)
async def test_trigger_decrease_moves_one_level_while_locked(
    started_controller: tuple[NurserySootherController, RecordedCalls],
    initial_level: SoothingLevel,
    expected_level: SoothingLevel,
    expected_volume: float,
) -> None:
    """A decrease gesture is direct parent control and moves one level."""
    controller, calls = started_controller
    await controller.async_set_level(initial_level)
    await controller.async_set_locked(locked=True)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await controller.async_decrease_from_trigger()

    assert controller.locked is True
    assert controller.level is expected_level
    assert controller.entry.options[CONF_LEVEL] == expected_level.value
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(expected_volume / 100)


@pytest.mark.parametrize(
    "boundary_level",
    [SoothingLevel.STANDBY, SoothingLevel.BASELINE],
    ids=("standby", "baseline"),
)
async def test_trigger_decrease_is_a_no_op_at_boundaries(
    started_controller: tuple[NurserySootherController, RecordedCalls],
    boundary_level: SoothingLevel,
) -> None:
    """A decrease gesture neither starts playback nor enters Standby."""
    controller, calls = started_controller
    await controller.async_set_level(boundary_level)
    media_count = len(calls.media)

    await controller.async_decrease_from_trigger()

    assert controller.level is boundary_level
    assert controller.entry.options[CONF_LEVEL] == boundary_level.value
    assert len(calls.media) == media_count


async def test_concurrent_trigger_decreases_serialize_level_selection(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Queued decrease gestures each select the prior level under the lock."""
    controller, calls = started_controller
    await controller.async_set_level(SoothingLevel.LEVEL_3)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    async with controller._lock:  # noqa: SLF001
        decreases = [
            asyncio.create_task(controller.async_decrease_from_trigger())
            for _ in range(2)
        ]
        await asyncio.sleep(0)
    await asyncio.gather(*decreases)

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.LEVEL_1.value
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 2


async def test_trigger_increase_preserves_safe_level_failure_handling(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A rejected physical increase cannot publish its requested level."""
    controller, calls = started_controller

    @callback
    def _fail_volume(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected trigger volume"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_VOLUME_SET, _fail_volume)

    with pytest.raises(HomeAssistantError) as error:
        await controller.async_increase_from_trigger()

    assert error.value.translation_key == "level_change_failed"
    assert controller.level is SoothingLevel.BASELINE
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.BASELINE.value
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    hass.services.async_register("media_player", SERVICE_VOLUME_SET, calls.media.append)


async def test_configured_event_triggers_dispatch_and_unsubscribe(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Generic HA triggers dispatch every action and detach during shutdown."""
    controller, _ = started_controller
    controller.toggle_triggers = [
        {"platform": "event", "event_type": "test_physical_toggle"}
    ]
    controller.increase_level_triggers = [
        {"platform": "event", "event_type": "test_physical_increase"}
    ]
    controller.decrease_level_triggers = [
        {"platform": "event", "event_type": "test_physical_decrease"}
    ]
    await controller._async_attach_physical_control_triggers()  # noqa: SLF001

    hass.bus.async_fire("test_physical_increase")
    await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert controller.level is SoothingLevel.LEVEL_1

    hass.bus.async_fire("test_physical_decrease")
    await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert controller.level is SoothingLevel.BASELINE

    await controller.async_set_level(SoothingLevel.STANDBY)
    hass.bus.async_fire("test_physical_toggle")
    await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert controller.level is SoothingLevel.BASELINE
    assert controller.diagnostics["physical_control"] == {
        "configured": True,
        "toggle_triggers_attached": True,
        "increase_level_triggers_attached": True,
        "decrease_level_triggers_attached": True,
    }

    assert await controller.async_shutdown()
    hass.bus.async_fire("test_physical_toggle")
    await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert controller.level is SoothingLevel.BASELINE


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

    with pytest.raises(HomeAssistantError):
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

    with pytest.raises(HomeAssistantError):
        await controller.async_set_level(SoothingLevel.LEVEL_1)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.entry.options[CONF_LEVEL] == SoothingLevel.STANDBY.value
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    await _advance(hass, 16)


async def test_failed_parent_level_change_reports_noncritical_stop_failure(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A failed replacement stop is reported instead of silently reverting."""
    controller, calls = started_controller
    controller.sounds[SoothingLevel.LEVEL_1] = {
        ATTR_MEDIA_CONTENT_ID: (
            "media-source://media_source/local/level-1-white-noise.mp3"
        ),
        "media_content_type": "audio/mpeg",
    }

    @callback
    def _fail_stop(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected stop"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_MEDIA_STOP, _fail_stop)

    with pytest.raises(HomeAssistantError) as error:
        await controller.async_set_level(SoothingLevel.LEVEL_1)

    assert error.value.translation_key == "level_change_failed"
    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    hass.services.async_register("media_player", SERVICE_MEDIA_STOP, calls.media.append)


async def test_manual_first_pulse_immediately_suggests_exact_next_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Manual mode immediately explains one event without changing the level."""
    controller, calls = started_controller
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await _cry_pulse(hass)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.RESPONDING
    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count
    notifications = _incident_notifications(calls)
    assert len(notifications) == PARENT_COUNT
    for notification in notifications:
        assert "1 cry event" in notification.data["message"]
        assert "Level 1" in notification.data["message"]
        assert ATTR_ENTITY_ID not in notification.data["data"]
        assert notification.data["data"]["image"] == f"/api/camera_proxy/{CAMERA}"
        assert notification.data["data"]["url"] == f"entityId:{CAMERA}"
        assert notification.data["data"]["clickAction"] == f"entityId:{CAMERA}"
        titles = {action["title"] for action in notification.data["data"]["actions"]}
        assert any("Level 1" in title for title in titles)
        assert "Acknowledge" not in titles

    await _advance(hass, 9)
    assert len(_incident_notifications(calls)) == PARENT_COUNT


async def test_status_attributes_expose_evidence_and_exact_utc_deadlines(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Status consumers receive thresholds and clocks, never ticking seconds."""
    controller, _ = started_controller

    initial = controller.status_attributes
    assert initial["explanation"] == PolicyExplanation.SOOTHING.value
    assert initial["evidence"] == {
        "events": 0,
        "active_seconds": 0.0,
        "event_threshold": 1,
        "active_seconds_threshold": 8.0,
        "sensor_active": False,
        "observed_at": "2026-07-11T12:00:00+00:00",
    }
    assert initial["countdowns"] == {}
    assert initial["next_countdown"] is None
    assert initial["next_countdown_at"] is None

    await _cry_pulse(hass)
    responding = controller.status_attributes
    assert responding["explanation"] == PolicyExplanation.CAREGIVER_DECISION.value
    assert responding["evidence"]["events"] == 0
    assert responding["countdowns"] == {
        "level_dwell": "2026-07-11T12:00:20+00:00",
        "cry_gap": "2026-07-11T12:01:00+00:00",
        "attention_deadline": "2026-07-11T12:02:30+00:00",
    }
    assert responding["next_countdown"] == "level_dwell"
    assert responding["next_countdown_at"] == "2026-07-11T12:00:20+00:00"
    assert responding["evidence"]["event_threshold"] == 1
    assert responding["evidence"]["active_seconds_threshold"] == (
        controller_module.ESCALATION_CRY_ACTIVE_SECONDS_THRESHOLD
    )
    assert responding["countdowns"]["level_dwell"] == (
        controller._level_dwell_at().isoformat()  # noqa: SLF001
    )


async def test_provisional_and_quiet_countdowns_follow_timer_lifecycle(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Provisional and quiet deadlines disappear exactly when timers end."""
    controller, _ = started_controller
    await controller.async_set_automatic(enabled=True)
    await _cry_pulse(hass)

    provisional = controller.status_attributes
    assert provisional["explanation"] == PolicyExplanation.PROVISIONAL_RESPONSE
    assert provisional["countdowns"] == {
        "confirmation_gate": "2026-07-11T12:00:08+00:00",
        "provisional_rollback": "2026-07-11T12:00:25+00:00",
        "cry_gap": "2026-07-11T12:01:00+00:00",
    }

    await _advance(hass, 26)
    rolled_back = controller.status_attributes
    assert "confirmation_gate" not in rolled_back["countdowns"]
    assert "provisional_rollback" not in rolled_back["countdowns"]
    assert rolled_back["countdowns"] == {"cry_gap": "2026-07-11T12:01:00+00:00"}

    await _advance(hass, 40)
    settling = controller.status_attributes
    assert settling["explanation"] == PolicyExplanation.QUIET_STEP_DOWN
    assert settling["countdowns"] == {"quiet_step_down": "2026-07-11T12:02:00+00:00"}

    await controller.async_set_level(SoothingLevel.STANDBY)
    standby = controller.status_attributes
    assert standby["explanation"] == PolicyExplanation.STANDBY
    assert standby["countdowns"] == {}


async def test_expired_confirmation_gate_is_removed_without_new_cry_evidence(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A one-event candidate refreshes status when its debounce gate expires."""
    controller, _ = started_controller
    await controller.async_set_level(SoothingLevel.LEVEL_1)
    await controller.async_set_automatic(enabled=True)
    await _cry_pulse(hass)

    assert "confirmation_gate" in controller.status_attributes["countdowns"]
    assert controller.diagnostics["timers"]["evidence"] is True

    await _advance(hass, controller.settings.debounce_seconds + 1)

    status = controller.status_attributes
    assert status["explanation"] == (PolicyExplanation.GATHERING_INITIAL_EVIDENCE)
    assert "confirmation_gate" not in status["countdowns"]
    assert status["countdowns"] == {"cry_gap": "2026-07-11T12:01:00+00:00"}
    assert controller.diagnostics["timers"]["evidence"] is False


async def test_level_lock_explanation_cancels_policy_change_countdowns(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A lock is explicit and removes clocks for policy-driven level changes."""
    controller, _ = started_controller
    await controller.async_set_automatic(enabled=True)
    await _cry_pulse(hass)
    await controller.async_set_locked(locked=True)

    status = controller.status_attributes
    assert status["explanation"] == PolicyExplanation.LEVEL_LOCKED
    assert "provisional_rollback" not in status["countdowns"]
    assert "quiet_step_down" not in status["countdowns"]


async def test_settling_timer_starts_only_after_cry_episode_ends(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Cry activity schedules its gap clock without a redundant settling clock."""
    controller, _ = started_controller

    await _set_cry(hass, "on")

    assert controller.diagnostics["cry_episode_active"] is True
    assert controller.diagnostics["timers"]["settling"] is False

    await _advance(hass, controller.settings.cry_gap_seconds + 1)

    assert controller.diagnostics["cry_episode_active"] is True
    assert controller.diagnostics["timers"]["settling"] is False

    await _set_cry(hass, "off")
    await _advance(hass, controller.settings.cry_gap_seconds + 1)

    assert controller.diagnostics["cry_episode_active"] is False
    assert controller.diagnostics["timers"]["settling"] is True


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


async def test_failed_notification_level_action_logs_without_listener_error(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rejected phone level action is contained at the event-bus boundary."""
    controller, calls = started_controller
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)
    action = _action_containing(_incident_notifications(calls)[0], "Level 1")

    @callback
    def _fail_volume(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected notification action"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_VOLUME_SET, _fail_volume)

    with caplog.at_level(logging.WARNING):
        hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": action})
        await hass.async_block_till_done()

    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.ATTENTION_REQUIRED
    action_log = next(
        record
        for record in caplog.records
        if record.getMessage()
        == "Nursery notification level action failed for level_1 (HomeAssistantError)"
    )
    assert action_log.exc_info is None


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


async def test_enabling_automatic_after_manual_alert_requires_fresh_evidence(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Enabling automatic cannot reuse the event behind a manual alert."""
    controller, calls = started_controller
    await _cry_pulse(hass)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.RESPONDING
    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    assert controller.diagnostics["cry_episode_confirmed"] is True
    assert controller.diagnostics["provisional_level_1"] is False
    assert len(_incident_notifications(calls)) == PARENT_COUNT

    await controller.async_set_automatic(enabled=True)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.RESPONDING
    assert controller.recommendation is Recommendation.OBSERVE
    assert controller.diagnostics["provisional_level_1"] is False
    assert controller.diagnostics["timers"]["provisional"] is False
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count
    assert len(_clear_notifications(calls)) == PARENT_COUNT


async def test_unconfirmed_provisional_level_1_returns_after_separate_timeout(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A lone event gets only one bounded 25-second Level 1 response."""
    controller, calls = started_controller
    await controller.async_set_automatic(enabled=True)
    await _cry_pulse(hass)

    await _advance(hass, 24)
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


async def test_provisional_level_1_runs_only_once_per_sparse_cry_episode(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Sparse events cannot repeatedly restart the episode's provisional output."""
    controller, calls = started_controller
    await controller.async_set_automatic(enabled=True)
    await _cry_pulse(hass)

    await _advance(hass, 26)
    assert controller.level is SoothingLevel.BASELINE
    assert controller.diagnostics["initial_level_1_applied"] is True
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    for delay in (19, 40):
        await _advance(hass, delay)
        await _cry_pulse(hass)
        assert controller.level is SoothingLevel.BASELINE

    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count
    assert not _incident_notifications(calls)

    await _advance(hass, controller.settings.cry_gap_seconds + 1)
    await _cry_pulse(hass)

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.diagnostics["provisional_level_1"] is True


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


async def test_pending_quiet_gap_gets_bounded_attention_grace(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A nearly complete quiet gap resolves before parent attention fires."""
    controller, calls = started_controller
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await _advance(hass, 40)
    await controller.async_simulate_cry_event()
    await _advance(hass, 50)
    await controller.async_simulate_cry_event()
    await _advance(hass, 8)
    await controller.async_simulate_cry_event()
    await _advance(hass, 52)

    assert controller.state is SootherState.RESPONDING
    assert not controller.attention_required
    assert controller.diagnostics["timers"]["attention"] is True
    countdowns = controller.status_attributes["countdowns"]
    attention_at = dt_util.parse_datetime(countdowns["attention_deadline"])
    cry_gap_at = dt_util.parse_datetime(countdowns["cry_gap"])
    assert attention_at is not None
    assert cry_gap_at is not None
    assert timedelta(0) < attention_at - cry_gap_at <= timedelta(seconds=0.01)

    await _advance(hass, 9)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING
    assert not controller.attention_required
    assert controller.diagnostics["timers"]["attention"] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_attention_grace_cannot_be_extended_by_fresh_events(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Fresh evidence during the one-time quiet grace cannot defer attention."""
    controller, calls = started_controller
    await _initial_cry_pulses(hass)
    await _advance(hass, 9)
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await _advance(hass, 40)
    await controller.async_simulate_cry_event()
    await _advance(hass, 50)
    await controller.async_simulate_cry_event()
    await _advance(hass, 8)
    await controller.async_simulate_cry_event()
    await _advance(hass, 52)
    await _advance(hass, 4)
    await controller.async_simulate_cry_event()
    await _advance(hass, 4)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.ATTEND
    assert controller.attention_required
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_unresolved_150_second_episode_enters_standby_and_attention(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A held cry reaches the fixed safety cutoff and stops owned playback."""
    controller, calls = started_controller
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
    await controller.async_set_locked(locked=True)

    await _set_cry(hass, "on")
    assert controller.recommendation is Recommendation.INCREASE_LEVEL

    await _advance(hass, 149)
    assert controller.level is SoothingLevel.BASELINE

    await _advance(hass, 2)

    assert controller.level is SoothingLevel.STANDBY
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.ATTEND
    assert controller.attention_required
    assert controller.locked is True
    assert controller.status_attributes["countdowns"] == {}
    assert controller.status_attributes["explanation"] == (
        PolicyExplanation.ATTENTION_REQUIRED
    )
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_simulated_cry_is_one_immediate_manual_event_not_a_virtual_state(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One test press sends a manual alert without creating held cry time."""
    controller, calls = started_controller

    await controller.async_simulate_cry_event()
    await _advance(hass, 9)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    notifications = _incident_notifications(calls)
    assert len(notifications) == PARENT_COUNT
    assert all("Simulated" in call.data["message"] for call in notifications)
    assert all("1 cry event" in call.data["message"] for call in notifications)
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


async def test_second_simulated_manual_event_waits_for_level_dwell(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A second test press cannot immediately duplicate the manual alert."""
    controller, calls = started_controller

    await controller.async_simulate_cry_event()
    assert len(_incident_notifications(calls)) == PARENT_COUNT

    await controller.async_simulate_cry_event()
    await _advance(hass, 9)

    assert controller.level is SoothingLevel.BASELINE
    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    notifications = _incident_notifications(calls)
    assert len(notifications) == PARENT_COUNT
    assert all("Simulated" in call.data["message"] for call in notifications)
    assert all("1 cry event" in call.data["message"] for call in notifications)
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


async def test_level_lock_blocks_automatic_response_but_allows_parent_level(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Lock freezes policy output while an exact parent selection still works."""
    controller, calls = started_controller
    await controller.async_set_automatic(enabled=True)
    await controller.async_set_locked(locked=True)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await _initial_cry_pulses(hass)
    await _advance(hass, 9)

    assert controller.locked is True
    assert controller.entry.options[CONF_LEVEL_LOCK] is True
    assert controller.level is SoothingLevel.BASELINE
    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    await controller.async_set_level(SoothingLevel.LEVEL_2)

    assert controller.locked is True
    assert controller.level is SoothingLevel.LEVEL_2


async def test_unlock_releases_unconfirmed_provisional_without_double_increase(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Unlock resumes rollback and later confirmation cannot jump to Level 2."""
    controller, _ = started_controller
    await controller.async_set_automatic(enabled=True)
    await _cry_pulse(hass)
    assert controller.level is SoothingLevel.LEVEL_1

    await controller.async_set_locked(locked=True)
    await _advance(hass, 26)

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.diagnostics["provisional_level_1"] is False
    assert controller.diagnostics["initial_level_1_applied"] is True

    await controller.async_set_locked(locked=False)
    assert controller.level is SoothingLevel.BASELINE

    await _cry_pulse(hass)

    assert controller.level is SoothingLevel.LEVEL_1
    assert controller.diagnostics["cry_episode_confirmed"] is True
    assert controller.diagnostics["initial_level_1_applied"] is False


async def test_level_lock_defers_quiet_downshift_until_unlocked(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Quiet cannot lower a locked level and gets a fresh interval on unlock."""
    controller, _ = started_controller
    await controller.async_set_level(SoothingLevel.LEVEL_3)
    await controller.async_set_locked(locked=True)
    await _initial_cry_pulses(hass)
    await _advance(hass, 190)

    assert controller.level is SoothingLevel.LEVEL_3
    assert controller.diagnostics["timers"]["settling"] is False

    await controller.async_set_locked(locked=False)
    await _advance(hass, 119)
    assert controller.level is SoothingLevel.LEVEL_3

    await _advance(hass, 2)
    assert controller.level is SoothingLevel.LEVEL_2


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


@pytest.mark.parametrize("invalid_minutes", [0.25, 2.25, 60.5, float("inf")])
async def test_invalid_attention_minute_updates_are_rejected(
    started_controller: tuple[NurserySootherController, RecordedCalls],
    invalid_minutes: float,
) -> None:
    """The live duration control preserves its bounded half-minute contract."""
    controller, _ = started_controller
    original_seconds = controller.settings.attention_seconds

    with pytest.raises(ServiceValidationError) as error:
        await controller.async_set_attention_minutes(invalid_minutes)

    assert error.value.translation_key == "invalid_attention_minutes"
    assert controller.settings.attention_seconds == original_seconds
    assert controller.entry.options[CONF_ATTENTION_SECONDS] == original_seconds


async def test_attention_minutes_persist_without_moving_live_deadline(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A new setting applies to future episodes, not an owned base deadline."""
    controller, _ = started_controller
    await controller.async_set_locked(locked=True)
    await _set_cry(hass, "on")
    deadline = controller.status_attributes["countdowns"]["attention_deadline"]

    await controller.async_set_attention_minutes(UPDATED_ATTENTION_MINUTES)

    assert controller.settings.attention_seconds == UPDATED_ATTENTION_SECONDS
    assert controller.entry.options[CONF_ATTENTION_SECONDS] == UPDATED_ATTENTION_SECONDS
    assert controller.status_attributes["countdowns"]["attention_deadline"] == deadline


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

    assert controller.recommendation is Recommendation.INCREASE_LEVEL
    notifications = _incident_notifications(calls)
    assert len(notifications) == PARENT_COUNT
    assert all("1 cry event" in call.data["message"] for call in notifications)


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
    notification_count = len(_incident_notifications(calls))
    assert await controller.async_shutdown()
    media_count = len(calls.media)

    await _advance(hass, 200)
    await _cry_pulse(hass)

    assert len(_incident_notifications(calls)) == notification_count
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


async def test_media_player_unavailable_then_idle_restarts_before_soothing(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Dependency recovery cannot report Soothing while stale ownership is idle."""
    controller, calls = started_controller
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    hass.states.async_set(MEDIA_PLAYER, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert controller.state is SootherState.ATTENTION_REQUIRED

    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
    )
    await hass.async_block_till_done()

    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count + 1
    assert controller.diagnostics["playback_owned"] is True
    assert controller.state is SootherState.SOOTHING


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

    with pytest.raises(HomeAssistantError):
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

    with pytest.raises(HomeAssistantError):
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
    with pytest.raises(HomeAssistantError):
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
