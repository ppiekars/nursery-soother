"""Behavior and safety tests for the Nursery Soother controller."""

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
    CONF_BOOST_VOLUME,
    CONF_CAMERA,
    CONF_COOLDOWN_SECONDS,
    CONF_CRY_SENSOR,
    CONF_DEBOUNCE_SECONDS,
    CONF_ENABLED,
    CONF_ESCALATION_SECONDS,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_SETTLING_SECONDS,
    CONF_WHITE_NOISE,
    DEFAULT_OPTIONS,
    DOMAIN,
    ENTRY_VERSION,
    EVENT_NOTIFICATION_ACTION,
    NAME,
)
from custom_components.nursery_soother.controller import NurserySootherController
from custom_components.nursery_soother.models import Recommendation, SootherState

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

CRY_SENSOR = "binary_sensor.nursery_crying"
CAMERA = "camera.nursery"
MEDIA_PLAYER = "media_player.nursery"
PARENT_ONE = "notify.parent_one"
PARENT_TWO = "notify.parent_two"
PARENT_COUNT = 2
ACTION_COUNT = 3
BASELINE_PERCENT = 20
BASELINE_LEVEL = BASELINE_PERCENT / 100
UPDATED_BOOST_PERCENT = 35

CONFIG_DATA = {
    CONF_CRY_SENSOR: CRY_SENSOR,
    CONF_CAMERA: CAMERA,
    CONF_MEDIA_PLAYER: MEDIA_PLAYER,
    CONF_WHITE_NOISE: {
        "media_content_id": "media-source://media_source/local/white-noise.mp3",
        "media_content_type": "audio/mpeg",
    },
    CONF_NOTIFY_TARGETS: [PARENT_ONE, PARENT_TWO],
}

