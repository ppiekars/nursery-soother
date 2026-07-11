"""Event-driven response controller for Nursery Soother."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_VOLUME_LEVEL,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.components.media_player.const import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
)
from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_ENTITY_ID,
    ATTR_SERVICE,
    ATTR_SUPPORTED_FEATURES,
    EVENT_SERVICE_REGISTERED,
    EVENT_SERVICE_REMOVED,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_STOP,
    SERVICE_VOLUME_SET,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_ACKNOWLEDGE,
    ACTION_BASELINE,
    ACTION_BOOST,
    ACTION_STOP,
    CONF_BASELINE_VOLUME,
    CONF_BOOST_VOLUME,
    CONF_CAMERA,
    CONF_CRY_SENSOR,
    CONF_MAX_VOLUME,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_WHITE_NOISE,
    DOMAIN,
    EVENT_NOTIFICATION_ACTION,
    MAX_VOLUME_PERCENT,
    NOTIFICATION_ACTION_PREFIX,
    NOTIFICATION_TAG_PREFIX,
)
from .models import Recommendation, SootherSettings, SootherState

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, State

_LOGGER = logging.getLogger(__name__)
SERVICE_CALL_TIMEOUT = 10
FAILED_PLAY_COMPENSATION_SECONDS = 15

type ControllerListener = Callable[[], None]

_REQUIRED_MEDIA_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET
)
_STOP_MEDIA_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.STOP | MediaPlayerEntityFeature.PAUSE
)


class NurserySootherController:
    """Coordinate nursery inputs, timers, speaker effects, and parents."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry[Any]) -> None:
        """Initialize a controller from one config entry."""
        self.hass = hass
        self.entry = entry
        self.settings = SootherSettings.from_options(dict(entry.options))
        self.cry_sensor = self._string_data(CONF_CRY_SENSOR)
        self.camera = self._string_data(CONF_CAMERA)
        self.media_player = self._string_data(CONF_MEDIA_PLAYER)
        configured_media = entry.data.get(CONF_WHITE_NOISE)
        self.white_noise = (
            dict(configured_media) if isinstance(configured_media, dict) else None
        )
        configured_targets = entry.data.get(CONF_NOTIFY_TARGETS)
        self.notify_targets = (
            tuple(target for target in configured_targets if isinstance(target, str))
            if isinstance(configured_targets, list)
            else ()
        )

        self.state = SootherState.DISABLED
        self.recommendation = Recommendation.ENABLE
        self._acknowledged = False
        self._boosted = False
        self._episode = 0
        self._session_id = secrets.token_hex(8)
        self._incident_active = False
        self._last_boost_at: datetime | None = None
        self._last_error: str | None = None
        self._last_reason = "initialized"
        self._last_transition_at = dt_util.utcnow()

        self._listeners: set[ControllerListener] = set()
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._cancel_debounce: CALLBACK_TYPE | None = None
        self._cancel_escalation: CALLBACK_TYPE | None = None
        self._cancel_settling: CALLBACK_TYPE | None = None
        self._dependency_issues: set[str] = set()
        self._owns_playback = False
        self._playback_interrupted = False
        self._owned_media_content_id: str | None = None
        self._owned_play_context_id: str | None = None
        self._awaiting_playback_confirmation = False
        self._pending_play_context_id: str | None = None
        self._failed_play_context_ids: set[str] = set()
        self._failed_play_expiries: dict[str, CALLBACK_TYPE] = {}
        self._media_context_ids: deque[str] = deque(maxlen=16)
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """Return whether the response loop is enabled."""
        return self.settings.enabled

    @property
    def configured(self) -> bool:
        """Return whether data added after the foundation release is present."""
        return (
            self.cry_sensor is not None
            and self.camera is not None
            and self.media_player is not None
            and isinstance(self.white_noise, dict)
            and isinstance(self.white_noise.get(ATTR_MEDIA_CONTENT_ID), str)
            and isinstance(self.white_noise.get(ATTR_MEDIA_CONTENT_TYPE), str)
            and bool(self.notify_targets)
        )

    @property
    def dependencies_available(self) -> bool:
        """Return whether all selected Home Assistant entities are usable."""
        return self.configured and not self._find_dependency_issues()

    @property
    def attention_required(self) -> bool:
        """Return whether the incident still needs a parent's acknowledgement."""
        return self.state is SootherState.ATTENTION_REQUIRED and not self._acknowledged

    @property
    def notification_tag(self) -> str:
        """Return the stable notification replacement tag for this entry."""
        return f"{NOTIFICATION_TAG_PREFIX}-{self.entry.entry_id}"

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return privacy-safe runtime diagnostics."""
        return {
            "state": self.state,
            "recommendation": self.recommendation,
            "enabled": self.enabled,
            "configured": self.configured,
            "dependencies_available": self.dependencies_available,
            "dependency_issue_types": sorted(self._dependency_issues),
            "acknowledged": self._acknowledged,
            "boosted": self._boosted,
            "playback_owned": self._owns_playback,
            "playback_interrupted": self._playback_interrupted,
            "timers": {
                "debounce": self._cancel_debounce is not None,
                "escalation": self._cancel_escalation is not None,
                "settling": self._cancel_settling is not None,
                "failed_play_compensation": bool(self._failed_play_context_ids),
            },
            "last_reason": self._last_reason,
            "last_transition_at": self._last_transition_at.isoformat(),
            "last_error_type": self._last_error,
        }

    @callback
    def async_add_listener(self, listener: ControllerListener) -> CALLBACK_TYPE:
        """Register an entity update listener."""
        self._listeners.add(listener)

        @callback
        def _remove_listener() -> None:
            self._listeners.discard(listener)

        return _remove_listener

    async def async_start(self) -> None:
        """Start listeners and recover into a conservative fresh state."""
        async with self._lock:
            await self._async_start_locked()

    async def _async_start_locked(self) -> None:
        """Start while serializing recovery with state events and commands."""
        self._started = True
        watched_entities = [
            entity_id
            for entity_id in (self.cry_sensor, self.camera, self.media_player)
            if entity_id is not None
        ]
        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass, watched_entities, self._async_state_changed
            )
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen(
                EVENT_NOTIFICATION_ACTION, self._async_notification_action
            )
        )
        for event_type in (EVENT_SERVICE_REGISTERED, EVENT_SERVICE_REMOVED):
            self._unsubscribers.append(
                self.hass.bus.async_listen(event_type, self._async_service_changed)
            )
        self._dependency_issues = self._find_dependency_issues()

        if not self.enabled:
            recommendation = (
                Recommendation.ENABLE if self.configured else Recommendation.CONFIGURE
            )
            self._transition(SootherState.DISABLED, recommendation, "safe startup")
            return

        if not self.configured:
            self.settings.enabled = False
            self._persist_settings()
            self._transition(
                SootherState.DISABLED,
                Recommendation.CONFIGURE,
                "incomplete migrated configuration",
            )
            return

        if self._dependency_issues:
            self._transition(
                SootherState.ATTENTION_REQUIRED,
                Recommendation.CHECK_DEVICES,
                "dependency unavailable at startup",
            )
            await self._async_notify_dependency_problem()
            return

        await self._async_recover_baseline()

    async def async_shutdown(self) -> bool:
        """Cancel work and prevent integration-owned playback from being orphaned."""
        async with self._lock:
            stopped = await self._async_stop_playback()
            if not stopped or self._failed_play_context_ids:
                return False
            self._started = False
            self._episode += 1
            self._cancel_all_timers()
            for unsubscribe in self._unsubscribers:
                unsubscribe()
            self._unsubscribers.clear()
            return stopped

    async def async_set_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the response loop."""
        if enabled:
            await self._async_enable()
        else:
            await self.async_stop()

    async def _async_enable(self) -> None:
        """Enable baseline playback and start a fresh response episode."""
        async with self._lock:
            self._ensure_started()
            if self.enabled:
                return
            if self._failed_play_context_ids:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="playback_settling",
                )
            if not self.configured:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_configured",
                )

            self.settings.enabled = True
            self._persist_settings()
            self._new_episode()
            self._dependency_issues = self._find_dependency_issues()
            if self._dependency_issues:
                self._transition(
                    SootherState.ATTENTION_REQUIRED,
                    Recommendation.CHECK_DEVICES,
                    "enabled with unavailable dependency",
                )
                await self._async_notify_dependency_problem()
                return

            await self._async_recover_baseline()

    async def async_stop(self, *, expected_episode: int | None = None) -> None:
        """Disable the response loop and stop integration-owned playback."""
        async with self._lock:
            if not self._started:
                return
            if expected_episode is not None and expected_episode != self._episode:
                return
            self.settings.enabled = False
            self._persist_settings()
            self._new_episode()
            self._cancel_all_timers()
            self._transition(
                SootherState.DISABLED, Recommendation.ENABLE, "stopped by parent"
            )
            stopped = await self._async_stop_playback()
            await self._async_clear_notifications()
            if not stopped:
                self._transition(
                    SootherState.ATTENTION_REQUIRED,
                    Recommendation.CHECK_DEVICES,
                    "speaker stop failed",
                )
                await self._async_notify_dependency_problem()

    async def async_boost(self, *, expected_episode: int | None = None) -> bool:
        """Apply the one parent-authorized temporary boost, subject to safety."""
        async with self._lock:
            if expected_episode is not None and expected_episode != self._episode:
                return False
            self._validate_controllable()
            if self._boosted:
                return False

            now = dt_util.utcnow()
            if (
                self._last_boost_at is not None
                and (now - self._last_boost_at).total_seconds()
                < self.settings.cooldown_seconds
            ):
                self._transition(
                    self.state, Recommendation.COOLDOWN, "boost rejected by cooldown"
                )
                return False

            self._new_episode()
            self._incident_active = True
            if not await self._async_set_speaker_volume(self.settings.boost_volume):
                self._boosted = False
                self._incident_active = False
                return False
            self._boosted = True
            self._last_boost_at = now

            self._transition(
                SootherState.BOOST,
                Recommendation.OBSERVE,
                "boost authorized by parent",
            )
            await self._async_clear_notifications()
            if self._cry_is_on():
                self._schedule_escalation()
            else:
                self._schedule_settling()
            return True

    async def async_baseline(self, *, expected_episode: int | None = None) -> None:
        """Return to baseline and mark an active episode parent-owned."""
        async with self._lock:
            if expected_episode is not None and expected_episode != self._episode:
                return
            self._validate_controllable()
            self._new_episode()
            self._boosted = False
            self._acknowledged = self._cry_is_on()
            if not await self._async_set_speaker_volume(self.settings.baseline_volume):
                return
            if self._cry_is_on():
                self._transition(
                    SootherState.CRY_PENDING,
                    Recommendation.ACKNOWLEDGED,
                    "baseline selected by parent",
                )
            else:
                self._transition(
                    SootherState.BASELINE,
                    Recommendation.NONE,
                    "baseline selected by parent",
                )
            await self._async_clear_notifications()

    async def async_acknowledge(self, *, expected_episode: int | None = None) -> None:
        """Mark the current episode as handled by either parent."""
        async with self._lock:
            if not self._started:
                return
            if expected_episode is not None and expected_episode != self._episode:
                return
            if not self.enabled:
                return
            was_settling = self._cancel_settling is not None
            self._episode += 1
            self._acknowledged = True
            self._cancel_timer("escalation")
            if was_settling:
                self._schedule_settling()
            self._transition(
                self.state,
                Recommendation.ACKNOWLEDGED,
                "acknowledged by parent",
            )
            await self._async_clear_notifications()

    async def async_set_volume(self, key: str, value: float) -> None:
        """Persist one volume setting after validating all relationships."""
        async with self._lock:
            self._ensure_started()
            proposed = {
                CONF_BASELINE_VOLUME: self.settings.baseline_volume,
                CONF_BOOST_VOLUME: self.settings.boost_volume,
                CONF_MAX_VOLUME: self.settings.max_volume,
            }
            if key not in proposed:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="invalid_volume"
                )
            proposed[key] = float(value)
            if not (
                0.0
                <= proposed[CONF_BASELINE_VOLUME]
                <= proposed[CONF_BOOST_VOLUME]
                <= proposed[CONF_MAX_VOLUME]
                <= MAX_VOLUME_PERCENT
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="invalid_volume"
                )

            self.settings.baseline_volume = proposed[CONF_BASELINE_VOLUME]
            self.settings.boost_volume = proposed[CONF_BOOST_VOLUME]
            self.settings.max_volume = proposed[CONF_MAX_VOLUME]
            self._persist_settings()

            if self.enabled and self.dependencies_available and self._owns_playback:
                target = (
                    self.settings.boost_volume
                    if self._boosted
                    else self.settings.baseline_volume
                )
                await self._async_set_speaker_volume(target)
            self._emit_update()

    async def _async_recover_baseline(self) -> None:
        """Recover safely without replaying a pre-restart boost."""
        self._cancel_all_timers()
        self._boosted = False
        self._acknowledged = False
        self._incident_active = False
        self._transition(
            SootherState.BASELINE, Recommendation.NONE, "fresh baseline recovery"
        )
        if not await self._async_ensure_playback():
            return
        if self._cry_is_on():
            self._begin_cry_pending(new_episode=True)

    async def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle cry, dependency, and continuous-playback state changes."""
        async with self._lock:
            if not self._started:
                return
            entity_id = event.data["entity_id"]
            if (
                entity_id == self.media_player
                and await self._async_compensate_failed_play(event)
            ):
                return
            old_issues = self._dependency_issues
            new_issues = self._find_dependency_issues()
            self._dependency_issues = new_issues

            if await self._async_handle_dependency_change(old_issues, new_issues):
                return

            if not self.enabled:
                return
            if self._playback_interrupted:
                return

            if entity_id == self.cry_sensor:
                self._handle_cry_state_changed(event)
            elif entity_id == self.media_player:
                await self._async_handle_media_state_changed(event)

    def _handle_cry_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle only concrete cry state edges, not attribute updates."""
        old_state = event.data.get("old_state")
        old_cry_is_on = old_state is not None and old_state.state == STATE_ON
        cry_is_on = self._cry_is_on()
        if old_cry_is_on == cry_is_on:
            return
        if cry_is_on:
            self._async_cry_started()
        else:
            self._async_cry_stopped()

    async def _async_handle_media_state_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Guard owned playback and fail safe on an external takeover."""
        if self._media_was_replaced(event):
            await self._async_finalize_playback_interruption()
            return
        current_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        if (
            self._owns_playback
            and current_state is not None
            and current_state.state
            in {
                MediaPlayerState.IDLE,
                MediaPlayerState.OFF,
                MediaPlayerState.PAUSED,
            }
        ):
            await self._async_ensure_playback()

    async def _async_service_changed(self, event: Event[dict[str, Any]]) -> None:
        """Reconcile health when a configured mobile notification action changes."""
        if event.data.get(ATTR_DOMAIN) != "notify":
            return
        service = event.data.get(ATTR_SERVICE)
        if (
            not isinstance(service, str)
            or f"notify.{service}" not in self.notify_targets
        ):
            return
        async with self._lock:
            if not self._started:
                return
            old_issues = self._dependency_issues
            new_issues = self._find_dependency_issues()
            self._dependency_issues = new_issues
            await self._async_handle_dependency_change(old_issues, new_issues)

    async def _async_handle_dependency_change(
        self, old_issues: set[str], new_issues: set[str]
    ) -> bool:
        """Handle dependency loss/recovery and return whether it consumed the event."""
        if new_issues:
            self._cancel_all_timers()
            if self.enabled:
                if new_issues != old_issues:
                    self._episode += 1
                    self._acknowledged = False
                if self._boosted and self._media_player_available():
                    self._boosted = False
                    if not await self._async_set_speaker_volume(
                        self.settings.baseline_volume
                    ):
                        return True
                self._transition(
                    SootherState.ATTENTION_REQUIRED,
                    Recommendation.CHECK_DEVICES,
                    "dependency became unavailable",
                )
                if new_issues != old_issues:
                    await self._async_notify_dependency_problem()
            return True

        if old_issues and self.enabled:
            if self._owns_playback:
                await self._async_playback_is_owned_now()
            if self._playback_interrupted:
                self._transition(
                    SootherState.ATTENTION_REQUIRED,
                    Recommendation.CHECK_DEVICES,
                    "dependency recovered while speaker remained externally owned",
                )
                return True
            await self._async_notify_recovery()
            self._new_episode()
            await self._async_recover_baseline()
            return True
        return False

    def _async_cry_started(self) -> None:
        """Start or resume a debounced episode."""
        settling_boost = (
            self.state is SootherState.BOOST and self._cancel_settling is not None
        )
        if self.state in {SootherState.BASELINE, SootherState.SETTLING} or (
            settling_boost
        ):
            self._cancel_timer("settling")
            self._begin_cry_pending(new_episode=self.state is SootherState.BASELINE)

    def _async_cry_stopped(self) -> None:
        """Cancel a false alarm or start uninterrupted-quiet settling."""
        if self._cancel_debounce is not None and not self._incident_active:
            self._cancel_timer("debounce")
            self._transition(
                SootherState.BASELINE,
                Recommendation.NONE,
                "cry ended before debounce",
            )
            return

        if self.state in {
            SootherState.CRY_PENDING,
            SootherState.BOOST,
            SootherState.ATTENTION_REQUIRED,
        }:
            self._cancel_timer("debounce")
            self._cancel_timer("escalation")
            self._transition(
                SootherState.SETTLING,
                Recommendation.SETTLING,
                "quiet period started",
            )
            self._schedule_settling()

    def _begin_cry_pending(self, *, new_episode: bool) -> None:
        """Enter cry pending and start a fresh debounce timer."""
        if new_episode:
            self._new_episode()
            self._acknowledged = False
            self._incident_active = False
        self._transition(
            SootherState.CRY_PENDING, Recommendation.WAIT, "cry debounce started"
        )
        self._schedule_debounce()

    def _schedule_debounce(self) -> None:
        """Schedule cry debounce with an episode generation guard."""
        self._cancel_timer("debounce")
        episode = self._episode
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            del now
            async with self._lock:
                if self._cancel_debounce is not cancel_callback:
                    return
                self._cancel_debounce = None
                if (
                    not self._started
                    or episode != self._episode
                    or not self.enabled
                    or not self.dependencies_available
                    or not self._cry_is_on()
                ):
                    return
                self._incident_active = True
                recommendation = (
                    Recommendation.ACKNOWLEDGED
                    if self._acknowledged
                    else Recommendation.OBSERVE
                    if self._boosted
                    else Recommendation.BOOST
                )
                state = (
                    SootherState.BOOST if self._boosted else SootherState.CRY_PENDING
                )
                self._transition(state, recommendation, "cry debounce completed")
                if not self._acknowledged:
                    if await self._async_notify_cry():
                        self._schedule_escalation()
                    else:
                        self._acknowledged = False
                        self._transition(
                            SootherState.ATTENTION_REQUIRED,
                            Recommendation.CHECK_DEVICES,
                            "all notification deliveries failed",
                        )

        cancel_callback = async_call_later(
            self.hass, self.settings.debounce_seconds, _expired
        )
        self._cancel_debounce = cancel_callback

    def _schedule_escalation(self) -> None:
        """Schedule the finite persistent-cry attention deadline."""
        self._cancel_timer("escalation")
        episode = self._episode
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            del now
            async with self._lock:
                if self._cancel_escalation is not cancel_callback:
                    return
                self._cancel_escalation = None
                if (
                    not self._started
                    or episode != self._episode
                    or not self.enabled
                    or not self.dependencies_available
                    or not self._cry_is_on()
                    or self._acknowledged
                ):
                    return
                self._episode += 1
                self._transition(
                    SootherState.ATTENTION_REQUIRED,
                    Recommendation.ATTEND,
                    "persistent cry attention deadline",
                )
                await self._async_notify_attention()

        cancel_callback = async_call_later(
            self.hass, self.settings.escalation_seconds, _expired
        )
        self._cancel_escalation = cancel_callback

    def _schedule_settling(self) -> None:
        """Schedule an automatic baseline after uninterrupted quiet."""
        self._cancel_timer("settling")
        episode = self._episode
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            del now
            async with self._lock:
                if self._cancel_settling is not cancel_callback:
                    return
                self._cancel_settling = None
                if (
                    not self._started
                    or episode != self._episode
                    or not self.enabled
                    or not self.dependencies_available
                    or not self._owns_playback
                    or self._cry_is_on()
                ):
                    return
                self._boosted = False
                if await self._async_playback_is_owned_now():
                    if not await self._async_set_speaker_volume(
                        self.settings.baseline_volume
                    ):
                        return
                elif self._owns_playback and not self._playback_interrupted:
                    if not await self._async_ensure_playback():
                        return
                else:
                    return
                self._acknowledged = False
                self._incident_active = False
                self._transition(
                    SootherState.BASELINE,
                    Recommendation.NONE,
                    "quiet settling completed",
                )
                await self._async_clear_notifications()
                self._episode += 1

        cancel_callback = async_call_later(
            self.hass, self.settings.settling_seconds, _expired
        )
        self._cancel_settling = cancel_callback

    async def _async_notification_action(self, event: Event[dict[str, Any]]) -> None:
        """Accept only current actions belonging to this entry and episode."""
        if not self._started:
            return
        parsed_action = self._parse_notification_action(event.data.get("action"))
        if parsed_action is None:
            return
        episode, command = parsed_action

        if command == ACTION_BOOST:
            await self.async_boost(expected_episode=episode)
        elif command == ACTION_BASELINE:
            await self.async_baseline(expected_episode=episode)
        elif command == ACTION_ACKNOWLEDGE:
            await self.async_acknowledge(expected_episode=episode)
        elif command == ACTION_STOP:
            await self.async_stop(expected_episode=episode)

    def _parse_notification_action(self, action: object) -> tuple[int, str] | None:
        """Authenticate and parse an action from this runtime session."""
        if not isinstance(action, str):
            return None
        prefix = (
            f"{NOTIFICATION_ACTION_PREFIX}:{self.entry.entry_id}:{self._session_id}:"
        )
        if not action.startswith(prefix):
            return None
        parts = action.split(":", 4)
        action_id_parts = 5
        if len(parts) != action_id_parts:
            return None
        try:
            episode = int(parts[3])
        except ValueError:
            return None
        if episode != self._episode:
            return None
        return episode, parts[4]

    def _notification_action(self, command: str) -> str:
        """Build an action identifier unique to the current episode."""
        return (
            f"{NOTIFICATION_ACTION_PREFIX}:{self.entry.entry_id}:"
            f"{self._session_id}:{self._episode}:{command}"
        )

    async def _async_notify_cry(self) -> bool:
        """Suggest the single parent-authorized boost to every parent."""
        if self._boosted:
            return await self._async_notify(
                (
                    "Crying continues while the boost is active. "
                    "Keep observing the nursery."
                ),
                [
                    self._action(ACTION_BASELINE, "Baseline"),
                    self._action(ACTION_ACKNOWLEDGE, "Acknowledge"),
                    self._action(ACTION_STOP, "Stop"),
                ],
                include_camera=True,
            )
        return await self._async_notify(
            (
                "Crying detected after the debounce period. "
                "Consider a small white-noise boost."
            ),
            [
                self._action(ACTION_BOOST, "Boost"),
                self._action(ACTION_BASELINE, "Baseline"),
                self._action(ACTION_ACKNOWLEDGE, "Acknowledge"),
            ],
            include_camera=True,
        )

    async def _async_notify_attention(self) -> None:
        """Ask a parent to attend after the finite response window."""
        await self._async_notify(
            "Crying is continuing. Please check the nursery.",
            [
                self._action(ACTION_ACKNOWLEDGE, "Acknowledge"),
                self._action(ACTION_BASELINE, "Baseline"),
                self._action(ACTION_STOP, "Stop"),
            ],
            include_camera=True,
        )

    async def _async_notify_dependency_problem(self) -> None:
        """Notify parents once when a selected dependency becomes unavailable."""
        await self._async_notify(
            (
                "A nursery camera, cry sensor, speaker, or parent notification "
                "action is unavailable. Automatic response is paused."
            ),
            [
                self._action(ACTION_ACKNOWLEDGE, "Acknowledge"),
                self._action(ACTION_STOP, "Stop"),
            ],
            include_camera=False,
        )

    async def _async_notify_recovery(self) -> None:
        """Replace the dependency alert after recovery."""
        await self._async_notify(
            "Nursery devices are available again. Restarting safely at baseline.",
            [],
            include_camera=False,
        )

    async def _async_notify_playback_replaced(self) -> None:
        """Tell parents that external speaker use paused the response loop."""
        await self._async_notify(
            (
                "The nursery speaker started playing different media. Nursery "
                "Soother is paused; stop it and enable it again to resume."
            ),
            [
                self._action(ACTION_ACKNOWLEDGE, "Acknowledge"),
                self._action(ACTION_STOP, "Stop"),
            ],
            include_camera=False,
        )

    def _action(self, command: str, title: str) -> dict[str, str]:
        """Build one actionable-notification button."""
        return {"action": self._notification_action(command), "title": title}

    async def _async_notify(
        self,
        message: str,
        actions: list[dict[str, str]],
        *,
        include_camera: bool,
    ) -> bool:
        """Send one shared tagged notification, continuing past target failures."""
        if not self.configured:
            return False
        camera = self.camera
        if camera is None:
            return False
        notification_data: dict[str, Any] = {
            "tag": self.notification_tag,
            "group": NOTIFICATION_TAG_PREFIX,
            "url": f"entityId:{camera}",
            "clickAction": f"entityId:{camera}",
            "actions": actions,
        }
        if include_camera and self._entity_available(camera):
            notification_data[ATTR_ENTITY_ID] = camera
            notification_data["image"] = f"/api/camera_proxy/{camera}"

        payload = {
            "title": "Nursery Soother",
            "message": message,
            "data": notification_data,
        }
        results = await asyncio.gather(
            *(
                self._async_call_notify(target, payload)
                for target in self.notify_targets
            )
        )
        return any(results)

    async def _async_clear_notifications(self) -> None:
        """Clear the shared incident on both parents' devices."""
        if not self.configured:
            return
        payload = {
            "message": "clear_notification",
            "data": {"tag": self.notification_tag},
        }
        await asyncio.gather(
            *(
                self._async_call_notify(target, payload)
                for target in self.notify_targets
            )
        )

    async def _async_call_notify(self, target: str, payload: dict[str, Any]) -> bool:
        """Call one configured notify action without blocking the other parent."""
        domain, separator, service = target.partition(".")
        if separator != "." or domain != "notify" or not service:
            self._last_error = "invalid_notification_target"
            return False
        try:
            async with asyncio.timeout(SERVICE_CALL_TIMEOUT):
                await self.hass.services.async_call(
                    domain, service, payload, blocking=True
                )
        # Third-party service handlers are an isolation boundary and may raise
        # exceptions outside Home Assistant's documented hierarchy.
        except Exception as err:  # noqa: BLE001
            self._last_error = type(err).__name__
            _LOGGER.warning(
                "Nursery notification delivery failed (%s)", type(err).__name__
            )
            return False
        return True

    async def _async_ensure_playback(self) -> bool:
        """Set a capped current volume and start the configured media."""
        media_player = self.media_player
        if media_player is None or not self._entity_available(media_player):
            return False
        target = (
            self.settings.boost_volume
            if self._boosted
            else self.settings.baseline_volume
        )
        if not await self._async_set_speaker_volume(target, require_owned=False):
            self._playback_interrupted = True
            return False

        media = self.white_noise
        if media is None:
            return False
        play_context = Context()
        self._playback_interrupted = False
        self._pending_play_context_id = play_context.id
        self._owns_playback = False
        self._owned_media_content_id = None
        self._awaiting_playback_confirmation = True
        if not await self._async_call_media(
            SERVICE_PLAY_MEDIA,
            {
                ATTR_MEDIA_CONTENT_ID: media[ATTR_MEDIA_CONTENT_ID],
                ATTR_MEDIA_CONTENT_TYPE: media[ATTR_MEDIA_CONTENT_TYPE],
            },
            context=play_context,
        ):
            self._track_failed_play_context(play_context.id)
            self._awaiting_playback_confirmation = False
            self._pending_play_context_id = None
            media_state = self.hass.states.get(media_player)
            if (
                media_state is not None
                and media_state.context.id == play_context.id
                and media_state.state
                in {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
            ):
                self._clear_failed_play_context(play_context.id)
                self._owns_playback = True
                self._owned_play_context_id = play_context.id
                current_content_id = media_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
                if isinstance(current_content_id, str):
                    self._owned_media_content_id = current_content_id
                await self._async_stop_playback()
            self._playback_interrupted = True
            return False
        self._owns_playback = True
        self._owned_play_context_id = play_context.id
        media_state = self.hass.states.get(media_player)
        current_content_id = (
            media_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
            if media_state is not None
            else None
        )
        if (
            isinstance(current_content_id, str)
            and media_state is not None
            and (
                media_state.context.id == play_context.id
                or current_content_id == media[ATTR_MEDIA_CONTENT_ID]
            )
        ):
            self._owned_media_content_id = current_content_id
            self._awaiting_playback_confirmation = False
            self._pending_play_context_id = None
        return True

    async def _async_set_speaker_volume(
        self,
        requested_percent: float,
        *,
        require_owned: bool = True,
    ) -> bool:
        """Issue a volume command that can never exceed the hard cap."""
        if require_owned and not await self._async_playback_is_owned_now():
            return False
        safe_percent = max(
            0.0,
            min(
                float(requested_percent),
                self.settings.max_volume,
                MAX_VOLUME_PERCENT,
            ),
        )
        return await self._async_call_media(
            SERVICE_VOLUME_SET,
            {ATTR_MEDIA_VOLUME_LEVEL: safe_percent / 100.0},
        )

    async def _async_playback_is_owned_now(self) -> bool:
        """Reconcile live speaker state immediately before a volume effect."""
        if not self._owns_playback or self.media_player is None:
            return False
        media_state = self.hass.states.get(self.media_player)
        if media_state is None or media_state.state not in {
            MediaPlayerState.PLAYING,
            MediaPlayerState.BUFFERING,
        }:
            return False
        current_content_id = media_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
        if self._awaiting_playback_confirmation:
            if media_state.context.id != self._pending_play_context_id:
                return False
            if isinstance(current_content_id, str):
                self._owned_media_content_id = current_content_id
            self._awaiting_playback_confirmation = False
            self._pending_play_context_id = None
            return True

        if self._owned_media_content_id is not None:
            still_owned = current_content_id == self._owned_media_content_id
        else:
            still_owned = current_content_id is None and (
                media_state.context.id == self._owned_play_context_id
                or media_state.context.id in self._media_context_ids
            )
        if still_owned:
            return True

        self._relinquish_playback()
        await self._async_finalize_playback_interruption()
        return False

    async def _async_compensate_failed_play(
        self, event: Event[EventStateChangedData]
    ) -> bool:
        """Stop only a late playback event proven to belong to a failed call."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return False
        context_matches = new_state.context.id in self._failed_play_context_ids
        if not context_matches:
            return False
        live_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        if new_state is not live_state:
            return True
        if new_state.state not in {
            MediaPlayerState.PLAYING,
            MediaPlayerState.BUFFERING,
        }:
            return True

        self._clear_failed_play_context(new_state.context.id)
        self._owns_playback = True
        self._owned_play_context_id = new_state.context.id
        current_content_id = new_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
        self._owned_media_content_id = (
            current_content_id if isinstance(current_content_id, str) else None
        )
        if await self._async_stop_playback():
            return True
        self._transition(
            SootherState.ATTENTION_REQUIRED,
            Recommendation.CHECK_DEVICES,
            "late failed playback could not be stopped",
        )
        await self._async_notify_dependency_problem()
        return True

    def _track_failed_play_context(self, context_id: str) -> None:
        """Monitor an ambiguous failed play for a bounded compensation window."""
        if context_id in self._failed_play_context_ids:
            return
        self._failed_play_context_ids.add(context_id)
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            del now
            async with self._lock:
                if self._failed_play_expiries.get(context_id) is not cancel_callback:
                    return
                self._failed_play_expiries.pop(context_id, None)
                self._failed_play_context_ids.discard(context_id)
                self._emit_update()

        cancel_callback = async_call_later(
            self.hass, FAILED_PLAY_COMPENSATION_SECONDS, _expired
        )
        self._failed_play_expiries[context_id] = cancel_callback

    def _clear_failed_play_context(self, context_id: str) -> None:
        """Finish monitoring one failed play context."""
        self._failed_play_context_ids.discard(context_id)
        if cancel_callback := self._failed_play_expiries.pop(context_id, None):
            cancel_callback()

    async def _async_finalize_playback_interruption(self) -> None:
        """Expose a detected external takeover without touching its audio."""
        self._cancel_all_timers()
        self._episode += 1
        self._boosted = False
        self._acknowledged = False
        self._incident_active = False
        self._transition(
            SootherState.ATTENTION_REQUIRED,
            Recommendation.CHECK_DEVICES,
            "speaker playback replaced externally",
        )
        await self._async_notify_playback_replaced()

    async def _async_stop_playback(self) -> bool:
        """Stop or pause only playback started by this controller."""
        if not self._owns_playback:
            return True
        media_player = self.media_player
        if media_player is None or not self._entity_available(media_player):
            return False
        media_state = self.hass.states.get(media_player)
        if media_state is None:
            return False
        if not self._current_playback_should_be_stopped(media_state):
            return True
        features = self._media_features()
        stopped = False
        if features & MediaPlayerEntityFeature.STOP:
            stopped = await self._async_call_media(
                SERVICE_MEDIA_STOP, {}, critical=False
            )
        if not stopped and features & MediaPlayerEntityFeature.PAUSE:
            stopped = await self._async_call_media(
                SERVICE_MEDIA_PAUSE, {}, critical=False
            )
        if stopped:
            self._clear_playback_ownership()
        return stopped

    def _current_playback_should_be_stopped(self, media_state: State) -> bool:
        """Reconcile live ownership and return whether Stop is safe."""
        if media_state.state in {
            MediaPlayerState.IDLE,
            MediaPlayerState.OFF,
            MediaPlayerState.PAUSED,
        }:
            self._clear_playback_ownership()
            return False
        current_content_id = media_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
        if self._owned_media_content_id is not None:
            should_stop = current_content_id == self._owned_media_content_id
            if not should_stop:
                self._relinquish_playback()
            return should_stop
        if self._awaiting_playback_confirmation:
            should_stop = media_state.context.id == self._pending_play_context_id
            if should_stop:
                self._awaiting_playback_confirmation = False
                self._pending_play_context_id = None
            else:
                if self._pending_play_context_id is not None:
                    self._track_failed_play_context(self._pending_play_context_id)
                self._clear_playback_ownership()
            return should_stop
        should_stop = (
            media_state.context.id == self._owned_play_context_id
            or media_state.context.id in self._media_context_ids
        )
        if not should_stop:
            self._relinquish_playback()
        return should_stop

    def _clear_playback_ownership(self) -> None:
        """Clear ownership without classifying an external takeover."""
        self._owns_playback = False
        self._owned_media_content_id = None
        self._owned_play_context_id = None
        self._awaiting_playback_confirmation = False
        self._pending_play_context_id = None

    def _media_was_replaced(self, event: Event[EventStateChangedData]) -> bool:
        """Relinquish ownership when a definite different media item replaces ours."""
        if not self._owns_playback:
            return False
        new_state = event.data.get("new_state")
        current_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        if new_state is None or new_state is not current_state:
            return False
        if self._owned_media_content_id is None:
            return self._unidentified_media_was_replaced(event)
        new_content_id = new_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
        replaced = (
            new_content_id != self._owned_media_content_id
            and new_state.state
            in {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
            and new_state.context.id != self._owned_play_context_id
            and new_state.context.id not in self._media_context_ids
        )
        if replaced:
            self._relinquish_playback()
        return replaced

    def _unidentified_media_was_replaced(
        self, event: Event[EventStateChangedData]
    ) -> bool:
        """Handle players that do not expose a usable media content ID."""
        if self._awaiting_playback_confirmation:
            return self._handle_pending_playback_confirmation(event)
        new_state = event.data.get("new_state")
        replaced = (
            new_state is not None
            and new_state.state
            in {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
            and new_state.context.id != self._owned_play_context_id
            and new_state.context.id not in self._media_context_ids
        )
        if replaced:
            self._relinquish_playback()
        return replaced

    def _handle_pending_playback_confirmation(
        self, event: Event[EventStateChangedData]
    ) -> bool:
        """Confirm our play context or reject a definite external replacement."""
        if not self._awaiting_playback_confirmation:
            return False
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state not in {
            MediaPlayerState.PLAYING,
            MediaPlayerState.BUFFERING,
        }:
            return False
        old_state = event.data.get("old_state")
        old_content_id = (
            old_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
            if old_state is not None
            else None
        )
        new_content_id = new_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
        if new_state.context.id == self._pending_play_context_id:
            if isinstance(new_content_id, str):
                self._owned_media_content_id = new_content_id
            self._awaiting_playback_confirmation = False
            self._pending_play_context_id = None
            return False
        if new_state.context.id in self._media_context_ids:
            return False
        if new_content_id != old_content_id or new_content_id is None:
            self._relinquish_playback()
            return True
        return False

    def _relinquish_playback(self) -> None:
        """Forget playback once another source has definitely taken over."""
        self._clear_playback_ownership()
        self._playback_interrupted = True

    async def _async_call_media(
        self,
        service: str,
        data: dict[str, Any],
        *,
        critical: bool = True,
        context: Context | None = None,
    ) -> bool:
        """Call the selected media player and convert failures to safe state."""
        if self.media_player is None:
            return False
        call_context = context or Context()
        self._media_context_ids.append(call_context.id)
        try:
            async with asyncio.timeout(SERVICE_CALL_TIMEOUT):
                await self.hass.services.async_call(
                    MEDIA_PLAYER_DOMAIN,
                    service,
                    {ATTR_ENTITY_ID: self.media_player, **data},
                    blocking=True,
                    context=call_context,
                )
        # Third-party media-player handlers are an isolation boundary and may
        # raise exceptions outside Home Assistant's documented hierarchy.
        except Exception as err:  # noqa: BLE001
            self._last_error = type(err).__name__
            _LOGGER.warning(
                "Nursery speaker %s action failed (%s)",
                service,
                type(err).__name__,
            )
            if not critical:
                return False
            self._cancel_all_timers()
            self._acknowledged = False
            self._transition(
                SootherState.ATTENTION_REQUIRED,
                Recommendation.CHECK_DEVICES,
                "speaker action failed",
            )
            await self._async_notify_dependency_problem()
            return False
        return True

    def _media_features(self) -> MediaPlayerEntityFeature:
        """Return supported features for the selected speaker."""
        if self.media_player is None:
            return MediaPlayerEntityFeature(0)
        state = self.hass.states.get(self.media_player)
        if state is None:
            return MediaPlayerEntityFeature(0)
        raw_features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        if not isinstance(raw_features, int):
            return MediaPlayerEntityFeature(0)
        return MediaPlayerEntityFeature(raw_features)

    def _find_dependency_issues(self) -> set[str]:
        """Return redacted dependency categories that are unavailable."""
        issues: set[str] = set()
        for key, entity_id in (
            (CONF_CRY_SENSOR, self.cry_sensor),
            (CONF_CAMERA, self.camera),
            (CONF_MEDIA_PLAYER, self.media_player),
        ):
            if entity_id is None or not self._entity_available(entity_id):
                issues.add(key)
        features = self._media_features()
        if CONF_MEDIA_PLAYER not in issues and (
            features & _REQUIRED_MEDIA_PLAYER_FEATURES
            != _REQUIRED_MEDIA_PLAYER_FEATURES
            or not features & _STOP_MEDIA_PLAYER_FEATURES
        ):
            issues.add(CONF_MEDIA_PLAYER)
        if any(
            not self._notify_target_available(target) for target in self.notify_targets
        ):
            issues.add(CONF_NOTIFY_TARGETS)
        return issues

    def _notify_target_available(self, target: str) -> bool:
        """Return whether a configured mobile notification action is registered."""
        domain, separator, service = target.partition(".")
        return (
            separator == "."
            and domain == "notify"
            and bool(service)
            and self.hass.services.has_service(domain, service)
        )

    def _entity_available(self, entity_id: str) -> bool:
        """Return whether an entity has a concrete usable state."""
        state = self.hass.states.get(entity_id)
        return state is not None and state.state not in {
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        }

    def _media_player_available(self) -> bool:
        """Return whether the configured media player is available."""
        return self.media_player is not None and self._entity_available(
            self.media_player
        )

    def _cry_is_on(self) -> bool:
        """Return whether the cry input is explicitly on."""
        if self.cry_sensor is None:
            return False
        state: State | None = self.hass.states.get(self.cry_sensor)
        return state is not None and state.state == STATE_ON

    def _string_data(self, key: str) -> str | None:
        """Snapshot one stable string config value for this runtime."""
        value = self.entry.data.get(key)
        return value if isinstance(value, str) else None

    def _validate_controllable(self) -> None:
        """Reject parent commands that cannot be applied safely."""
        self._ensure_started()
        if not self.enabled:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="disabled"
            )
        if not self.configured:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_configured"
            )
        self._dependency_issues = self._find_dependency_issues()
        if self._dependency_issues:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="devices_unavailable",
            )
        if not self._owns_playback:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="playback_replaced",
            )

    def _ensure_started(self) -> None:
        """Reject standard-entity commands after entry unload has begun."""
        if not self._started:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
            )

    def _new_episode(self) -> None:
        """Invalidate stale timers and phone actions."""
        self._episode += 1
        self._cancel_all_timers()
        self._acknowledged = False
        self._incident_active = False

    def _cancel_all_timers(self) -> None:
        """Cancel all response timers."""
        self._cancel_timer("debounce")
        self._cancel_timer("escalation")
        self._cancel_timer("settling")

    def _cancel_timer(self, timer: str) -> None:
        """Cancel one named response timer."""
        attribute = f"_cancel_{timer}"
        cancel: CALLBACK_TYPE | None = getattr(self, attribute)
        if cancel is not None:
            cancel()
            setattr(self, attribute, None)

    def _persist_settings(self) -> None:
        """Persist mutable entity settings without forcing a reload."""
        self.hass.config_entries.async_update_entry(
            self.entry, options=self.settings.as_options()
        )

    def _transition(
        self,
        state: SootherState,
        recommendation: Recommendation,
        reason: str,
    ) -> None:
        """Apply one state transition and notify every entity."""
        self.state = state
        self.recommendation = recommendation
        self._last_reason = reason
        self._last_transition_at = dt_util.utcnow()
        _LOGGER.debug(
            "Nursery transition: state=%s recommendation=%s reason=%s",
            state,
            recommendation,
            reason,
        )
        self._emit_update()

    @callback
    def _emit_update(self) -> None:
        """Push current memory state to every integration entity."""
        for listener in tuple(self._listeners):
            listener()