FAST_OPTIONS = DEFAULT_OPTIONS | {
    CONF_ENABLED: True,
    CONF_DEBOUNCE_SECONDS: 10,
    CONF_COOLDOWN_SECONDS: 30,
    CONF_SETTLING_SECONDS: 20,
    CONF_ESCALATION_SECONDS: 30,
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


@dataclass
class RecordedCalls:
    """Service calls made by one controller under test."""

    media: list[ServiceCall] = field(default_factory=list)
    notifications: list[ServiceCall] = field(default_factory=list)


@pytest.fixture
async def started_controller(
    hass: HomeAssistant,
) -> AsyncGenerator[tuple[NurserySootherController, RecordedCalls]]:
    """Create a configured, enabled controller with real HA event listeners."""
    calls = RecordedCalls()

    @callback
    def _record_media(call: ServiceCall) -> None:
        calls.media.append(call)
        if call.service == SERVICE_PLAY_MEDIA:
            current_state = hass.states.get(MEDIA_PLAYER)
            attributes = (
                dict(current_state.attributes) if current_state is not None else {}
            )
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
        "playing",
        {
            "supported_features": int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "resolved://preexisting-track",
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


async def _restart_with_sonos_play_state(
    hass: HomeAssistant,
    controller: NurserySootherController,
    calls: RecordedCalls,
    *,
    media_content_id: str | None,
) -> Context:
    """Restart with Sonos reporting one play state before its service returns."""
    await controller.async_stop()
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
    await controller.async_set_enabled(enabled=True)
    await hass.async_block_till_done()
    return _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context


async def _advance(hass: HomeAssistant, seconds: int) -> None:
    """Advance Home Assistant's point-in-time listeners."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def _set_cry(hass: HomeAssistant, state: str) -> None:
    """Set the cry input and finish listener work."""
    hass.states.async_set(CRY_SENSOR, state)
    await hass.async_block_till_done()


async def test_start_recovers_at_baseline_without_stale_boost(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Enabled startup always sets baseline and starts the configured media."""
    controller, calls = started_controller

    assert controller.state is SootherState.BASELINE
    assert controller.recommendation is Recommendation.NONE
    volume_calls = _media_calls(calls, SERVICE_VOLUME_SET)
    assert len(volume_calls) == 1
    assert volume_calls[0].data[ATTR_MEDIA_VOLUME_LEVEL] == BASELINE_LEVEL
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == 1


async def test_suggestions_debounce_never_increases_automatically(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A stable cry suggests a boost but makes no automatic volume increase."""
    controller, calls = started_controller
    initial_volume_calls = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await _set_cry(hass, "on")
    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.WAIT

    await _advance(hass, 11)

    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.BOOST
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == initial_volume_calls
    notifications = _incident_notifications(calls)
    assert len(notifications) == PARENT_COUNT
    assert notifications[0].data["data"][ATTR_ENTITY_ID] == CAMERA
    assert notifications[0].data["data"]["image"] == (f"/api/camera_proxy/{CAMERA}")
    assert notifications[0].data["data"]["url"] == f"entityId:{CAMERA}"
    assert notifications[0].data["data"]["clickAction"] == f"entityId:{CAMERA}"
    action_sets = [call.data["data"]["actions"] for call in notifications]
    assert action_sets[0] == action_sets[1]
    assert len(action_sets[0]) == ACTION_COUNT
    assert all(controller.entry.entry_id in item["action"] for item in action_sets[0])

    await _advance(hass, 31)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.attention_required
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == initial_volume_calls

    # The Attention notification replaces the original suggestion. A delayed
    # Boost response from that earlier notification must now be stale.
    hass.bus.async_fire(
        EVENT_NOTIFICATION_ACTION,
        {"action": action_sets[0][0]["action"]},
    )
    await hass.async_block_till_done()
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == initial_volume_calls


async def test_simulated_cry_event_debounces_notifies_and_auto_releases(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One event uses real debounce, sends one test alert, and infers quiet."""
    controller, calls = started_controller
    initial_volume_calls = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await controller.async_simulate_cry_event()

    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.WAIT
    assert controller.diagnostics["timers"]["debounce"] is True
    assert controller.diagnostics["timers"]["simulated_cry_event"] is True
    assert not _incident_notifications(calls)

    await _advance(hass, 11)

    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.BOOST
    notifications = _incident_notifications(calls)
    assert len(notifications) == PARENT_COUNT
    assert all(
        call.data["message"].startswith("[Test] Simulated cry event")
        for call in notifications
    )
    assert controller.diagnostics["timers"]["escalation"] is False
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == initial_volume_calls

    await _advance(hass, 13)

    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING
    assert controller.diagnostics["timers"]["simulated_cry_event"] is False
    assert controller.diagnostics["timers"]["escalation"] is False
    assert controller.diagnostics["timers"]["settling"] is True

    await _advance(hass, 34)

    assert controller.state is SootherState.BASELINE
    assert controller.recommendation is Recommendation.NONE
    assert not controller.attention_required
    assert len(_incident_notifications(calls)) == PARENT_COUNT


async def test_repeated_simulated_cry_events_are_coalesced(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Repeated presses during one synthetic pulse cannot duplicate an alert."""
    controller, calls = started_controller
    await controller.async_simulate_cry_event()
    episode = controller._episode  # noqa: SLF001

    await controller.async_simulate_cry_event()
    await _advance(hass, 11)

    assert controller._episode == episode  # noqa: SLF001
    assert len(_incident_notifications(calls)) == PARENT_COUNT


async def test_acknowledge_during_simulated_event_cannot_strand_virtual_input(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An acknowledgement generation change still allows the pulse to release."""
    controller, calls = started_controller
    await controller.async_simulate_cry_event()

    await controller.async_acknowledge()
    await _advance(hass, 13)

    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING
    assert controller.diagnostics["timers"]["simulated_cry_event"] is False
    assert controller.diagnostics["timers"]["settling"] is True
    assert not _incident_notifications(calls)


async def test_acknowledge_after_simulated_notification_still_releases_event(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A post-notification acknowledgement cannot invalidate pulse cleanup."""
    controller, calls = started_controller
    await controller.async_simulate_cry_event()
    await _advance(hass, 11)

    await controller.async_acknowledge()
    await _advance(hass, 13)

    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING
    assert controller.diagnostics["timers"]["simulated_cry_event"] is False
    assert controller.diagnostics["timers"]["settling"] is True
    assert len(_incident_notifications(calls)) == PARENT_COUNT


async def test_simulated_event_cannot_escalate_with_one_second_deadline(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Synthetic-only input never creates persistent-cry attention."""
    controller, _ = started_controller
    controller.settings.escalation_seconds = 1

    await controller.async_simulate_cry_event()
    await _advance(hass, 11)

    assert controller.recommendation is Recommendation.BOOST
    assert controller.diagnostics["timers"]["escalation"] is False

    await _advance(hass, 13)

    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING
    assert not controller.attention_required


async def test_simulated_release_cannot_hide_notification_failure(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A failed test alert remains fail-safe after the synthetic input releases."""
    controller, _ = started_controller

    @callback
    def _fail_notification(call: ServiceCall) -> None:
        del call
        error_message = "test notification delivery failed"
        raise HomeAssistantError(error_message)

    hass.services.async_register("notify", "parent_one", _fail_notification)
    hass.services.async_register("notify", "parent_two", _fail_notification)
    await controller.async_simulate_cry_event()
    await _advance(hass, 11)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES

    await _advance(hass, 13)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["timers"]["simulated_cry_event"] is False
    assert controller.diagnostics["timers"]["settling"] is False


async def test_stop_cancels_simulated_event_and_disabled_simulation_is_rejected(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A synthetic event cannot leak into a disabled or later session."""
    controller, calls = started_controller
    await controller.async_simulate_cry_event()

    await controller.async_stop()
    await _advance(hass, 60)

    assert controller.state is SootherState.DISABLED
    assert not any(controller.diagnostics["timers"].values())
    assert not _incident_notifications(calls)
    with pytest.raises(ServiceValidationError):
        await controller.async_simulate_cry_event()


async def test_dependency_outage_cancels_simulated_event_without_hiding_failure(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An outage cancels the test pulse and keeps fail-safe state authoritative."""
    controller, calls = started_controller
    await controller.async_simulate_cry_event()

    hass.states.async_set(CAMERA, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    await _advance(hass, 60)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert not controller.dependencies_available
    assert not any(controller.diagnostics["timers"].values())
    assert not any(
        call.data["message"].startswith("[Test]")
        for call in _incident_notifications(calls)
    )

    hass.states.async_set(CAMERA, "idle")
    await hass.async_block_till_done()

    assert controller.dependencies_available
    assert controller.state is SootherState.BASELINE
    assert controller.recommendation is Recommendation.NONE


async def test_physical_cry_during_simulated_event_survives_auto_release(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A real cry keeps the episode active after the test pulse releases."""
    controller, calls = started_controller
    await controller.async_simulate_cry_event()

    await _set_cry(hass, "on")
    await _advance(hass, 11)

    assert all(
        not call.data["message"].startswith("[Test]")
        for call in _incident_notifications(calls)
    )
    assert controller.diagnostics["timers"]["escalation"] is True

    await _advance(hass, 13)

    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.BOOST
    assert controller.diagnostics["timers"]["simulated_cry_event"] is False
    assert controller.diagnostics["timers"]["escalation"] is True
    assert controller.diagnostics["timers"]["settling"] is False

    await _set_cry(hass, "off")

    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING


async def test_physical_cry_after_test_notification_starts_escalation(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A real cry joining a confirmed test event upgrades attention timing."""
    controller, _ = started_controller
    await controller.async_simulate_cry_event()
    await _advance(hass, 11)
    assert controller.diagnostics["timers"]["escalation"] is False

    await _set_cry(hass, "on")

    assert controller.diagnostics["timers"]["escalation"] is True
    await _advance(hass, 13)
    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.BOOST
    assert controller.diagnostics["timers"]["escalation"] is True


async def test_real_cry_ending_inside_test_pulse_cancels_escalation(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Synthetic overlap cannot keep real-source attention timing alive."""
    controller, _ = started_controller
    controller.settings.escalation_seconds = 1
    await controller.async_simulate_cry_event()
    await _advance(hass, 11)

    await _set_cry(hass, "on")
    assert controller.diagnostics["timers"]["escalation"] is True
    await _set_cry(hass, "off")
    assert controller.diagnostics["timers"]["escalation"] is False

    await _advance(hass, 13)

    assert controller.state is SootherState.SETTLING
    assert controller.recommendation is Recommendation.SETTLING
    assert not controller.attention_required


async def test_simulated_event_is_rejected_while_real_cry_is_active(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A test press cannot replace an episode already driven by real input."""
    controller, _ = started_controller
    await _set_cry(hass, "on")
    episode = controller._episode  # noqa: SLF001

    with pytest.raises(ServiceValidationError) as error:
        await controller.async_simulate_cry_event()

    assert error.value.translation_key == "cry_already_active"
    assert controller._episode == episode  # noqa: SLF001
    assert controller.diagnostics["timers"]["simulated_cry_event"] is False


async def test_false_alarm_cancels_debounce(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A cry shorter than debounce returns directly to baseline."""
    controller, calls = started_controller

    await _set_cry(hass, "on")
    await _set_cry(hass, "off")
    await _advance(hass, 60)

    assert controller.state is SootherState.BASELINE
    assert controller.recommendation is Recommendation.NONE
    assert not _incident_notifications(calls)
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == 1


async def test_parent_boost_then_quiet_returns_to_baseline(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Either parent's action applies one capped boost and quiet lowers it."""
    controller, calls = started_controller
    await _set_cry(hass, "on")
    await _advance(hass, 11)
    boost_action = _incident_notifications(calls)[0].data["data"]["actions"][0][
        "action"
    ]

    hass.bus.async_fire(
        EVENT_NOTIFICATION_ACTION,
        {"action": boost_action},
    )
    await hass.async_block_till_done()

    assert controller.state is SootherState.BOOST
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(0.3)
    clear_calls = [
        call
        for call in calls.notifications
        if call.data.get("message") == "clear_notification"
    ]
    assert len(clear_calls) == PARENT_COUNT

    await _set_cry(hass, "off")
    assert controller.state is SootherState.SETTLING
    await _advance(hass, 21)

    assert controller.state is SootherState.BASELINE
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(BASELINE_LEVEL)


async def test_acknowledge_preserves_quiet_boost_settling_timer(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Acknowledging a quiet boost cannot leave it active indefinitely."""
    controller, _ = started_controller

    assert await controller.async_boost()
    assert controller.diagnostics["timers"]["settling"] is True
    await controller.async_acknowledge()
    assert controller.diagnostics["timers"]["settling"] is True

    await _advance(hass, 21)

    assert controller.state is SootherState.BASELINE
    assert controller.recommendation is Recommendation.NONE


async def test_settling_recovers_idle_owned_playback_at_baseline(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An idle event racing quiet expiry cannot strand a boosted settling state."""
    controller, calls = started_controller
    assert await controller.async_boost()
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
    )

    await _advance(hass, 21)

    assert controller.state is SootherState.BASELINE
    assert controller.diagnostics["timers"]["settling"] is False
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(BASELINE_LEVEL)


async def test_cry_after_quiet_direct_boost_starts_observation(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A cry interrupts quiet settling without offering a second boost."""
    controller, calls = started_controller
    assert await controller.async_boost()

    await _set_cry(hass, "on")
    assert controller.state is SootherState.CRY_PENDING
    assert controller.diagnostics["timers"]["settling"] is False
    await _advance(hass, 11)

    assert controller.state is SootherState.BOOST
    assert controller.recommendation is Recommendation.OBSERVE
    actions = _incident_notifications(calls)[-1].data["data"]["actions"]
    assert {action["title"] for action in actions} == {
        "Baseline",
        "Acknowledge",
        "Stop",
    }


async def test_cry_resuming_during_boost_only_suggests_observation(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A boosted episode never presents a second Boost action."""
    controller, calls = started_controller
    await _set_cry(hass, "on")
    await _advance(hass, 11)
    boost_action = _incident_notifications(calls)[0].data["data"]["actions"][0][
        "action"
    ]
    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": boost_action})
    await hass.async_block_till_done()

    await _set_cry(hass, "off")
    assert controller.state is SootherState.SETTLING
    await _set_cry(hass, "on")
    await _advance(hass, 11)

    assert controller.state is SootherState.BOOST
    assert controller.recommendation is Recommendation.OBSERVE
    resumed_notifications = _incident_notifications(calls)[-PARENT_COUNT:]
    assert all(
        call.data["message"]
        == "Crying continues while the boost is active. Keep observing the nursery."
        for call in resumed_notifications
    )
    assert {
        action["title"] for action in resumed_notifications[0].data["data"]["actions"]
    } == {"Baseline", "Acknowledge", "Stop"}


async def test_manual_baseline_resets_boost_cooldown(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An explicit Baseline lets the parent apply another bounded boost."""
    controller, calls = started_controller

    assert await controller.async_boost()
    await controller.async_baseline()
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    assert await controller.async_boost()
    assert controller.state is SootherState.BOOST
    assert controller.recommendation is Recommendation.OBSERVE
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count + 1


async def test_fresh_enabled_session_resets_boost_cooldown(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Stop and re-enable cannot inherit a previous session's cooldown."""
    controller, _ = started_controller
    assert await controller.async_boost()

    await controller.async_stop()
    await controller.async_set_enabled(enabled=True)

    assert await controller.async_boost()
    assert controller.state is SootherState.BOOST


async def test_automatic_settling_retains_and_expires_cooldown_recommendation(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Automatic baseline retains rate limiting without a stale recommendation."""
    controller, calls = started_controller
    assert await controller.async_boost()
    await _advance(hass, 21)
    assert controller.state is SootherState.BASELINE
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    assert not await controller.async_boost()
    assert controller.recommendation is Recommendation.COOLDOWN
    assert controller.diagnostics["timers"]["cooldown"] is True
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    await _advance(hass, 31)

    assert controller.state is SootherState.BASELINE
    assert controller.recommendation is Recommendation.NONE
    assert controller.diagnostics["timers"]["cooldown"] is False


async def test_intervening_transition_replaces_cooldown_restore_target(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Cooldown expiry cannot restore a recommendation from an older state."""
    controller, _ = started_controller
    assert await controller.async_boost()
    await _advance(hass, 21)
    assert not await controller.async_boost()
    assert controller.diagnostics["timers"]["cooldown"] is True

    await controller.async_acknowledge()

    assert controller.recommendation is Recommendation.ACKNOWLEDGED
    assert controller.diagnostics["timers"]["cooldown"] is False
    assert not await controller.async_boost()
    assert controller.recommendation is Recommendation.COOLDOWN

    await _advance(hass, 31)

    assert controller.recommendation is Recommendation.ACKNOWLEDGED


async def test_acknowledge_from_one_parent_cancels_attention(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """One acknowledgement owns the shared episode and clears both phones."""
    controller, calls = started_controller
    await _set_cry(hass, "on")
    await _advance(hass, 11)
    actions = _incident_notifications(calls)[0].data["data"]["actions"]
    boost_action = actions[0]["action"]
    acknowledge_action = actions[2]["action"]

    hass.bus.async_fire(
        EVENT_NOTIFICATION_ACTION,
        {"action": acknowledge_action},
    )
    await hass.async_block_till_done()
    await _advance(hass, 60)

    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.ACKNOWLEDGED
    assert not controller.attention_required
    assert len(_incident_notifications(calls)) == PARENT_COUNT

    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))
    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": boost_action})
    await hass.async_block_till_done()
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_notification_failure_does_not_block_other_parent(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A failed phone action cannot prevent delivery to the other parent."""
    _, calls = started_controller

    @callback
    def _fail_notification(call: ServiceCall) -> None:
        del call
        error_message = "phone offline"
        raise HomeAssistantError(error_message)

    hass.services.async_register("notify", "parent_one", _fail_notification)
    await _set_cry(hass, "on")
    await _advance(hass, 11)

    notifications = _incident_notifications(calls)
    assert len(notifications) == 1
    assert notifications[0].service == "parent_two"


async def test_stale_phone_action_is_ignored(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An action from a completed episode cannot control a later episode."""
    controller, calls = started_controller
    await _set_cry(hass, "on")
    await _advance(hass, 11)
    stale_boost = _incident_notifications(calls)[0].data["data"]["actions"][0]["action"]
    await _set_cry(hass, "off")
    await _advance(hass, 21)
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": stale_boost})
    await hass.async_block_till_done()

    assert controller.state is SootherState.BASELINE
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_dependency_loss_fails_safe_from_boost(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Dependency loss cancels escalation, lowers a boost, and asks for help."""
    controller, calls = started_controller
    assert await controller.async_boost()

    hass.states.async_set(CAMERA, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert not controller.dependencies_available
    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(BASELINE_LEVEL)
    assert len(_incident_notifications(calls)) == PARENT_COUNT

    await _advance(hass, 60)
    assert controller.state is SootherState.ATTENTION_REQUIRED

    hass.states.async_set(CAMERA, "idle")
    await hass.async_block_till_done()
    assert controller.state is SootherState.BASELINE
    assert controller.dependencies_available


async def test_stop_then_enable_starts_a_fresh_baseline(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The enabled switch persists intent and cleanly restarts playback."""
    controller, calls = started_controller
    initial_play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    await controller.async_set_enabled(enabled=False)

    assert controller.state is SootherState.DISABLED
    assert controller.enabled is False
    assert controller.entry.options[CONF_ENABLED] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == 1

    await controller.async_set_enabled(enabled=True)

    assert controller.state is SootherState.BASELINE
    assert controller.enabled is True
    assert controller.entry.options[CONF_ENABLED] is True
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == initial_play_count + 1


async def test_enable_with_unavailable_dependency_fails_safe(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Enabling can persist intent during an outage but cannot start playback."""
    controller, calls = started_controller
    await controller.async_stop()
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))
    hass.states.async_set(CAMERA, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    await controller.async_set_enabled(enabled=True)

    assert controller.enabled is True
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count
    assert len(_incident_notifications(calls)) == PARENT_COUNT


async def test_baseline_media_failure_preserves_attention_state(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A failed baseline command cannot be overwritten by a success state."""
    controller, _ = started_controller

    @callback
    def _fail_volume(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected volume"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_VOLUME_SET, _fail_volume)

    await controller.async_baseline()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES


async def test_active_cry_on_restart_gets_fresh_debounce(
    hass: HomeAssistant,
) -> None:
    """Restart with cry on sets baseline and never replays a stale boost."""
    media_calls: list[ServiceCall] = []

    @callback
    def _record(call: ServiceCall) -> None:
        media_calls.append(call)
        if call.service == SERVICE_PLAY_MEDIA:
            current_state = hass.states.get(MEDIA_PLAYER)
            attributes = (
                dict(current_state.attributes) if current_state is not None else {}
            )
            attributes[ATTR_MEDIA_CONTENT_ID] = call.data[ATTR_MEDIA_CONTENT_ID]
            hass.states.async_set(
                MEDIA_PLAYER,
                "playing",
                attributes,
                context=call.context,
            )

    @callback
    def _ignore_notification(call: ServiceCall) -> None:
        del call

    for service in (
        SERVICE_VOLUME_SET,
        SERVICE_PLAY_MEDIA,
        SERVICE_MEDIA_STOP,
    ):
        hass.services.async_register("media_player", service, _record)
    hass.services.async_register("notify", "parent_one", _ignore_notification)
    hass.services.async_register("notify", "parent_two", _ignore_notification)
    hass.states.async_set(CRY_SENSOR, "on")
    hass.states.async_set(CAMERA, "idle")
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {"supported_features": int(SPEAKER_FEATURES)},
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

    assert controller.state is SootherState.CRY_PENDING
    assert controller.recommendation is Recommendation.WAIT
    volume_calls = [call for call in media_calls if call.service == SERVICE_VOLUME_SET]
    assert [call.data[ATTR_MEDIA_VOLUME_LEVEL] for call in volume_calls] == [
        BASELINE_LEVEL
    ]
    await controller.async_shutdown()


async def test_incomplete_migrated_entry_is_forced_disabled(
    hass: HomeAssistant,
) -> None:
    """Legacy data can load safely but cannot be enabled before reconfigure."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data={
            CONF_CRY_SENSOR: CRY_SENSOR,
            CONF_CAMERA: CAMERA,
            CONF_MEDIA_PLAYER: MEDIA_PLAYER,
        },
        options=FAST_OPTIONS,
        version=ENTRY_VERSION,
    )
    entry.add_to_hass(hass)
    controller = NurserySootherController(hass, entry)

    await controller.async_start()

    assert controller.state is SootherState.DISABLED
    assert controller.recommendation is Recommendation.CONFIGURE
    assert controller.enabled is False
    assert controller.entry.options[CONF_ENABLED] is False
    with pytest.raises(ServiceValidationError):
        await controller.async_set_enabled(enabled=True)
    await controller.async_shutdown()


async def test_shutdown_cancels_timers_and_state_listeners(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """No pending callback can act after the config entry unloads."""
    controller, calls = started_controller
    await _set_cry(hass, "on")
    await controller.async_shutdown()

    await _advance(hass, 60)
    await _set_cry(hass, "off")

    assert not _incident_notifications(calls)
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == 1
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == 1


async def test_reconfigure_shutdown_stops_the_original_speaker(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Changing stable data cannot orphan playback on the previous speaker."""
    controller, calls = started_controller
    hass.config_entries.async_update_entry(
        controller.entry,
        data=dict(controller.entry.data)
        | {CONF_MEDIA_PLAYER: "media_player.replacement"},
    )

    await controller.async_shutdown()

    stop_call = _media_calls(calls, SERVICE_MEDIA_STOP)[-1]
    assert stop_call.data[ATTR_ENTITY_ID] == MEDIA_PLAYER


async def test_continuous_playback_restarts_after_idle(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Playback owned by the integration is guarded while enabled."""
    _, calls = started_controller
    initial_play_calls = len(_media_calls(calls, SERVICE_PLAY_MEDIA))

    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {"supported_features": int(SPEAKER_FEATURES)},
    )
    await hass.async_block_till_done()

    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == initial_play_calls + 1


async def test_invalid_volume_relationship_is_rejected(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Runtime number entities cannot bypass the controller's safety ordering."""
    controller, calls = started_controller
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    with pytest.raises(ServiceValidationError):
        await controller.async_set_volume("baseline_volume", 31)

    assert controller.settings.baseline_volume == BASELINE_PERCENT
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_valid_volume_update_persists_and_invalid_key_is_rejected(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Number controls persist valid settings while accepting known keys only."""
    controller, calls = started_controller

    await controller.async_set_volume(CONF_BOOST_VOLUME, UPDATED_BOOST_PERCENT)

    assert controller.settings.boost_volume == UPDATED_BOOST_PERCENT
    assert controller.entry.options[CONF_BOOST_VOLUME] == UPDATED_BOOST_PERCENT
    assert (
        _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[ATTR_MEDIA_VOLUME_LEVEL]
        == BASELINE_LEVEL
    )

    with pytest.raises(ServiceValidationError):
        await controller.async_set_volume("not_a_volume", 10)


async def test_old_action_is_invalid_across_controller_sessions(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A per-runtime nonce prevents episode-number collisions after reload."""
    old_controller, calls = started_controller
    await _set_cry(hass, "on")
    await _advance(hass, 11)
    old_boost_action = _incident_notifications(calls)[0].data["data"]["actions"][0][
        "action"
    ]
    assert await old_controller.async_shutdown()

    new_controller = NurserySootherController(hass, old_controller.entry)
    await new_controller.async_start()
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": old_boost_action})
    await hass.async_block_till_done()

    assert new_controller.state is SootherState.CRY_PENDING
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count
    assert await new_controller.async_shutdown()


async def test_cry_attribute_update_cannot_clear_attention(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Only actual cry state edges can move the response state machine."""
    controller, _ = started_controller
    await _set_cry(hass, "on")
    await _advance(hass, 11)
    await _advance(hass, 31)
    assert controller.state is SootherState.ATTENTION_REQUIRED

    hass.states.async_set(CRY_SENSOR, "on", {"heartbeat": 1})
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.ATTEND


async def test_notification_service_loss_and_recovery(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Removing a parent action pauses policy until the service returns."""
    controller, calls = started_controller

    hass.services.async_remove("notify", "parent_one")
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert not controller.dependencies_available

    @callback
    def _record_notification(call: ServiceCall) -> None:
        calls.notifications.append(call)

    hass.services.async_register("notify", "parent_one", _record_notification)
    await hass.async_block_till_done()

    assert controller.state is SootherState.BASELINE
    assert controller.dependencies_available


async def test_redundant_enable_is_idempotent_during_boost(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A repeated switch turn-on cannot reset an active response episode."""
    controller, calls = started_controller
    assert await controller.async_boost()
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    await controller.async_set_enabled(enabled=True)

    assert controller.state is SootherState.BOOST
    assert controller.recommendation is Recommendation.OBSERVE
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_fresh_sonos_session_waits_for_id_then_accepts_boost(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A fresh session resets cooldown but still waits for verified Sonos media."""
    controller, calls = started_controller
    assert await controller.async_boost()
    play_context = await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id=None,
    )
    assert controller.diagnostics["playback_owned"] is True
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))
    assert not await controller.async_boost()
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

    assert controller.state is SootherState.BASELINE
    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["playback_interrupted"] is False
    assert await controller.async_boost()


async def test_stale_idle_signed_url_cannot_confirm_new_play(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The previous idle URL cannot preempt Sonos's no-ID then signed events."""
    controller, calls = started_controller
    await controller.async_stop()
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
    await controller.async_set_enabled(enabled=True)
    play_context = _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
        context=play_context,
    )
    await hass.async_block_till_done()
    assert controller.state is SootherState.BASELINE

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

    assert controller.state is SootherState.BASELINE
    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["playback_interrupted"] is False
    assert await controller.async_boost()


async def test_play_context_cannot_adopt_different_media(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A play-context refresh cannot make unchanged parent media ours."""
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
    await controller.async_stop()
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


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

    assert controller.state is SootherState.BASELINE
    assert controller.diagnostics["playback_owned"] is True
    assert controller.diagnostics["playback_interrupted"] is False
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert await controller.async_boost()

    await controller.async_stop()

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


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

    assert controller.state is SootherState.ATTENTION_REQUIRED
    await controller.async_stop()
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_pending_user_replay_of_same_file_is_external(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A user replay cannot claim the pending nursery playback generation."""
    controller, calls = started_controller
    await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id=None,
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

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.diagnostics["playback_owned"] is False
    await controller.async_stop()
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
    """Only the exact HA local path and non-auth query remain owned."""
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

    assert controller.state is SootherState.ATTENTION_REQUIRED
    await controller.async_stop()
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_sonos_spotify_takeover_is_never_stopped_or_volume_changed(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A genuinely different Sonos source still relinquishes playback safely."""
    controller, calls = started_controller
    await _restart_with_sonos_play_state(
        hass,
        controller,
        calls,
        media_content_id=SONOS_NURSERY_URL,
    )
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))
    volume_context = _media_calls(calls, SERVICE_VOLUME_SET)[-1].context

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "x-sonos-vli:RINCON_TEST:2,spotify:parent-session",
            "source": "Spotify Connect",
            "media_title": "Parent music",
        },
        context=volume_context,
    )
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["playback_owned"] is False
    with pytest.raises(ServiceValidationError):
        await controller.async_boost()
    with pytest.raises(ServiceValidationError):
        await controller.async_baseline()
    await controller.async_stop()
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count


async def test_external_media_replacement_is_not_stopped(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A definite manual media takeover relinquishes playback ownership."""
    controller, calls = started_controller
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
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    assert controller.state is SootherState.ATTENTION_REQUIRED
    with pytest.raises(ServiceValidationError):
        await controller.async_boost()
    with pytest.raises(ServiceValidationError):
        await controller.async_baseline()
    await controller.async_set_volume(CONF_BOOST_VOLUME, UPDATED_BOOST_PERCENT)
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count

    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))
    hass.states.async_set(CAMERA, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    hass.states.async_set(CAMERA, "idle")
    await hass.async_block_till_done()
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count

    await _set_cry(hass, "on")
    assert controller.state is SootherState.ATTENTION_REQUIRED

    await controller.async_stop()

    assert controller.state is SootherState.DISABLED
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_paused_external_media_is_not_restarted(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A paused parent item cannot be overwritten by the playback watchdog."""
    controller, calls = started_controller
    play_count = len(_media_calls(calls, SERVICE_PLAY_MEDIA))
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    hass.states.async_set(
        MEDIA_PLAYER,
        "paused",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "resolved://parent-music",
        },
    )
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_PLAY_MEDIA)) == play_count
    await controller.async_stop()
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_live_takeover_is_reconciled_before_queued_state_callback(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A command cannot change replacement media while its event is queued."""
    controller, calls = started_controller
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: "resolved://parent-music",
        },
    )
    volume_count = len(_media_calls(calls, SERVICE_VOLUME_SET))

    with pytest.raises(ServiceValidationError):
        await controller.async_boost()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_VOLUME_SET)) == volume_count
    await hass.async_block_till_done()


async def test_external_media_without_content_id_is_not_stopped(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A no-ID external source is treated as an uncertain speaker takeover."""
    controller, calls = started_controller
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
    )
    await hass.async_block_till_done()
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    assert controller.state is SootherState.ATTENTION_REQUIRED
    await controller.async_stop()
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count


async def test_queued_no_id_takeover_is_reconciled_before_stop(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Stop cannot halt live external no-ID audio while its event is queued."""
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
    """A dedicated play context keeps no-ID Nursery audio safely stoppable."""
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

    context_history_size = 20
    for _ in range(context_history_size):
        assert await controller._async_call_media(  # noqa: SLF001
            SERVICE_VOLUME_SET,
            {ATTR_MEDIA_VOLUME_LEVEL: BASELINE_LEVEL},
        )
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    await controller.async_stop()

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_runtime_media_capability_loss_fails_safe(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A speaker that can no longer be stopped becomes an unhealthy dependency."""
    controller, _ = started_controller
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.VOLUME_SET
            )
        },
    )
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert not controller.dependencies_available

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
    )
    await hass.async_block_till_done()
    assert controller.dependencies_available


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


async def test_stop_uses_pause_fallback(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Stop falls back to pause when the accepted player lacks media_stop."""
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
            ATTR_MEDIA_CONTENT_ID: CONFIG_DATA[CONF_WHITE_NOISE][ATTR_MEDIA_CONTENT_ID],
        },
    )
    await hass.async_block_till_done()

    await controller.async_stop()

    assert controller.state is SootherState.DISABLED
    assert len(_media_calls(calls, SERVICE_MEDIA_PAUSE)) == 1


async def test_stop_failure_remains_attention_required(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A failed stop cannot claim Disabled while integration audio may continue."""
    controller, calls = started_controller

    @callback
    def _fail_stop(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected stop"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_MEDIA_STOP, _fail_stop)

    await controller.async_stop()

    assert controller.enabled is False
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES

    hass.services.async_register("media_player", SERVICE_MEDIA_STOP, calls.media.append)
    assert await controller.async_shutdown()


async def test_standard_commands_cannot_cross_shutdown(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Queued entity commands cannot issue effects after unload begins."""
    controller, calls = started_controller
    assert await controller.async_shutdown()
    media_count = len(calls.media)

    for command in (
        controller.async_boost,
        controller.async_baseline,
    ):
        with pytest.raises(ServiceValidationError):
            await command()
    with pytest.raises(ServiceValidationError):
        await controller.async_set_enabled(enabled=True)
    with pytest.raises(ServiceValidationError):
        await controller.async_set_volume(CONF_BOOST_VOLUME, UPDATED_BOOST_PERCENT)
    await controller.async_acknowledge()
    await controller.async_stop()

    assert len(calls.media) == media_count


async def test_failed_shutdown_keeps_controller_active_for_retry(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Unload refusal retains listeners and ownership until Stop can succeed."""
    controller, calls = started_controller

    @callback
    def _fail_stop(call: ServiceCall) -> None:
        del call
        error_message = "speaker offline"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_MEDIA_STOP, _fail_stop)

    assert not await controller.async_shutdown()
    assert await controller.async_boost()

    hass.services.async_register("media_player", SERVICE_MEDIA_STOP, calls.media.append)
    assert await controller.async_shutdown()


async def test_failed_boost_does_not_consume_cooldown(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Cooldown begins only after the speaker accepts the boost volume."""
    controller, calls = started_controller

    @callback
    def _fail_volume(call: ServiceCall) -> None:
        del call
        error_message = "speaker rejected boost"
        raise HomeAssistantError(error_message)

    hass.services.async_register("media_player", SERVICE_VOLUME_SET, _fail_volume)
    assert not await controller.async_boost()

    hass.services.async_register("media_player", SERVICE_VOLUME_SET, calls.media.append)
    assert await controller.async_boost()
    assert controller.state is SootherState.BOOST


async def test_defensive_volume_cap_survives_corrupt_runtime_settings(
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """The final media command remains capped even if memory is corrupted."""
    controller, calls = started_controller
    controller.settings.boost_volume = 80
    controller.settings.max_volume = 40

    assert await controller.async_boost()

    assert _media_calls(calls, SERVICE_VOLUME_SET)[-1].data[
        ATTR_MEDIA_VOLUME_LEVEL
    ] == pytest.approx(0.4)


async def test_all_notification_failures_enter_attention(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """No automatic response timer continues without any parent oversight."""
    controller, _ = started_controller

    @callback
    def _fail_notification(call: ServiceCall) -> None:
        del call
        error_message = "unexpected third-party notification failure"
        raise RuntimeError(error_message)

    hass.services.async_register("notify", "parent_one", _fail_notification)
    hass.services.async_register("notify", "parent_two", _fail_notification)
    await _set_cry(hass, "on")
    await _advance(hass, 11)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert controller.recommendation is Recommendation.CHECK_DEVICES
    assert controller.diagnostics["timers"]["escalation"] is False

    hass.states.async_set(CAMERA, "idle", {"motion": False})
    await hass.async_block_till_done()
    assert controller.state is SootherState.ATTENTION_REQUIRED


async def test_arbitrary_play_failure_is_isolated_and_compensated(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """An ambiguous third-party play failure triggers a compensating stop."""
    controller, calls = started_controller
    await controller.async_stop()
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

    await controller.async_set_enabled(enabled=True)
    await hass.async_block_till_done()

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_immediate_play_rejection_does_not_stop_parent_media(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A rejected play call cannot claim or stop unchanged parent audio."""
    controller, calls = started_controller
    await controller.async_stop()
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

    await controller.async_set_enabled(enabled=True)

    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count
    await _set_cry(hass, "on")
    await _set_cry(hass, "off")
    assert controller.state is SootherState.ATTENTION_REQUIRED
    assert not await controller.async_shutdown()
    await _advance(hass, 16)
    assert await controller.async_shutdown()


async def test_delayed_non_timeout_play_failure_is_compensated(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Every failed play context is monitored during the bounded grace window."""
    controller, calls = started_controller
    await controller.async_stop()
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    @callback
    def _reject_then_start_later(call: ServiceCall) -> None:
        calls.media.append(call)
        error_message = "speaker reported failure before its delayed state"
        raise RuntimeError(error_message)

    hass.services.async_register(
        "media_player", SERVICE_PLAY_MEDIA, _reject_then_start_later
    )
    await controller.async_set_enabled(enabled=True)
    failed_context = _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: CONFIG_DATA[CONF_WHITE_NOISE][ATTR_MEDIA_CONTENT_ID],
            "media_title": "late white noise",
        },
        context=Context(parent_id=failed_context.id),
    )
    await hass.async_block_till_done()

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1


async def test_successful_retry_waits_for_failed_play_window(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """A fresh enable cannot be mistaken for an older failed play call."""
    controller, calls = started_controller
    await controller.async_stop()

    @callback
    def _reject_play(call: ServiceCall) -> None:
        calls.media.append(call)
        error_message = "speaker rejected play"
        raise RuntimeError(error_message)

    hass.services.async_register("media_player", SERVICE_PLAY_MEDIA, _reject_play)
    await controller.async_set_enabled(enabled=True)
    await controller.async_stop()

    with pytest.raises(ServiceValidationError) as error:
        await controller.async_set_enabled(enabled=True)
    assert error.value.translation_key == "playback_settling"

    await _advance(hass, 16)

    @callback
    def _accept_play(call: ServiceCall) -> None:
        calls.media.append(call)
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            {
                ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
                ATTR_MEDIA_CONTENT_ID: call.data[ATTR_MEDIA_CONTENT_ID],
            },
            context=call.context,
        )

    hass.services.async_register("media_player", SERVICE_PLAY_MEDIA, _accept_play)
    await controller.async_set_enabled(enabled=True)
    await hass.async_block_till_done()

    assert controller.state is SootherState.BASELINE


async def test_timed_out_play_keeps_compensation_listener_until_stopped(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Idle-before-playing timeout events cannot escape unload compensation."""
    controller, calls = started_controller
    await controller.async_stop()
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    @callback
    def _time_out_play(call: ServiceCall) -> None:
        calls.media.append(call)
        raise TimeoutError

    hass.services.async_register("media_player", SERVICE_PLAY_MEDIA, _time_out_play)
    await controller.async_set_enabled(enabled=True)
    failed_context = _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context

    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
        context=failed_context,
    )
    await hass.async_block_till_done()
    assert not await controller.async_shutdown()

    hass.states.async_set(CAMERA, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: CONFIG_DATA[CONF_WHITE_NOISE][ATTR_MEDIA_CONTENT_ID],
        },
        context=failed_context,
    )
    await hass.async_block_till_done()

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1
    assert await controller.async_shutdown()


async def test_stale_idle_event_cannot_consume_late_play_compensation(
    hass: HomeAssistant,
    started_controller: tuple[NurserySootherController, RecordedCalls],
) -> None:
    """Back-to-back failed-context states retain proof until live audio stops."""
    controller, calls = started_controller
    await controller.async_stop()
    stop_count = len(_media_calls(calls, SERVICE_MEDIA_STOP))

    @callback
    def _time_out_play(call: ServiceCall) -> None:
        calls.media.append(call)
        raise TimeoutError

    hass.services.async_register("media_player", SERVICE_PLAY_MEDIA, _time_out_play)
    await controller.async_set_enabled(enabled=True)
    failed_context = _media_calls(calls, SERVICE_PLAY_MEDIA)[-1].context

    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES)},
        context=failed_context,
    )
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            ATTR_SUPPORTED_FEATURES: int(SPEAKER_FEATURES),
            ATTR_MEDIA_CONTENT_ID: CONFIG_DATA[CONF_WHITE_NOISE][ATTR_MEDIA_CONTENT_ID],
        },
        context=failed_context,
    )
    await hass.async_block_till_done()

    assert len(_media_calls(calls, SERVICE_MEDIA_STOP)) == stop_count + 1
