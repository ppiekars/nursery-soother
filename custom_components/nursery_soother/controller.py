"""Event-driven response controller for Nursery Soother."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeGuard
from urllib.parse import parse_qsl, unquote, urlsplit

from homeassistant.components.homeassistant.const import DOMAIN as HOMEASSISTANT_DOMAIN
from homeassistant.components.media_player import MediaPlayerEnqueue, RepeatMode
from homeassistant.components.media_player.const import (
    ATTR_GROUP_MEMBERS,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_REPEAT,
    ATTR_MEDIA_VOLUME_LEVEL,
    SERVICE_CLEAR_PLAYLIST,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
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
    SERVICE_REPEAT_SET,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_SET,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.network import is_hass_url
from homeassistant.helpers.trigger import (
    async_initialize_triggers,
    async_validate_trigger_config,
)
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_SET_LEVEL,
    ACTION_SET_MANUAL,
    CONF_BASELINE_VOLUME,
    CONF_CAMERA,
    CONF_CRY_SENSOR,
    CONF_DECREASE_LEVEL_TRIGGERS,
    CONF_INCREASE_LEVEL_TRIGGERS,
    CONF_LEVEL_1_VOLUME,
    CONF_LEVEL_2_VOLUME,
    CONF_LEVEL_3_VOLUME,
    CONF_LEVEL_4_VOLUME,
    CONF_MAX_VOLUME,
    CONF_MEDIA_PLAYER,
    CONF_NOTIFY_TARGETS,
    CONF_SOUNDS,
    CONF_TOGGLE_TRIGGERS,
    DOMAIN,
    EVENT_NOTIFICATION_ACTION,
    MAX_VOLUME_PERCENT,
    NOTIFICATION_ACTION_PREFIX,
    NOTIFICATION_TAG_PREFIX,
)
from .evidence import CryEvidence, EvidenceSnapshot
from .models import (
    ACTIVE_LEVELS,
    LEVEL_VOLUME_KEYS,
    Recommendation,
    SootherSettings,
    SootherState,
    SoothingLevel,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, State

_LOGGER = logging.getLogger(__name__)
SERVICE_CALL_TIMEOUT = 10
FAILED_PLAY_COMPENSATION_SECONDS = 15
NATIVE_LOOP_CONFIRMATION_SECONDS = 5.0
NATIVE_LOOP_CONFIRMATION_POLL_SECONDS = 0.1
SERVICE_UPDATE_ENTITY = "update_entity"
AUTH_SIGNATURE_QUERY_PARAMETER = "authSig"
LOCAL_MEDIA_URL_PREFIX = "/media/"
INITIAL_CRY_EVENT_THRESHOLD = 2
INITIAL_CRY_ACTIVE_SECONDS_THRESHOLD = 8.0
ESCALATION_CRY_EVENT_THRESHOLD = 1
ESCALATION_CRY_ACTIVE_SECONDS_THRESHOLD = 6.0

type ControllerListener = Callable[[], None]

_REQUIRED_MEDIA_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET
)
_STOP_MEDIA_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.STOP | MediaPlayerEntityFeature.PAUSE
)
_NATIVE_LOOP_MEDIA_PLAYER_FEATURES = (
    MediaPlayerEntityFeature.CLEAR_PLAYLIST
    | MediaPlayerEntityFeature.MEDIA_ENQUEUE
    | MediaPlayerEntityFeature.REPEAT_SET
)
SONOS_PLATFORM = "sonos"
SONOS_CROSSFADE_UNIQUE_ID_SUFFIX = "-cross_fade"


@dataclass(frozen=True, slots=True)
class _NativeLoopSettings:
    """Speaker settings captured for one integration-owned loop session."""

    crossfade_entity_id: str
    previous_repeat: str
    previous_crossfade: str
    group_members: tuple[str, ...]


class NurserySootherController:
    """Coordinate nursery inputs, timers, speaker effects, and parents."""

    def __init__(  # noqa: PLR0915
        self, hass: HomeAssistant, entry: ConfigEntry[Any]
    ) -> None:
        """Initialize a controller from one config entry."""
        self.hass = hass
        self.entry = entry
        self.settings = SootherSettings.from_options(dict(entry.options))
        self.cry_sensor = self._string_data(CONF_CRY_SENSOR)
        self.camera = self._string_data(CONF_CAMERA)
        self.media_player = self._string_data(CONF_MEDIA_PLAYER)
        configured_sounds = entry.data.get(CONF_SOUNDS)
        self.sounds: dict[SoothingLevel, dict[str, Any]] = {}
        if isinstance(configured_sounds, dict):
            for level in ACTIVE_LEVELS:
                configured_media = configured_sounds.get(level.value)
                if isinstance(configured_media, dict):
                    self.sounds[level] = dict(configured_media)
        configured_targets = entry.data.get(CONF_NOTIFY_TARGETS)
        self.notify_targets = (
            tuple(target for target in configured_targets if isinstance(target, str))
            if isinstance(configured_targets, list)
            else ()
        )
        self.toggle_triggers = self._trigger_configs(CONF_TOGGLE_TRIGGERS)
        self.increase_level_triggers = self._trigger_configs(
            CONF_INCREASE_LEVEL_TRIGGERS
        )
        self.decrease_level_triggers = self._trigger_configs(
            CONF_DECREASE_LEVEL_TRIGGERS
        )

        self.state = SootherState.STANDBY
        self.recommendation = Recommendation.START
        self._episode = 0
        self._action_generation = 0
        self._session_id = secrets.token_hex(8)
        self._incident_active = False
        self._episode_confirmed = False
        self._provisional_level_1 = False
        self._initial_level_1_applied = False
        self._settling_deferred_by_lock = False
        self._episode_started_at: datetime | None = None
        self._confirmed_at: datetime | None = None
        self._last_cry_activity_at: datetime | None = None
        self._last_level_change_at: datetime | None = None
        self._stage_simulated_events = 0
        self._evidence = CryEvidence(self.settings.evidence_window_seconds)
        self._last_error: str | None = None
        self._last_reason = "initialized"
        self._last_transition_at = dt_util.utcnow()

        self._listeners: set[ControllerListener] = set()
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._cancel_evidence: CALLBACK_TYPE | None = None
        self._cancel_provisional: CALLBACK_TYPE | None = None
        self._cancel_cry_gap: CALLBACK_TYPE | None = None
        self._cancel_settling: CALLBACK_TYPE | None = None
        self._cancel_attention: CALLBACK_TYPE | None = None
        self._dependency_issues: set[str] = set()
        self._owns_playback = False
        self._playback_interrupted = False
        self._owned_media_content_id: str | None = None
        self._owned_play_context_id: str | None = None
        self._awaiting_playback_confirmation = False
        self._pending_play_context_id: str | None = None
        self._failed_play_context_ids: set[str] = set()
        self._failed_play_media_content_ids: dict[str, str] = {}
        self._failed_play_expiries: dict[str, CALLBACK_TYPE] = {}
        self._failed_native_loop_context_ids: set[str] = set()
        self._native_loop_settings: _NativeLoopSettings | None = None
        self._native_loop_active = False
        self._native_loop_force_restore = False
        self._toggle_triggers_attached = False
        self._increase_level_triggers_attached = False
        self._decrease_level_triggers_attached = False
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """Return whether an active soothing level is selected."""
        return self.level is not SoothingLevel.STANDBY

    @property
    def level(self) -> SoothingLevel:
        """Return the selected soothing level."""
        return self.settings.level

    @property
    def automatic(self) -> bool:
        """Return whether confirmed crying may increase the level."""
        return self.settings.automatic_operation

    @property
    def locked(self) -> bool:
        """Return whether policy-driven level changes are frozen."""
        return self.settings.level_lock

    @property
    def suggested_level(self) -> SoothingLevel | None:
        """Return the exact next level for a manual recommendation."""
        if self.recommendation is not Recommendation.INCREASE_LEVEL:
            return None
        return self.level.next_active()

    @property
    def configured(self) -> bool:
        """Return whether every stable dependency and sound is configured."""
        return (
            self.cry_sensor is not None
            and self.camera is not None
            and self.media_player is not None
            and all(
                isinstance(self.sounds.get(level), dict)
                and isinstance(self.sounds[level].get(ATTR_MEDIA_CONTENT_ID), str)
                and isinstance(self.sounds[level].get(ATTR_MEDIA_CONTENT_TYPE), str)
                for level in ACTIVE_LEVELS
            )
            and bool(self.notify_targets)
        )

    @property
    def dependencies_available(self) -> bool:
        """Return whether all selected Home Assistant entities are usable."""
        return self.configured and not self._find_dependency_issues()

    @property
    def attention_required(self) -> bool:
        """Return whether the controller is asking a parent to intervene."""
        return self.state is SootherState.ATTENTION_REQUIRED

    @property
    def notification_tag(self) -> str:
        """Return the stable notification replacement tag for this entry."""
        return f"{NOTIFICATION_TAG_PREFIX}-{self.entry.entry_id}"

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return privacy-safe runtime diagnostics."""
        evidence = self._evidence.snapshot(dt_util.utcnow())
        return {
            "state": self.state,
            "recommendation": self.recommendation,
            "level": self.level,
            "automatic_operation": self.automatic,
            "level_locked": self.locked,
            "enabled": self.enabled,
            "configured": self.configured,
            "dependencies_available": self.dependencies_available,
            "dependency_issue_types": sorted(self._dependency_issues),
            "cry_episode_active": self._incident_active,
            "cry_episode_confirmed": self._episode_confirmed,
            "provisional_level_1": self._provisional_level_1,
            "initial_level_1_applied": self._initial_level_1_applied,
            "stage_evidence": {
                "events": evidence.events,
                "active_seconds": round(evidence.active_seconds, 3),
                "simulated_events": self._stage_simulated_events,
            },
            "playback_owned": self._owns_playback,
            "playback_interrupted": self._playback_interrupted,
            "native_crossfade_loop_active": self._native_loop_active,
            "physical_control": {
                "configured": bool(
                    self.toggle_triggers
                    or self.increase_level_triggers
                    or self.decrease_level_triggers
                ),
                "toggle_triggers_attached": self._toggle_triggers_attached,
                "increase_level_triggers_attached": (
                    self._increase_level_triggers_attached
                ),
                "decrease_level_triggers_attached": (
                    self._decrease_level_triggers_attached
                ),
            },
            "timers": {
                "evidence": self._cancel_evidence is not None,
                "provisional": self._cancel_provisional is not None,
                "cry_gap": self._cancel_cry_gap is not None,
                "settling": self._cancel_settling is not None,
                "attention": self._cancel_attention is not None,
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
        """Start listeners and restore the selected soothing session."""
        async with self._lock:
            await self._async_start_locked()

    async def _async_start_locked(self) -> None:
        """Start while serializing recovery with state events and commands."""
        self._started = True
        watched_entities = [
            entity_id
            for entity_id in (
                self.cry_sensor,
                self.camera,
                self.media_player,
                self._sonos_crossfade_entity_id(),
            )
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
        await self._async_attach_physical_control_triggers()
        self._dependency_issues = self._find_dependency_issues()

        if not self.configured:
            self.settings.level = SoothingLevel.STANDBY
            self._persist_settings()
            self._transition(
                SootherState.ATTENTION_REQUIRED,
                Recommendation.CHECK_DEVICES,
                "incomplete configuration",
            )
            return

        if not self.enabled:
            self._transition(
                SootherState.STANDBY, Recommendation.START, "standby startup"
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

        if self._media_player_is_active():
            self._playback_interrupted = True
            self.settings.level = SoothingLevel.STANDBY
            self._persist_settings()
            self._transition(
                SootherState.ATTENTION_REQUIRED,
                Recommendation.CHECK_DEVICES,
                "speaker already active at startup",
            )
            await self._async_notify_playback_replaced()
            return

        if not await self._async_ensure_playback():
            return
        self._transition(
            SootherState.SOOTHING,
            Recommendation.NONE,
            f"restored {self.level.value}",
        )
        if self._physical_cry_is_on():
            now = dt_util.utcnow()
            self._start_cry_episode(now, physical_active=True)
            await self._async_evaluate_cry_evidence(now)

    async def async_shutdown(self) -> bool:
        """Cancel work and prevent integration-owned playback from being orphaned."""
        async with self._lock:
            stopped = await self._async_stop_playback()
            if not stopped or self._failed_play_context_ids:
                return False
            self._stop_runtime_work()
            return stopped

    async def async_abort_startup(self) -> None:
        """Detach a failed setup attempt even when playback could not be stopped."""
        async with self._lock:
            self._stop_runtime_work()

    def _stop_runtime_work(self) -> None:
        """Cancel listeners and timers owned by this controller instance."""
        self._started = False
        self._episode += 1
        self._cancel_all_timers()
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        for cancel in self._failed_play_expiries.values():
            cancel()
        self._failed_play_expiries.clear()
        self._failed_play_context_ids.clear()
        self._failed_play_media_content_ids.clear()
        self._failed_native_loop_context_ids.clear()
        self._native_loop_settings = None
        self._native_loop_active = False
        self._native_loop_force_restore = False
        self._toggle_triggers_attached = False
        self._increase_level_triggers_attached = False
        self._decrease_level_triggers_attached = False

    async def _async_attach_physical_control_triggers(self) -> None:
        """Attach optional generic Home Assistant triggers for physical control."""
        if self.toggle_triggers:
            toggle_triggers = await self._async_validate_physical_triggers(
                self.toggle_triggers, "toggle"
            )
            remove_toggle_triggers = await async_initialize_triggers(
                self.hass,
                toggle_triggers,
                self._async_toggle_triggered,
                DOMAIN,
                f"{self.entry.title} physical toggle",
                self._log_physical_trigger_setup,
            )
            if remove_toggle_triggers is not None:
                self._unsubscribers.append(remove_toggle_triggers)
                self._toggle_triggers_attached = True

        if self.increase_level_triggers:
            increase_level_triggers = await self._async_validate_physical_triggers(
                self.increase_level_triggers, "increase"
            )
            remove_increase_triggers = await async_initialize_triggers(
                self.hass,
                increase_level_triggers,
                self._async_increase_triggered,
                DOMAIN,
                f"{self.entry.title} physical increase",
                self._log_physical_trigger_setup,
            )
            if remove_increase_triggers is not None:
                self._unsubscribers.append(remove_increase_triggers)
                self._increase_level_triggers_attached = True

        if self.decrease_level_triggers:
            decrease_level_triggers = await self._async_validate_physical_triggers(
                self.decrease_level_triggers, "decrease"
            )
            remove_decrease_triggers = await async_initialize_triggers(
                self.hass,
                decrease_level_triggers,
                self._async_decrease_triggered,
                DOMAIN,
                f"{self.entry.title} physical decrease",
                self._log_physical_trigger_setup,
            )
            if remove_decrease_triggers is not None:
                self._unsubscribers.append(remove_decrease_triggers)
                self._decrease_level_triggers_attached = True

    async def _async_validate_physical_triggers(
        self, trigger_configs: list[dict[str, Any]], action: str
    ) -> list[dict[str, Any]]:
        """Fully validate optional triggers without blocking soothing setup."""
        try:
            return await async_validate_trigger_config(self.hass, trigger_configs)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Nursery physical %s trigger setup failed (%s)",
                action,
                type(err).__name__,
            )
            return []

    async def _async_toggle_triggered(
        self,
        run_variables: dict[str, Any],
        context: Context | None = None,
    ) -> None:
        """Apply one configured physical toggle trigger."""
        del run_variables, context
        try:
            await self.async_toggle_from_trigger()
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Nursery physical toggle action failed (%s)", type(err).__name__
            )

    async def _async_increase_triggered(
        self,
        run_variables: dict[str, Any],
        context: Context | None = None,
    ) -> None:
        """Apply one configured physical increase trigger."""
        del run_variables, context
        try:
            await self.async_increase_from_trigger()
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Nursery physical increase action failed (%s)", type(err).__name__
            )

    async def _async_decrease_triggered(
        self,
        run_variables: dict[str, Any],
        context: Context | None = None,
    ) -> None:
        """Apply one configured physical decrease trigger."""
        del run_variables, context
        try:
            await self.async_decrease_from_trigger()
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Nursery physical decrease action failed (%s)", type(err).__name__
            )

    def _log_physical_trigger_setup(
        self, level: int, message: str, **kwargs: object
    ) -> None:
        """Prefix Home Assistant trigger setup logs with integration context."""
        _LOGGER.log(level, "Physical control: %s", message, **kwargs)

    async def async_simulate_cry_event(self) -> None:
        """Inject one point-in-time cry event through the normal response path."""
        async with self._lock:
            self._ensure_started()
            if not self.enabled:
                return
            self._validate_controllable()
            now = dt_util.utcnow()
            if not self._incident_active:
                self._start_cry_episode(now, physical_active=False)
            self._evidence.record_event(now)
            self._stage_simulated_events += 1
            self._record_cry_activity(now)
            await self._async_evaluate_cry_evidence(now)

    async def async_toggle_from_trigger(self) -> None:
        """Toggle a physical control between Standby and an active session."""
        async with self._lock:
            self._ensure_started()
            if self.enabled:
                await self._async_enter_standby("standby selected by physical control")
                return
            await self._async_set_active_level(SoothingLevel.BASELINE)

    async def async_increase_from_trigger(self) -> None:
        """Increase one exact level for an active physical-control command."""
        async with self._lock:
            self._ensure_started()
            if not self.enabled:
                return
            requested = self.level.next_active()
            if requested is None:
                return
            await self._async_set_active_level(requested)

    async def async_decrease_from_trigger(self) -> None:
        """Decrease one exact level for an active physical-control command."""
        async with self._lock:
            self._ensure_started()
            if not self.enabled:
                return
            requested = self.level.previous_active()
            if requested is None:
                return
            await self._async_set_active_level(requested)

    async def async_set_level(
        self,
        level: SoothingLevel | str,
        *,
        expected_action_generation: int | None = None,
    ) -> None:
        """Select Standby or one exact soothing output level."""
        requested = SoothingLevel(level)
        async with self._lock:
            self._ensure_started()
            if (
                expected_action_generation is not None
                and expected_action_generation != self._action_generation
            ):
                return
            if requested is self.level:
                if (
                    requested is SoothingLevel.STANDBY
                    and (
                        self._native_loop_active
                        or self._native_loop_settings is not None
                    )
                    and not await self._async_stop_playback()
                ):
                    self._transition(
                        SootherState.ATTENTION_REQUIRED,
                        Recommendation.CHECK_DEVICES,
                        "speaker loop cleanup failed",
                    )
                    await self._async_notify_dependency_problem()
                    return
                if requested is SoothingLevel.STANDBY and self._playback_interrupted:
                    self._playback_interrupted = False
                    self._transition(
                        SootherState.STANDBY,
                        Recommendation.START,
                        "speaker interruption reset in standby",
                    )
                    await self._async_clear_notifications()
                return
            if requested is SoothingLevel.STANDBY:
                await self._async_enter_standby("standby selected by parent")
                return
            await self._async_set_active_level(requested)

    async def _async_set_active_level(self, requested: SoothingLevel) -> None:
        """Validate and apply one active level while the public lock is held."""
        previous_level = self.level
        self._validate_active_level_request()
        previous_media = self._media_for_level(previous_level)
        if self.enabled and not await self._async_playback_is_owned_now(
            notify_interruption=False
        ):
            if self._playback_interrupted and self.level is SoothingLevel.STANDBY:
                previous_level = SoothingLevel.STANDBY
                previous_media = None
            else:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="playback_settling",
                )
        self.settings.level = requested
        now = dt_util.utcnow()
        if not await self._async_apply_level(previous_media):
            self._restore_level_after_failed_effect(previous_level)
            if self.state is not SootherState.ATTENTION_REQUIRED:
                self._transition(
                    SootherState.ATTENTION_REQUIRED,
                    Recommendation.CHECK_DEVICES,
                    "parent-selected level could not be applied",
                )
                await self._async_notify_dependency_problem()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="level_change_failed",
            )
        self._cancel_provisional_response()
        self._persist_settings()

        if previous_level is SoothingLevel.STANDBY:
            await self._async_started_active_level(requested, now)
            return
        self._last_level_change_at = now
        if self._incident_active:
            self._reset_evidence_stage(now)
            self._transition(
                SootherState.RESPONDING,
                Recommendation.OBSERVE,
                f"{requested.value} selected during cry episode",
            )
            await self._async_evaluate_cry_evidence(now)
        else:
            self._transition(
                SootherState.SOOTHING,
                Recommendation.NONE,
                f"{requested.value} selected by parent",
            )
        await self._async_clear_notifications()

    def _validate_active_level_request(self) -> None:
        """Reject an active-level request that cannot safely start or change."""
        if self._failed_play_context_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="playback_settling",
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
        if self.enabled and self._awaiting_playback_confirmation:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="playback_settling",
            )

    async def _async_started_active_level(
        self, requested: SoothingLevel, now: datetime
    ) -> None:
        """Initialize a fresh session after leaving Standby."""
        self._new_episode()
        self._transition(
            SootherState.SOOTHING,
            Recommendation.NONE,
            f"{requested.value} selected by parent",
        )
        await self._async_clear_notifications()
        if self._physical_cry_is_on():
            self._start_cry_episode(now, physical_active=True)
            await self._async_evaluate_cry_evidence(now)

    async def async_set_automatic(
        self,
        *,
        enabled: bool,
        expected_action_generation: int | None = None,
    ) -> None:
        """Enable or disable automatic upward level changes."""
        async with self._lock:
            self._ensure_started()
            if (
                expected_action_generation is not None
                and expected_action_generation != self._action_generation
            ):
                return
            requested = bool(enabled)
            if requested is self.automatic:
                return
            self.settings.automatic_operation = requested
            self._persist_settings()
            now = dt_util.utcnow()
            if not requested and self._provisional_level_1:
                await self._async_rollback_provisional_level_1(
                    "automatic operation disabled during provisional response"
                )
                return
            if self.enabled and self._incident_active and self._episode_confirmed:
                self._reset_evidence_stage(now)
                self._last_level_change_at = now
                if requested:
                    self._transition(
                        SootherState.RESPONDING,
                        Recommendation.OBSERVE,
                        "automatic operation enabled during episode",
                    )
                    await self._async_clear_notifications()
                    await self._async_evaluate_cry_evidence(now)
                else:
                    self._cancel_timer("evidence")
                    self._transition(
                        SootherState.RESPONDING,
                        Recommendation.WAIT,
                        "automatic operation disabled during episode",
                    )
                    await self._async_clear_notifications()
                    await self._async_evaluate_cry_evidence(now)
            else:
                self._emit_update()
                if requested and self.enabled and self._incident_active:
                    await self._async_evaluate_cry_evidence(now)

    async def async_set_locked(self, *, locked: bool) -> None:
        """Freeze or resume policy-driven level changes."""
        async with self._lock:
            self._ensure_started()
            requested = bool(locked)
            if requested is self.locked:
                return
            self.settings.level_lock = requested
            self._persist_settings()
            now = dt_util.utcnow()
            if requested:
                if self._cancel_settling is not None:
                    self._settling_deferred_by_lock = True
                self._cancel_timer("settling")
                self._cancel_timer("provisional")
                self._provisional_level_1 = False
                self._emit_update()
                return
            if self._incident_active:
                await self._async_evaluate_cry_evidence(now)
                if (
                    self._initial_level_1_applied
                    and not self._episode_confirmed
                    and self.level is SoothingLevel.LEVEL_1
                ):
                    response_seconds = max(
                        float(self.settings.level_up_seconds),
                        self.settings.debounce_seconds + 0.001,
                    )
                    elapsed = (
                        (now - self._last_level_change_at).total_seconds()
                        if self._last_level_change_at is not None
                        else response_seconds
                    )
                    remaining = response_seconds - elapsed
                    if remaining <= 0:
                        await self._async_rollback_provisional_level_1(
                            "level lock released after unconfirmed cry event"
                        )
                    else:
                        self._provisional_level_1 = True
                        self._schedule_provisional_rollback(remaining)
                return
            if self._settling_deferred_by_lock and self.enabled:
                self._settling_deferred_by_lock = False
                self._schedule_settling()
            self._emit_update()

    async def async_set_volume(self, key: str, value: float) -> None:
        """Persist one volume setting after validating all relationships."""
        async with self._lock:
            self._ensure_started()
            proposed = {
                CONF_BASELINE_VOLUME: self.settings.baseline_volume,
                CONF_LEVEL_1_VOLUME: self.settings.level_1_volume,
                CONF_LEVEL_2_VOLUME: self.settings.level_2_volume,
                CONF_LEVEL_3_VOLUME: self.settings.level_3_volume,
                CONF_LEVEL_4_VOLUME: self.settings.level_4_volume,
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
                <= proposed[CONF_LEVEL_1_VOLUME]
                <= proposed[CONF_LEVEL_2_VOLUME]
                <= proposed[CONF_LEVEL_3_VOLUME]
                <= proposed[CONF_LEVEL_4_VOLUME]
                <= proposed[CONF_MAX_VOLUME]
                <= MAX_VOLUME_PERCENT
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="invalid_volume"
                )

            self.settings.baseline_volume = proposed[CONF_BASELINE_VOLUME]
            self.settings.level_1_volume = proposed[CONF_LEVEL_1_VOLUME]
            self.settings.level_2_volume = proposed[CONF_LEVEL_2_VOLUME]
            self.settings.level_3_volume = proposed[CONF_LEVEL_3_VOLUME]
            self.settings.level_4_volume = proposed[CONF_LEVEL_4_VOLUME]
            self.settings.max_volume = proposed[CONF_MAX_VOLUME]
            self._persist_settings()

            if (
                self.enabled
                and self.dependencies_available
                and self._owns_playback
                and key == LEVEL_VOLUME_KEYS[self.level]
            ):
                await self._async_set_speaker_volume(
                    self.settings.volume_for_level(self.level)
                )
            self._emit_update()

    async def _async_enter_standby(self, reason: str) -> None:
        """Stop owned playback and make Standby the single off state."""
        stopped = await self._async_stop_playback()
        if not stopped:
            self._transition(
                SootherState.ATTENTION_REQUIRED,
                Recommendation.CHECK_DEVICES,
                "speaker stop failed",
            )
            await self._async_notify_dependency_problem()
            return
        self.settings.level = SoothingLevel.STANDBY
        self._persist_settings()
        self._new_episode()
        self._transition(SootherState.STANDBY, Recommendation.START, reason)
        await self._async_clear_notifications()

    async def _async_apply_level(self, previous_media: dict[str, Any] | None) -> bool:
        """Apply the current level, changing media only when its source differs."""
        current_media = self._media_for_level(self.level)
        if previous_media == current_media and self._owns_playback:
            return await self._async_set_speaker_volume(
                self.settings.volume_for_level(self.level)
            )
        if self._owns_playback and not await self._async_stop_playback(
            preserve_native_loop_session=True
        ):
            return False
        return await self._async_ensure_playback()

    def _restore_level_after_failed_effect(self, previous_level: SoothingLevel) -> None:
        """Publish a level consistent with the output left after a failed effect."""
        self.settings.level = (
            previous_level
            if self._owns_playback and not self._playback_interrupted
            else SoothingLevel.STANDBY
        )
        self._persist_settings()
        self._emit_update()

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
                await self._async_handle_cry_state_changed(event)
            elif entity_id == self.media_player:
                await self._async_handle_media_state_changed(event)
            elif entity_id == self._sonos_crossfade_entity_id():
                await self._async_handle_native_loop_control_changed()

    async def _async_handle_cry_state_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Convert only concrete binary-sensor edges into cry evidence."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        old_on = old_state is not None and old_state.state == STATE_ON
        new_on = new_state is not None and new_state.state == STATE_ON
        if old_on == new_on:
            return
        now = dt_util.utcnow()
        if new_on:
            if not self._incident_active:
                self._start_cry_episode(now, physical_active=False)
            if not self._evidence.record_on(now):
                return
            self._record_cry_activity(now)
        else:
            if not self._incident_active:
                return
            if self._evidence.record_off(now) is None:
                return
            self._record_cry_activity(now)
        await self._async_evaluate_cry_evidence(now)

    async def _async_handle_media_state_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Guard owned playback and fail safe on an external takeover."""
        if self._media_was_replaced(event):
            await self._async_finalize_playback_interruption()
            return
        if self._native_loop_active and not self._native_loop_controls_are_current():
            await self._async_handle_native_loop_control_changed()
            return
        current_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        should_restart = (
            self._owns_playback
            and current_state is not None
            and current_state.state
            in {
                MediaPlayerState.IDLE,
                MediaPlayerState.OFF,
                MediaPlayerState.PAUSED,
            }
        )
        if not should_restart:
            return
        self._clear_playback_ownership()
        if not await self._async_ensure_playback():
            await self._async_handle_playback_recovery_failure(
                "owned playback recovery failed"
            )

    def _native_loop_controls_are_current(self) -> bool:
        """Return whether the active Sonos loop still has both required modes."""
        if not self._native_loop_active or self._native_loop_settings is None:
            return True
        media_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        crossfade_state = self.hass.states.get(
            self._native_loop_settings.crossfade_entity_id
        )
        return (
            media_state is not None
            and crossfade_state is not None
            and media_state.attributes.get(ATTR_MEDIA_REPEAT) == RepeatMode.ALL
            and crossfade_state.state == STATE_ON
        )

    async def _async_handle_native_loop_control_changed(self) -> None:
        """Stop a loop that can no longer satisfy its continuity contract."""
        if not self._native_loop_active or self._native_loop_settings is None:
            return
        if (
            await self._async_refresh_entity(
                self._native_loop_settings.crossfade_entity_id
            )
            and self._native_loop_controls_are_current()
        ):
            return
        self._last_error = "native_loop_controls_changed"
        await self._async_stop_playback()
        self.settings.level = SoothingLevel.STANDBY
        self._persist_settings()
        self._new_episode()
        self._transition(
            SootherState.ATTENTION_REQUIRED,
            Recommendation.CHECK_DEVICES,
            "Sonos repeat or crossfade changed during playback",
        )
        await self._async_notify_dependency_problem()

    async def _async_handle_playback_recovery_failure(self, reason: str) -> None:
        """Expose a failed restart instead of claiming silent soothing."""
        if self.state is SootherState.ATTENTION_REQUIRED:
            return
        self._transition(
            SootherState.ATTENTION_REQUIRED,
            Recommendation.CHECK_DEVICES,
            reason,
        )
        await self._async_notify_dependency_problem()

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
            if self.enabled:
                await self._async_handle_dependency_loss(old_issues, new_issues)
            return True

        if old_issues and self.enabled:
            await self._async_handle_dependency_recovery()
            return True
        return False

    async def _async_handle_dependency_loss(
        self, old_issues: set[str], new_issues: set[str]
    ) -> None:
        """Pause policy and lower an active session to its safest level."""
        self._cancel_all_timers()
        self._incident_active = False
        self._episode_confirmed = False
        if new_issues != old_issues:
            self._episode += 1
        if self.level is not SoothingLevel.BASELINE:
            previous_level = self.level
            previous_media = self._media_for_level(previous_level)
            self.settings.level = SoothingLevel.BASELINE
            if (
                self._media_player_available()
                and self._owns_playback
                and not await self._async_apply_level(previous_media)
            ):
                self._restore_level_after_failed_effect(previous_level)
                return
            self._persist_settings()
        self._transition(
            SootherState.ATTENTION_REQUIRED,
            Recommendation.CHECK_DEVICES,
            "dependency became unavailable",
        )
        if new_issues != old_issues:
            await self._async_notify_dependency_problem()

    async def _async_handle_dependency_recovery(self) -> None:
        """Resume the selected safe level after all dependencies recover."""
        if self._owns_playback:
            await self._async_playback_is_owned_now()
        if self._playback_interrupted:
            self._transition(
                SootherState.ATTENTION_REQUIRED,
                Recommendation.CHECK_DEVICES,
                "dependency recovered while speaker remained externally owned",
            )
            return
        await self._async_notify_recovery()
        self._new_episode()
        if not self._owns_playback and not await self._async_ensure_playback():
            await self._async_handle_playback_recovery_failure(
                "dependency recovery could not restart playback"
            )
            return
        self._transition(
            SootherState.SOOTHING,
            Recommendation.NONE,
            "dependencies recovered",
        )
        if self._physical_cry_is_on():
            now = dt_util.utcnow()
            self._start_cry_episode(now, physical_active=True)
            await self._async_evaluate_cry_evidence(now)

    def _start_cry_episode(self, now: datetime, *, physical_active: bool) -> None:
        """Start one event episode without requiring a continuously-on sensor."""
        self._new_episode()
        self._incident_active = True
        self._episode_confirmed = False
        self._episode_started_at = now
        self._confirmed_at = None
        self._last_level_change_at = None
        self._stage_simulated_events = 0
        self._evidence.reset(now, active=physical_active)
        self._transition(
            SootherState.CRY_PENDING,
            Recommendation.WAIT,
            "cry episode started",
        )
        self._record_cry_activity(now)

    def _record_cry_activity(self, now: datetime) -> None:
        """Extend the event episode and restart its quiet clocks."""
        self._last_cry_activity_at = now
        self._schedule_cry_gap()

    async def _async_evaluate_cry_evidence(self, now: datetime) -> None:
        """Confirm or advance an episode once evidence and timing both allow it."""
        if (
            not self._started
            or not self.enabled
            or not self._incident_active
            or not self.dependencies_available
        ):
            return
        snapshot = self._evidence.snapshot(now)
        if (
            not self._episode_confirmed
            and not self._provisional_level_1
            and not self._initial_level_1_applied
            and self.automatic
            and not self.locked
            and self.level is SoothingLevel.BASELINE
            and (snapshot.events > 0 or self._physical_cry_is_on())
            and not await self._async_start_provisional_level_1(now)
        ):
            return
        event_threshold, active_seconds_threshold = self._evidence_thresholds()
        evidence_ready = (
            snapshot.events >= event_threshold
            or snapshot.active_seconds >= active_seconds_threshold
        )
        gate_started_at = (
            self._last_level_change_at
            if self._episode_confirmed
            else self._episode_started_at
        )
        gate_seconds = (
            self.settings.level_up_seconds
            if self._episode_confirmed
            else self.settings.debounce_seconds
        )
        gate_remaining = (
            max(0.0, gate_seconds - (now - gate_started_at).total_seconds())
            if gate_started_at is not None
            else float(gate_seconds)
        )

        if evidence_ready and gate_remaining <= 0:
            self._cancel_timer("evidence")
            simulated_only = (
                snapshot.events > 0
                and self._stage_simulated_events >= snapshot.events
                and snapshot.active_seconds == 0
            )
            if not self._episode_confirmed:
                self._episode_confirmed = True
                self._confirmed_at = now
                self._schedule_attention()
            await self._async_handle_confirmed_evidence(
                now, snapshot, simulated_only=simulated_only
            )
            return

        delay: float | None = gate_remaining if evidence_ready else None
        active_delay = self._evidence.seconds_until_active_threshold(
            now, active_seconds_threshold
        )
        if active_delay is not None:
            held_delay = max(active_delay, gate_remaining)
            delay = held_delay if delay is None else min(delay, held_delay)
        if delay is not None:
            self._schedule_evidence(max(delay, 0.001))
        self._emit_update()

    async def _async_handle_confirmed_evidence(
        self,
        now: datetime,
        snapshot: EvidenceSnapshot,
        *,
        simulated_only: bool,
    ) -> None:
        """Raise one level automatically or send one exact manual suggestion."""
        if not self._provisional_level_1:
            self._initial_level_1_applied = False
        provisional_started_at = (
            self._last_level_change_at if self._initial_level_1_applied else None
        )
        if (
            self.automatic
            and self._initial_level_1_applied
            and self.level is SoothingLevel.LEVEL_1
        ):
            self._cancel_provisional_response()
            self._transition(
                SootherState.RESPONDING,
                Recommendation.OBSERVE,
                "provisional level_1 response confirmed",
            )
            delivered = await self._async_notify_automatic_change(
                SoothingLevel.LEVEL_1,
                snapshot,
                simulated_only=simulated_only,
            )
            if not delivered:
                await self._async_fail_safe_notification_delivery()
                return
            self._last_level_change_at = provisional_started_at or now
            self._reset_evidence_stage(now)
            self._evaluate_after_stage_reset(now)
            return

        next_level = self.level.next_active()
        if self.automatic and not self.locked and next_level is not None:
            previous_level = self.level
            previous_media = self._media_for_level(previous_level)
            self.settings.level = next_level
            if not await self._async_apply_level(previous_media):
                self._restore_level_after_failed_effect(previous_level)
                return
            self._persist_settings()
            self._last_level_change_at = now
            self._transition(
                SootherState.RESPONDING,
                Recommendation.OBSERVE,
                f"automatic increase to {next_level.value}",
            )
            delivered = await self._async_notify_automatic_change(
                next_level, snapshot, simulated_only=simulated_only
            )
        else:
            recommendation = (
                Recommendation.INCREASE_LEVEL
                if next_level is not None
                else Recommendation.ATTEND
            )
            self._transition(
                SootherState.RESPONDING,
                recommendation,
                "manual cry recommendation"
                if next_level is not None
                else "crying continues at level 4",
            )
            delivered = await self._async_notify_cry(
                snapshot, simulated_only=simulated_only
            )
        if not delivered:
            await self._async_fail_safe_notification_delivery()
            return
        self._last_level_change_at = now
        self._reset_evidence_stage(now)
        self._evaluate_after_stage_reset(now)

    async def _async_start_provisional_level_1(self, now: datetime) -> bool:
        """Apply a reversible Level 1 response to the first cry event."""
        previous_level = self.level
        previous_media = self._media_for_level(previous_level)
        self.settings.level = SoothingLevel.LEVEL_1
        if not await self._async_apply_level(previous_media):
            self._restore_level_after_failed_effect(previous_level)
            return False
        self._persist_settings()
        self._provisional_level_1 = True
        self._initial_level_1_applied = True
        self._last_level_change_at = now
        self._transition(
            SootherState.CRY_PENDING,
            Recommendation.WAIT,
            "provisional level_1 response to initial cry event",
        )
        self._schedule_provisional_rollback()
        return True

    def _schedule_provisional_rollback(self, delay: float | None = None) -> None:
        """Return an unconfirmed provisional response to Baseline after dwell."""
        self._cancel_timer("provisional")
        episode = self._episode
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            del now
            async with self._lock:
                if self._cancel_provisional is not cancel_callback:
                    return
                self._cancel_provisional = None
                if (
                    episode != self._episode
                    or not self._provisional_level_1
                    or self._episode_confirmed
                ):
                    return
                await self._async_rollback_provisional_level_1(
                    "initial cry event was not confirmed"
                )

        cancel_callback = async_call_later(
            self.hass,
            max(
                float(self.settings.level_up_seconds),
                self.settings.debounce_seconds + 0.001,
            )
            if delay is None
            else delay,
            _expired,
        )
        self._cancel_provisional = cancel_callback

    async def _async_rollback_provisional_level_1(self, reason: str) -> None:
        """Safely undo an unconfirmed provisional Level 1 response."""
        self._cancel_provisional_response(clear_initial=False)
        if self.level is not SoothingLevel.LEVEL_1:
            return
        previous_media = self._media_for_level(SoothingLevel.LEVEL_1)
        self.settings.level = SoothingLevel.BASELINE
        if not await self._async_apply_level(previous_media):
            self._restore_level_after_failed_effect(SoothingLevel.LEVEL_1)
            return
        self._persist_settings()
        self._last_level_change_at = None
        self._transition(SootherState.CRY_PENDING, Recommendation.WAIT, reason)

    def _cancel_provisional_response(self, *, clear_initial: bool = True) -> None:
        """Clear provisional policy state without changing the selected level."""
        self._cancel_timer("provisional")
        self._provisional_level_1 = False
        if clear_initial:
            self._initial_level_1_applied = False

    async def _async_fail_safe_notification_delivery(self) -> None:
        """Stop owned output when no caregiver notification can be delivered."""
        self._cancel_all_timers()
        stopped = await self._async_stop_playback()
        self._new_episode()
        if stopped:
            self.settings.level = SoothingLevel.STANDBY
            self._persist_settings()
        self._transition(
            SootherState.ATTENTION_REQUIRED,
            Recommendation.CHECK_DEVICES,
            "all notification deliveries failed",
        )

    def _evaluate_after_stage_reset(self, now: datetime) -> None:
        """Schedule held-sensor evidence after a stage consumes prior evidence."""
        active_delay = self._evidence.seconds_until_active_threshold(
            now, ESCALATION_CRY_ACTIVE_SECONDS_THRESHOLD
        )
        if active_delay is not None:
            dwell_remaining = (
                max(
                    0.0,
                    self.settings.level_up_seconds
                    - (now - self._last_level_change_at).total_seconds(),
                )
                if self._last_level_change_at is not None
                else float(self.settings.level_up_seconds)
            )
            self._schedule_evidence(max(active_delay, dwell_remaining, 0.001))

    def _evidence_thresholds(self) -> tuple[int, float]:
        """Return conservative initial or responsive fresh-stage thresholds."""
        if self._episode_confirmed:
            return (
                ESCALATION_CRY_EVENT_THRESHOLD,
                ESCALATION_CRY_ACTIVE_SECONDS_THRESHOLD,
            )
        return INITIAL_CRY_EVENT_THRESHOLD, INITIAL_CRY_ACTIVE_SECONDS_THRESHOLD

    def _reset_evidence_stage(self, now: datetime) -> None:
        """Require fresh post-response evidence before another recommendation."""
        self._evidence.reset(now, active=self._physical_cry_is_on())
        self._stage_simulated_events = 0
        self._cancel_timer("evidence")

    def _schedule_evidence(self, delay: float) -> None:
        """Re-evaluate when a held pulse or timing gate can become eligible."""
        self._cancel_timer("evidence")
        episode = self._episode
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            async with self._lock:
                if self._cancel_evidence is not cancel_callback:
                    return
                self._cancel_evidence = None
                if episode != self._episode:
                    return
                await self._async_evaluate_cry_evidence(now)

        cancel_callback = async_call_later(self.hass, delay, _expired)
        self._cancel_evidence = cancel_callback

    def _schedule_cry_gap(self) -> None:
        """End an episode after no cry activity for the configured gap."""
        self._cancel_timer("cry_gap")
        episode = self._episode
        activity_at = self._last_cry_activity_at
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            async with self._lock:
                if self._cancel_cry_gap is not cancel_callback:
                    return
                self._cancel_cry_gap = None
                if (
                    episode != self._episode
                    or activity_at != self._last_cry_activity_at
                    or not self._incident_active
                ):
                    return
                if self._physical_cry_is_on():
                    self._last_cry_activity_at = now
                    self._schedule_cry_gap()
                    return
                await self._async_finish_cry_gap(now, activity_at)

        cancel_callback = async_call_later(
            self.hass, self.settings.cry_gap_seconds, _expired
        )
        self._cancel_cry_gap = cancel_callback

    async def _async_finish_cry_gap(
        self, now: datetime, activity_at: datetime | None
    ) -> None:
        """Resolve an episode as quiet, including exact deadline ties."""
        if self._provisional_level_1:
            await self._async_rollback_provisional_level_1(
                "cry event gap elapsed before confirmation"
            )
            if self.level is not SoothingLevel.BASELINE:
                return
        self._cancel_timer("cry_gap")
        self._incident_active = False
        self._episode_confirmed = False
        self._cancel_timer("evidence")
        self._cancel_timer("attention")
        self._episode += 1
        self._evidence.reset(now)
        self._stage_simulated_events = 0
        self._initial_level_1_applied = False
        self._transition(
            SootherState.SETTLING,
            Recommendation.SETTLING,
            "cry event gap elapsed",
        )
        elapsed_quiet = (
            (now - activity_at).total_seconds() if activity_at is not None else 0.0
        )
        if self.locked:
            self._settling_deferred_by_lock = True
            await self._async_clear_notifications()
            return
        self._schedule_settling(
            max(0.001, self.settings.settling_seconds - elapsed_quiet)
        )
        await self._async_clear_notifications()

    def _schedule_settling(self, delay: float | None = None) -> None:
        """Step down exactly one level after uninterrupted quiet."""
        self._cancel_timer("settling")
        if self.locked:
            self._settling_deferred_by_lock = True
            return
        episode = self._episode
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            del now
            async with self._lock:
                if self._cancel_settling is not cancel_callback:
                    return
                self._cancel_settling = None
                if (
                    episode != self._episode
                    or not self._started
                    or not self.enabled
                    or self.locked
                    or not self.dependencies_available
                    or self._incident_active
                    or self._physical_cry_is_on()
                ):
                    return
                previous_level = self.level
                lower_level = previous_level.previous_active()
                if lower_level is None:
                    self._transition(
                        SootherState.SOOTHING,
                        Recommendation.NONE,
                        "quiet at baseline",
                    )
                    await self._async_clear_notifications()
                    return
                previous_media = self._media_for_level(previous_level)
                self.settings.level = lower_level
                if not await self._async_apply_level(previous_media):
                    self._restore_level_after_failed_effect(previous_level)
                    return
                self._persist_settings()
                if lower_level is SoothingLevel.BASELINE:
                    self._transition(
                        SootherState.SOOTHING,
                        Recommendation.NONE,
                        "quiet settling reached baseline",
                    )
                    await self._async_clear_notifications()
                    return
                self._transition(
                    SootherState.SETTLING,
                    Recommendation.SETTLING,
                    f"quiet settling lowered to {lower_level.value}",
                )
                self._schedule_settling()

        cancel_callback = async_call_later(
            self.hass,
            self.settings.settling_seconds if delay is None else delay,
            _expired,
        )
        self._cancel_settling = cancel_callback

    def _schedule_attention(self) -> None:
        """Stop soothing and alert a parent at the fixed episode deadline."""
        self._cancel_timer("attention")
        episode = self._episode
        confirmed_at = self._confirmed_at
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            async with self._lock:
                if self._cancel_attention is not cancel_callback:
                    return
                self._cancel_attention = None
                if (
                    episode != self._episode
                    or confirmed_at != self._confirmed_at
                    or not self.enabled
                    or not self._incident_active
                    or not self._episode_confirmed
                ):
                    return
                activity_at = self._last_cry_activity_at
                if (
                    activity_at is not None
                    and not self._physical_cry_is_on()
                    and (now - activity_at).total_seconds()
                    >= self.settings.cry_gap_seconds
                ):
                    await self._async_finish_cry_gap(now, activity_at)
                    return
                stopped = await self._async_stop_playback()
                if not stopped:
                    self._transition(
                        SootherState.ATTENTION_REQUIRED,
                        Recommendation.CHECK_DEVICES,
                        "attention timeout could not stop speaker",
                    )
                    await self._async_notify_dependency_problem()
                    return
                self.settings.level = SoothingLevel.STANDBY
                self._persist_settings()
                self._episode += 1
                self._cancel_all_timers()
                self._incident_active = False
                self._episode_confirmed = False
                self._transition(
                    SootherState.ATTENTION_REQUIRED,
                    Recommendation.ATTEND,
                    "persistent cry attention deadline",
                )
                await self._async_notify_attention()

        cancel_callback = async_call_later(
            self.hass, self.settings.attention_seconds, _expired
        )
        self._cancel_attention = cancel_callback

    async def _async_notification_action(self, event: Event[dict[str, Any]]) -> None:
        """Accept only current actions belonging to this entry and episode."""
        if not self._started:
            return
        parsed_action = self._parse_notification_action(event.data.get("action"))
        if parsed_action is None:
            return
        action_generation, command = parsed_action

        level_prefix = f"{ACTION_SET_LEVEL}="
        if command.startswith(level_prefix):
            try:
                level = SoothingLevel(command.removeprefix(level_prefix))
            except ValueError:
                return
            try:
                await self.async_set_level(
                    level, expected_action_generation=action_generation
                )
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "Nursery notification level action failed for %s (%s)",
                    level,
                    type(err).__name__,
                )
        elif command == ACTION_SET_MANUAL:
            await self.async_set_automatic(
                enabled=False,
                expected_action_generation=action_generation,
            )

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
            action_generation = int(parts[3])
        except ValueError:
            return None
        if action_generation != self._action_generation:
            return None
        return action_generation, parts[4]

    def _notification_action(self, command: str) -> str:
        """Build an action identifier unique to the current episode."""
        return (
            f"{NOTIFICATION_ACTION_PREFIX}:{self.entry.entry_id}:"
            f"{self._session_id}:{self._action_generation}:{command}"
        )

    async def _async_notify_cry(
        self, snapshot: EvidenceSnapshot, *, simulated_only: bool
    ) -> bool:
        """Explain the evidence and suggest one exact next manual level."""
        self._action_generation += 1
        next_level = self.level.next_active()
        prefix = "[Test] Simulated " if simulated_only else ""
        evidence = (
            f"{snapshot.events} cry events and "
            f"{snapshot.active_seconds:.1f} detected seconds in "
            f"{self.settings.evidence_window_seconds} seconds"
        )
        if next_level is None:
            message = (
                f"{prefix}cry evidence: {evidence}. The soother is already at "
                "Level 4; please check the nursery."
            )
            actions = [self._level_action(SoothingLevel.STANDBY)]
        else:
            message = (
                f"{prefix}cry evidence: {evidence}. Current level is "
                f"{self._level_title(self.level)}; consider "
                f"{self._level_title(next_level)}."
            )
            actions = [
                self._level_action(next_level),
                self._level_action(SoothingLevel.STANDBY),
            ]
        return await self._async_notify(message, actions)

    async def _async_notify_automatic_change(
        self,
        level: SoothingLevel,
        snapshot: EvidenceSnapshot,
        *,
        simulated_only: bool,
    ) -> bool:
        """Report one evidence-authorized automatic level change."""
        self._action_generation += 1
        prefix = "[Test] Simulated " if simulated_only else ""
        return await self._async_notify(
            (
                f"{prefix}cry evidence ({snapshot.events} events, "
                f"{snapshot.active_seconds:.1f} detected seconds) increased "
                f"Nursery Soother to {self._level_title(level)}."
            ),
            [
                self._action(ACTION_SET_MANUAL, "Use manual operation"),
                self._level_action(SoothingLevel.STANDBY),
            ],
        )

    async def _async_notify_attention(self) -> None:
        """Ask a parent to attend after the finite response window."""
        self._action_generation += 1
        await self._async_notify(
            (
                "Crying continued for the full response window. Nursery Soother "
                "is now in Standby; please check the nursery."
            ),
            [
                self._level_action(SoothingLevel.BASELINE),
            ],
        )

    async def _async_notify_dependency_problem(self) -> None:
        """Notify parents once when a selected dependency becomes unavailable."""
        self._action_generation += 1
        await self._async_notify(
            (
                "A nursery camera, cry sensor, speaker, or parent notification "
                "action is unavailable. Cry response is paused at a safe level."
            ),
            [self._level_action(SoothingLevel.STANDBY)] if self.enabled else [],
        )

    async def _async_notify_recovery(self) -> None:
        """Replace the dependency alert after recovery."""
        self._action_generation += 1
        await self._async_notify(
            f"Nursery devices are available again at {self._level_title(self.level)}.",
            [],
        )

    async def _async_notify_playback_replaced(self) -> None:
        """Tell parents that external speaker use paused the response loop."""
        self._action_generation += 1
        await self._async_notify(
            (
                "The nursery speaker was already active or started playing "
                "outside this soothing session. Nursery Soother moved to Standby "
                "without touching that media. Select an active level to start it "
                "again."
            ),
            [self._level_action(SoothingLevel.BASELINE)],
        )

    def _action(self, command: str, title: str) -> dict[str, str]:
        """Build one actionable-notification button."""
        return {"action": self._notification_action(command), "title": title}

    def _level_action(self, level: SoothingLevel) -> dict[str, str]:
        """Build an action selecting one exact soothing level."""
        return self._action(
            f"{ACTION_SET_LEVEL}={level.value}", self._level_title(level)
        )

    @staticmethod
    def _level_title(level: SoothingLevel) -> str:
        """Return the parent-facing title for one level."""
        return {
            SoothingLevel.STANDBY: "Standby",
            SoothingLevel.BASELINE: "Baseline",
            SoothingLevel.LEVEL_1: "Level 1",
            SoothingLevel.LEVEL_2: "Level 2",
            SoothingLevel.LEVEL_3: "Level 3",
            SoothingLevel.LEVEL_4: "Level 4",
        }[level]

    async def _async_notify(
        self,
        message: str,
        actions: list[dict[str, str]],
    ) -> bool:
        """Send an action-first notification, continuing past target failures."""
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
            # A plain image is a single camera frame. Deliberately omit
            # ``entity_id`` here: on iOS that enables the live camera player
            # and can displace the actionable-notification controls.
            "image": f"/api/camera_proxy/{camera}",
            "actions": actions,
        }

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
        self._action_generation += 1
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
        """Set a capped volume and start either native looping or direct media."""
        media_player = self.media_player
        if (
            not self.enabled
            or media_player is None
            or not self._entity_available(media_player)
        ):
            return False
        target = self.settings.volume_for_level(self.level)
        if not await self._async_set_speaker_volume(target, require_owned=False):
            if self._native_loop_settings is not None:
                await self._async_cleanup_native_loop(
                    clear_queue=self._native_loop_active,
                    end_session=True,
                )
            self._playback_interrupted = True
            return False

        media = self._media_for_level(self.level)
        if media is None:
            if self._native_loop_settings is not None:
                await self._async_cleanup_native_loop(
                    clear_queue=self._native_loop_active,
                    end_session=True,
                )
            return False
        crossfade_entity_id = self._sonos_crossfade_entity_id()
        if self._native_loop_settings is not None:
            crossfade_entity_id = self._native_loop_settings.crossfade_entity_id
        if crossfade_entity_id is not None and self._native_crossfade_loop_supported(
            crossfade_entity_id
        ):
            return await self._async_start_native_crossfade_loop(
                media, crossfade_entity_id
            )
        if self._native_loop_settings is not None:
            self._last_error = "native_crossfade_loop_unavailable"
            await self._async_cleanup_native_loop(
                clear_queue=self._native_loop_active,
                end_session=True,
            )
            self._playback_interrupted = True
            return False
        return await self._async_start_configured_media(media)

    async def _async_start_native_crossfade_loop(  # noqa: C901, PLR0911, PLR0912
        self, media: dict[str, Any], crossfade_entity_id: str
    ) -> bool:
        """Build one Sonos queue item and let repeat-all loop it natively."""
        if self._native_loop_settings is None:
            if not await self._async_refresh_entity(crossfade_entity_id):
                self._last_error = "native_crossfade_refresh_failed"
                return await self._async_start_configured_media(media)
            settings = self._capture_native_loop_settings(crossfade_entity_id)
            if settings is None:
                return await self._async_start_configured_media(media)
            self._native_loop_settings = settings

        if not await self._async_native_queue_can_be_replaced():
            await self._async_cleanup_native_loop(clear_queue=False, end_session=True)
            await self._async_handle_playback_recovery_failure(
                "speaker became active before Sonos queue setup"
            )
            return False

        crossfade_state = self.hass.states.get(crossfade_entity_id)
        if crossfade_state is None:
            await self._async_cleanup_native_loop(clear_queue=False, end_session=True)
            return False
        if crossfade_state.state == STATE_OFF:
            self._native_loop_force_restore = True
            crossfade_enabled = await self._async_call_entity(
                Platform.SWITCH,
                SERVICE_TURN_ON,
                crossfade_entity_id,
                {},
            ) and await self._async_refresh_entity(
                crossfade_entity_id, expected_state=STATE_ON
            )
            if not crossfade_enabled:
                self._last_error = "native_crossfade_not_confirmed"
                if await self._async_native_queue_can_be_replaced():
                    await self._async_cleanup_native_loop(
                        clear_queue=False, end_session=True
                    )
                await self._async_handle_playback_recovery_failure(
                    "Sonos crossfade could not be confirmed"
                )
                return False
            self._native_loop_force_restore = False

        if not await self._async_native_queue_can_be_replaced():
            await self._async_handle_playback_recovery_failure(
                "speaker takeover during Sonos queue setup"
            )
            return False

        if not await self._async_call_media(SERVICE_CLEAR_PLAYLIST, {}):
            await self._async_cleanup_native_loop(clear_queue=False, end_session=True)
            return False
        if not await self._async_native_queue_can_be_replaced():
            await self._async_handle_playback_recovery_failure(
                "speaker takeover after Sonos queue clear"
            )
            return False

        self._native_loop_active = True
        if not await self._async_start_configured_media(
            media,
            {ATTR_MEDIA_ENQUEUE: MediaPlayerEnqueue.PLAY},
            native_loop=True,
        ):
            await self._async_cleanup_native_loop(clear_queue=True, end_session=True)
            return False

        if not await self._async_wait_for_owned_playback():
            await self._async_handle_unconfirmed_native_playback()
            return False

        self._native_loop_force_restore = True
        repeat_enabled = await self._async_call_media(
            SERVICE_REPEAT_SET, {ATTR_MEDIA_REPEAT: RepeatMode.ALL}
        ) and await self._async_wait_for_media_repeat(RepeatMode.ALL)
        if not repeat_enabled:
            self._last_error = "native_repeat_not_confirmed"
            await self._async_stop_playback()
            await self._async_handle_playback_recovery_failure(
                "Sonos repeat-all could not be confirmed"
            )
            return False
        self._native_loop_force_restore = False
        return True

    async def _async_native_queue_can_be_replaced(self) -> bool:
        """Recheck the speaker immediately before destructive queue setup."""
        media_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        if media_state is None:
            return False
        if media_state.state in {
            MediaPlayerState.IDLE,
            MediaPlayerState.OFF,
            MediaPlayerState.PAUSED,
        }:
            return True
        if media_state.state in {
            MediaPlayerState.PLAYING,
            MediaPlayerState.BUFFERING,
        }:
            if self._owns_playback:
                await self._async_playback_is_owned_now(notify_interruption=False)
            else:
                self._relinquish_playback()
                await self._async_finalize_playback_interruption(notify=False)
        return False

    async def _async_wait_for_entity_state(
        self, entity_id: str, expected_state: str
    ) -> bool:
        """Wait briefly for a subscription-backed entity to confirm a command."""
        return await self._async_wait_for_state(
            entity_id,
            lambda state: state is not None and state.state == expected_state,
        )

    async def _async_wait_for_state(
        self,
        entity_id: str,
        predicate: Callable[[State | None], bool],
    ) -> bool:
        """Wait for one entity predicate without depending on polling cadence."""
        if predicate(self.hass.states.get(entity_id)):
            return True
        changed = asyncio.Event()

        @callback
        def _state_changed(event: Event[EventStateChangedData]) -> None:
            if predicate(event.data.get("new_state")):
                changed.set()

        unsubscribe = async_track_state_change_event(
            self.hass, [entity_id], _state_changed
        )
        try:
            if predicate(self.hass.states.get(entity_id)):
                return True
            async with asyncio.timeout(NATIVE_LOOP_CONFIRMATION_SECONDS):
                await changed.wait()
        except TimeoutError:
            return False
        finally:
            unsubscribe()
        return True

    async def _async_refresh_entity(
        self, entity_id: str, *, expected_state: str | None = None
    ) -> bool:
        """Force-refresh a cached entity and optionally confirm its new state."""
        refreshed = await self._async_call_entity(
            HOMEASSISTANT_DOMAIN,
            SERVICE_UPDATE_ENTITY,
            entity_id,
            {},
            critical=False,
        )
        if not refreshed:
            return False
        if expected_state is None:
            return True
        return await self._async_wait_for_entity_state(entity_id, expected_state)

    async def _async_wait_for_media_repeat(self, expected_repeat: str) -> bool:
        """Wait briefly for Sonos to publish the requested repeat mode."""
        return self.media_player is not None and await self._async_wait_for_state(
            self.media_player,
            lambda state: (
                state is not None
                and state.attributes.get(ATTR_MEDIA_REPEAT) == expected_repeat
            ),
        )

    async def _async_wait_for_media_inactive(self) -> bool:
        """Wait for Sonos to confirm that a stop or pause command settled."""
        return self.media_player is not None and await self._async_wait_for_state(
            self.media_player,
            lambda state: (
                state is not None
                and state.state
                in {
                    MediaPlayerState.IDLE,
                    MediaPlayerState.OFF,
                    MediaPlayerState.PAUSED,
                }
            ),
        )

    async def _async_wait_for_owned_playback(self) -> bool:
        """Wait for the queued item to become identifiable before changing repeat."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + NATIVE_LOOP_CONFIRMATION_SECONDS
        while self._owns_playback:
            media_state = (
                self.hass.states.get(self.media_player)
                if self.media_player is not None
                else None
            )
            if media_state is not None and media_state.state in {
                MediaPlayerState.PLAYING,
                MediaPlayerState.BUFFERING,
            }:
                if await self._async_playback_is_owned_now(notify_interruption=False):
                    return True
                if not self._owns_playback:
                    return False
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(NATIVE_LOOP_CONFIRMATION_POLL_SECONDS, remaining))
        return False

    async def _async_handle_unconfirmed_native_playback(self) -> None:
        """Fail safe without touching active media that could belong to a parent."""
        media_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        if media_state is not None and media_state.state in {
            MediaPlayerState.IDLE,
            MediaPlayerState.OFF,
            MediaPlayerState.PAUSED,
        }:
            self._clear_playback_ownership()
            await self._async_cleanup_native_loop(clear_queue=True, end_session=True)
        elif self._owns_playback:
            pending_context_id = self._pending_play_context_id
            if pending_context_id is not None:
                self._failed_native_loop_context_ids.add(pending_context_id)
                self._track_failed_play_context(
                    pending_context_id, self._configured_media_content_id()
                )
            self._relinquish_playback()
            await self._async_finalize_playback_interruption(notify=False)
        await self._async_handle_playback_recovery_failure(
            "Sonos queued playback could not be confirmed"
        )

    async def _async_start_configured_media(
        self,
        media: dict[str, Any],
        extra_data: dict[str, Any] | None = None,
        *,
        native_loop: bool = False,
    ) -> bool:
        """Start configured media while retaining strict playback ownership."""
        media_player = self.media_player
        if media_player is None:
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
                ATTR_MEDIA_CONTENT_TYPE: (
                    MediaType.MUSIC
                    if self._selected_media_player_is_sonos()
                    else media[ATTR_MEDIA_CONTENT_TYPE]
                ),
                **(extra_data or {}),
            },
            context=play_context,
        ):
            if native_loop:
                self._failed_native_loop_context_ids.add(play_context.id)
            self._track_failed_play_context(
                play_context.id, media[ATTR_MEDIA_CONTENT_ID]
            )
            self._awaiting_playback_confirmation = False
            self._pending_play_context_id = None
            media_state = self.hass.states.get(media_player)
            current_content_id = (
                media_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
                if media_state is not None
                else None
            )
            if (
                media_state is not None
                and self._state_has_play_context(media_state, play_context.id)
                and media_state.state
                in {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
                and self._media_content_id_matches_configured(current_content_id)
            ):
                self._clear_failed_play_context(play_context.id)
                self._owns_playback = True
                self._owned_play_context_id = play_context.id
                self._owned_media_content_id = current_content_id
                await self._async_stop_playback()
            elif (
                native_loop
                and media_state is not None
                and media_state.state
                in {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
            ):
                # Another source won the race after queue setup. Do not clear its
                # queue or alter the repeat/crossfade settings it now owns.
                self._relinquish_playback()
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
            media_state is not None
            and media_state.state
            in {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
            and self._state_has_play_context(media_state, play_context.id)
            and self._media_content_id_matches_configured(current_content_id)
        ):
            self._adopt_owned_media_content_id(current_content_id)
        return True

    def _sonos_crossfade_entity_id(self) -> str | None:
        """Return the crossfade switch paired with a selected Sonos player."""
        if self.media_player is None:
            return None
        registry = er.async_get(self.hass)
        media_entry = registry.async_get(self.media_player)
        if media_entry is None or media_entry.platform != SONOS_PLATFORM:
            return None
        return registry.async_get_entity_id(
            Platform.SWITCH,
            SONOS_PLATFORM,
            f"{media_entry.unique_id}{SONOS_CROSSFADE_UNIQUE_ID_SUFFIX}",
        )

    def _selected_media_player_is_sonos(self) -> bool:
        """Return whether the selected player belongs to the Sonos platform."""
        if self.media_player is None:
            return False
        media_entry = er.async_get(self.hass).async_get(self.media_player)
        return media_entry is not None and media_entry.platform == SONOS_PLATFORM

    def _native_crossfade_loop_supported(self, crossfade_entity_id: str) -> bool:
        """Return whether the selected Sonos exposes every proven loop control."""
        media_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        crossfade_state = self.hass.states.get(crossfade_entity_id)
        if (
            media_state is None
            or crossfade_state is None
            or crossfade_state.state not in {STATE_OFF, STATE_ON}
            or media_state.attributes.get(ATTR_MEDIA_REPEAT)
            not in {mode.value for mode in RepeatMode}
            or self._media_features() & _NATIVE_LOOP_MEDIA_PLAYER_FEATURES
            != _NATIVE_LOOP_MEDIA_PLAYER_FEATURES
        ):
            return False
        return all(
            self.hass.services.has_service(domain, service)
            for domain, service in (
                (MEDIA_PLAYER_DOMAIN, SERVICE_CLEAR_PLAYLIST),
                (MEDIA_PLAYER_DOMAIN, SERVICE_REPEAT_SET),
                (Platform.SWITCH, SERVICE_TURN_ON),
                (Platform.SWITCH, SERVICE_TURN_OFF),
            )
        )

    def _capture_native_loop_settings(
        self, crossfade_entity_id: str
    ) -> _NativeLoopSettings | None:
        """Capture only settings that Nursery Soother will temporarily force."""
        media_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        crossfade_state = self.hass.states.get(crossfade_entity_id)
        if media_state is None or crossfade_state is None:
            return None
        previous_repeat = media_state.attributes.get(ATTR_MEDIA_REPEAT)
        if not isinstance(previous_repeat, str) or crossfade_state.state not in {
            STATE_OFF,
            STATE_ON,
        }:
            return None
        return _NativeLoopSettings(
            crossfade_entity_id=crossfade_entity_id,
            previous_repeat=previous_repeat,
            previous_crossfade=crossfade_state.state,
            group_members=self._media_group_members(media_state),
        )

    def _media_group_members(self, media_state: State) -> tuple[str, ...]:
        """Return stable group topology for conservative setting restoration."""
        members = media_state.attributes.get(ATTR_GROUP_MEMBERS)
        if isinstance(members, list) and all(
            isinstance(member, str) for member in members
        ):
            return tuple(members)
        return (self.media_player,) if self.media_player is not None else ()

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

    async def _async_playback_is_owned_now(  # noqa: PLR0911
        self, *, notify_interruption: bool = True
    ) -> bool:
        """Reconcile live speaker state immediately before a volume effect."""
        if not self._owns_playback or self.media_player is None:
            return False
        media_state = self.hass.states.get(self.media_player)
        if media_state is None:
            return False
        if media_state.state in {
            MediaPlayerState.IDLE,
            MediaPlayerState.OFF,
            MediaPlayerState.PAUSED,
        }:
            self._clear_playback_ownership()
            return False
        if media_state.state not in {
            MediaPlayerState.PLAYING,
            MediaPlayerState.BUFFERING,
        }:
            return False
        current_content_id = media_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
        if self._awaiting_playback_confirmation:
            if current_content_id is None:
                return False
            still_owned = self._pending_playback_is_owned(
                media_state, current_content_id
            )
        elif self._owned_media_content_id is not None:
            still_owned = self._identified_media_state_is_owned(
                media_state, current_content_id
            )
            if still_owned and isinstance(current_content_id, str):
                self._owned_media_content_id = current_content_id
        else:
            still_owned = current_content_id is None and (
                self._state_has_play_context(media_state, self._owned_play_context_id)
            )
        if still_owned:
            return True

        self._relinquish_playback()
        await self._async_finalize_playback_interruption(notify=notify_interruption)
        return False

    async def _async_compensate_failed_play(
        self, event: Event[EventStateChangedData]
    ) -> bool:
        """Stop only a late playback event proven to belong to a failed call."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return False
        failed_context_id = next(
            (
                context_id
                for context_id in self._failed_play_context_ids
                if self._state_has_play_context(new_state, context_id)
            ),
            None,
        )
        if failed_context_id is None:
            return False
        live_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        current_content_id = new_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
        failed_media_content_id = self._failed_play_media_content_ids.get(
            failed_context_id
        )
        if (
            new_state is live_state
            and new_state.state
            in {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
            and self._media_content_ids_match(
                current_content_id, failed_media_content_id
            )
        ):
            failed_native_loop = failed_context_id in (
                self._failed_native_loop_context_ids
            )
            self._clear_failed_play_context(failed_context_id)
            self._owns_playback = True
            self._owned_play_context_id = failed_context_id
            self._owned_media_content_id = current_content_id
            self._native_loop_active = failed_native_loop
            if not await self._async_stop_playback():
                self._transition(
                    SootherState.ATTENTION_REQUIRED,
                    Recommendation.CHECK_DEVICES,
                    "late failed playback could not be stopped",
                )
                await self._async_notify_dependency_problem()
        return True

    def _track_failed_play_context(
        self, context_id: str, media_content_id: object
    ) -> None:
        """Monitor an ambiguous failed play for a bounded compensation window."""
        if context_id in self._failed_play_context_ids:
            return
        self._failed_play_context_ids.add(context_id)
        if isinstance(media_content_id, str):
            self._failed_play_media_content_ids[context_id] = media_content_id
        cancel_callback: CALLBACK_TYPE | None = None

        async def _expired(now: datetime) -> None:
            del now
            async with self._lock:
                if self._failed_play_expiries.get(context_id) is not cancel_callback:
                    return
                self._failed_play_expiries.pop(context_id, None)
                self._failed_play_context_ids.discard(context_id)
                self._failed_play_media_content_ids.pop(context_id, None)
                self._failed_native_loop_context_ids.discard(context_id)
                self._emit_update()

        cancel_callback = async_call_later(
            self.hass, FAILED_PLAY_COMPENSATION_SECONDS, _expired
        )
        self._failed_play_expiries[context_id] = cancel_callback

    def _clear_failed_play_context(self, context_id: str) -> None:
        """Finish monitoring one failed play context."""
        self._failed_play_context_ids.discard(context_id)
        self._failed_play_media_content_ids.pop(context_id, None)
        self._failed_native_loop_context_ids.discard(context_id)
        if cancel_callback := self._failed_play_expiries.pop(context_id, None):
            cancel_callback()

    async def _async_finalize_playback_interruption(
        self, *, notify: bool = True
    ) -> None:
        """Expose a detected external takeover without touching its audio."""
        self._cancel_all_timers()
        self._episode += 1
        self._incident_active = False
        self._episode_confirmed = False
        self.settings.level = SoothingLevel.STANDBY
        self._persist_settings()
        self._transition(
            SootherState.ATTENTION_REQUIRED,
            Recommendation.CHECK_DEVICES,
            "speaker playback replaced externally",
        )
        if notify:
            await self._async_notify_playback_replaced()
        else:
            self._action_generation += 1

    async def _async_stop_playback(  # noqa: C901, PLR0911, PLR0912
        self, *, preserve_native_loop_session: bool = False
    ) -> bool:
        """Stop only owned playback and clean up its temporary Sonos loop."""
        native_loop_present = (
            self._native_loop_active or self._native_loop_settings is not None
        )
        if not self._owns_playback:
            if native_loop_present and not preserve_native_loop_session:
                media_state = (
                    self.hass.states.get(self.media_player)
                    if self.media_player is not None
                    else None
                )
                return await self._async_cleanup_native_loop(
                    clear_queue=(
                        media_state is not None
                        and media_state.state
                        in {
                            MediaPlayerState.IDLE,
                            MediaPlayerState.OFF,
                            MediaPlayerState.PAUSED,
                        }
                    ),
                    end_session=True,
                )
            return True
        media_player = self.media_player
        if media_player is None or not self._entity_available(media_player):
            return False
        media_state = self.hass.states.get(media_player)
        if media_state is None:
            return False
        if not self._current_playback_should_be_stopped(media_state):
            if self._playback_interrupted:
                return True
            if native_loop_present:
                return await self._async_cleanup_native_loop(
                    clear_queue=True,
                    end_session=not preserve_native_loop_session,
                )
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
            if (
                self._selected_media_player_is_sonos()
                and not await self._async_wait_for_media_inactive()
            ):
                self._last_error = "media_stop_not_confirmed"
                return False
            self._clear_playback_ownership()
            if native_loop_present:
                cleaned = await self._async_cleanup_native_loop(
                    clear_queue=True,
                    end_session=not preserve_native_loop_session,
                )
                if not cleaned and preserve_native_loop_session:
                    await self._async_cleanup_native_loop(
                        clear_queue=False, end_session=True
                    )
                return cleaned
        return stopped

    async def _async_cleanup_native_loop(
        self, *, clear_queue: bool, end_session: bool
    ) -> bool:
        """Clear an owned Sonos queue and optionally restore prior settings."""
        success = True
        if clear_queue and self._native_loop_active:
            queue_cleared = await self._async_call_media(
                SERVICE_CLEAR_PLAYLIST, {}, critical=False
            )
            success = queue_cleared
            if queue_cleared:
                self._native_loop_active = False

        if end_session and self._native_loop_settings is not None:
            restored = await self._async_restore_native_loop_settings(
                self._native_loop_settings,
                force=self._native_loop_force_restore,
            )
            success = restored and success
            if restored:
                self._native_loop_settings = None
                self._native_loop_force_restore = False
        if end_session and success:
            self._native_loop_active = False
            self._native_loop_force_restore = False
        return success

    async def _async_restore_native_loop_settings(
        self, settings: _NativeLoopSettings, *, force: bool = False
    ) -> bool:
        """Restore settings only while the same Sonos group still has our values."""
        if not await self._async_refresh_entity(settings.crossfade_entity_id):
            return False
        media_state = (
            self.hass.states.get(self.media_player)
            if self.media_player is not None
            else None
        )
        crossfade_state = self.hass.states.get(settings.crossfade_entity_id)
        if media_state is None or crossfade_state is None:
            return False
        if self._media_group_members(media_state) != settings.group_members:
            _LOGGER.info(
                "Nursery Sonos group changed; leaving repeat and crossfade untouched"
            )
            return True

        success = True
        if (
            media_state.attributes.get(ATTR_MEDIA_REPEAT) == RepeatMode.ALL or force
        ) and settings.previous_repeat != RepeatMode.ALL:
            success = await self._async_call_media(
                SERVICE_REPEAT_SET,
                {ATTR_MEDIA_REPEAT: settings.previous_repeat},
                critical=False,
            ) and await self._async_wait_for_media_repeat(settings.previous_repeat)
        if (
            crossfade_state.state == STATE_ON or force
        ) and settings.previous_crossfade == STATE_OFF:
            crossfade_restored = await self._async_call_entity(
                Platform.SWITCH,
                SERVICE_TURN_OFF,
                settings.crossfade_entity_id,
                {},
                critical=False,
            ) and await self._async_refresh_entity(
                settings.crossfade_entity_id, expected_state=STATE_OFF
            )
            success = crossfade_restored and success
        return success

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
            should_stop = self._identified_media_state_is_owned(
                media_state, current_content_id
            )
            if should_stop and isinstance(current_content_id, str):
                self._owned_media_content_id = current_content_id
            if not should_stop:
                self._relinquish_playback()
            return should_stop
        if self._awaiting_playback_confirmation:
            should_stop = self._pending_playback_is_owned(
                media_state, current_content_id
            )
            if not should_stop:
                if self._pending_play_context_id is not None:
                    self._track_failed_play_context(
                        self._pending_play_context_id,
                        self._configured_media_content_id(),
                    )
                self._clear_playback_ownership()
            return should_stop
        should_stop = current_content_id is None and self._state_has_play_context(
            media_state, self._owned_play_context_id
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
        ownership_changed = not self._identified_media_state_is_owned(
            new_state, new_content_id
        )
        replacement_state = new_state.state in {
            MediaPlayerState.PLAYING,
            MediaPlayerState.BUFFERING,
        } or (
            isinstance(new_content_id, str)
            and new_state.state
            in {
                MediaPlayerState.IDLE,
                MediaPlayerState.OFF,
                MediaPlayerState.PAUSED,
            }
        )
        replaced = ownership_changed and replacement_state
        if not replaced and isinstance(new_content_id, str):
            self._owned_media_content_id = new_content_id
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
        if new_state is not None:
            new_content_id = new_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
            if self._media_content_id_matches_configured(new_content_id) and (
                new_state.context.user_id is None
                or self._state_has_play_context(new_state, self._owned_play_context_id)
            ):
                self._adopt_owned_media_content_id(new_content_id)
                return False
        replaced = (
            new_state is not None
            and new_state.state
            in {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
            and not self._state_has_play_context(new_state, self._owned_play_context_id)
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
        new_content_id = new_state.attributes.get(ATTR_MEDIA_CONTENT_ID)
        if isinstance(new_content_id, str):
            if self._pending_playback_is_owned(new_state, new_content_id):
                return False
            self._relinquish_playback()
            return True
        # Players can publish several no-ID states before exposing the resolved
        # local-media URL. They remain unverified and cannot authorize effects.
        return False

    def _pending_playback_is_owned(
        self, media_state: State, media_content_id: object
    ) -> bool:
        """Verify pending playback and adopt its late-arriving usable ID."""
        if not self._media_content_id_matches_configured(media_content_id):
            return False
        if media_state.context.user_id is not None and not self._state_has_play_context(
            media_state, self._pending_play_context_id
        ):
            return False
        self._adopt_owned_media_content_id(media_content_id)
        return True

    @staticmethod
    def _state_has_play_context(media_state: State, context_id: str | None) -> bool:
        """Return whether state context is the play call or its direct child."""
        return context_id is not None and context_id in (
            media_state.context.id,
            media_state.context.parent_id,
        )

    def _configured_media_content_id(self) -> str | None:
        """Return the source identifier for the selected active level."""
        media = self._media_for_level(self.level)
        if media is None:
            return None
        content_id = media.get(ATTR_MEDIA_CONTENT_ID)
        return content_id if isinstance(content_id, str) else None

    def _media_for_level(self, level: SoothingLevel) -> dict[str, Any] | None:
        """Return the configured media mapping for one active level."""
        if level is SoothingLevel.STANDBY:
            return None
        return self.sounds.get(level)

    def _media_content_id_matches_configured(
        self, media_content_id: object
    ) -> TypeGuard[str]:
        """Return whether an observed ID is the configured local media."""
        return self._media_content_ids_match(
            media_content_id, self._configured_media_content_id()
        )

    def _identified_media_state_is_owned(
        self, media_state: State, media_content_id: object
    ) -> bool:
        """Match owned media unless a user explicitly supplied a fresh raw ID."""
        if media_state.context.user_id is not None and not self._state_has_play_context(
            media_state, self._owned_play_context_id
        ):
            return False
        owned_content_id = self._owned_media_content_id
        return self._media_content_ids_match(media_content_id, owned_content_id)

    def _media_content_ids_match(self, first: object, second: object) -> bool:
        """Compare raw IDs or stable identities for HA-hosted local media."""
        if not isinstance(first, str) or not isinstance(second, str):
            return False
        if first == second:
            return True
        first_identity = self._local_media_identity(first)
        second_identity = self._local_media_identity(second)
        return first_identity is not None and first_identity == second_identity

    def _local_media_identity(
        self, content_id: str
    ) -> tuple[str, tuple[tuple[str, str], ...], str] | None:
        """Return path identity while excluding only HA's volatile authSig."""
        try:
            parsed = urlsplit(content_id)
        except ValueError:
            return None

        path = unquote(parsed.path)
        if parsed.scheme == "media-source":
            if parsed.netloc != "media_source" or not path.startswith("/"):
                return None
            relative_path = path.removeprefix("/")
            query = tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        elif parsed.scheme in {"", "http", "https"}:
            if (
                not path.startswith(LOCAL_MEDIA_URL_PREFIX)
                or (not parsed.scheme and parsed.netloc)
                or (parsed.scheme and not self._is_home_assistant_url(content_id))
            ):
                return None
            relative_path = path.removeprefix(LOCAL_MEDIA_URL_PREFIX)
            query = tuple(
                sorted(
                    (key, value)
                    for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                    if key != AUTH_SIGNATURE_QUERY_PARAMETER
                )
            )
        else:
            return None
        if not relative_path:
            return None
        return relative_path, query, unquote(parsed.fragment)

    def _is_home_assistant_url(self, content_id: str) -> bool:
        """Safely identify absolute URLs served by this Home Assistant."""
        try:
            return is_hass_url(self.hass, content_id)
        except TypeError, ValueError:
            return False

    def _adopt_owned_media_content_id(self, media_content_id: str) -> None:
        """Finish pending confirmation with a usable stable media identity."""
        self._owned_media_content_id = media_content_id
        self._awaiting_playback_confirmation = False
        self._pending_play_context_id = None

    def _relinquish_playback(self) -> None:
        """Forget playback once another source has definitely taken over."""
        self._clear_playback_ownership()
        # Never alter repeat, crossfade, or queue state after another source takes
        # ownership. A parent takeover is more important than restoring our snapshot.
        self._native_loop_active = False
        self._native_loop_settings = None
        self._native_loop_force_restore = False
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
        return await self._async_call_entity(
            MEDIA_PLAYER_DOMAIN,
            service,
            self.media_player,
            data,
            critical=critical,
            context=context,
        )

    async def _async_call_entity(  # noqa: PLR0913
        self,
        domain: str,
        service: str,
        entity_id: str,
        data: dict[str, Any],
        *,
        critical: bool = True,
        context: Context | None = None,
    ) -> bool:
        """Call one output entity and convert integration failures to safe state."""
        call_context = context or Context()
        try:
            async with asyncio.timeout(SERVICE_CALL_TIMEOUT):
                await self.hass.services.async_call(
                    domain,
                    service,
                    {ATTR_ENTITY_ID: entity_id, **data},
                    blocking=True,
                    context=call_context,
                )
        # Third-party media-player handlers are an isolation boundary and may
        # raise exceptions outside Home Assistant's documented hierarchy.
        except Exception as err:  # noqa: BLE001
            self._last_error = type(err).__name__
            _LOGGER.warning(
                "Nursery output %s.%s action failed (%s)",
                domain,
                service,
                type(err).__name__,
            )
            if not critical:
                return False
            self._cancel_all_timers()
            self._incident_active = False
            self._episode_confirmed = False
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

    def _media_player_is_active(self) -> bool:
        """Return whether startup would overwrite an already-active speaker."""
        if self.media_player is None:
            return False
        state = self.hass.states.get(self.media_player)
        return state is not None and state.state in {
            MediaPlayerState.PLAYING,
            MediaPlayerState.BUFFERING,
        }

    def _physical_cry_is_on(self) -> bool:
        """Return whether the configured cry binary sensor is explicitly on."""
        if self.cry_sensor is None:
            return False
        state: State | None = self.hass.states.get(self.cry_sensor)
        return state is not None and state.state == STATE_ON

    def _string_data(self, key: str) -> str | None:
        """Snapshot one stable string config value for this runtime."""
        value = self.entry.data.get(key)
        return value if isinstance(value, str) else None

    def _trigger_configs(self, key: str) -> list[dict[str, Any]]:
        """Snapshot one validated trigger sequence for this runtime."""
        value = self.entry.data.get(key)
        if not isinstance(value, list):
            return []
        return [dict(trigger) for trigger in value if isinstance(trigger, dict)]

    def _validate_controllable(self) -> None:
        """Reject parent commands that cannot be applied safely."""
        self._ensure_started()
        if not self.enabled:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="standby"
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
        self._action_generation += 1
        self._cancel_all_timers()
        self._incident_active = False
        self._episode_confirmed = False
        self._episode_started_at = None
        self._confirmed_at = None
        self._last_cry_activity_at = None
        self._last_level_change_at = None
        self._settling_deferred_by_lock = False
        self._provisional_level_1 = False
        self._initial_level_1_applied = False
        self._stage_simulated_events = 0
        self._evidence.reset(dt_util.utcnow())

    def _cancel_all_timers(self) -> None:
        """Cancel all response timers."""
        self._cancel_timer("evidence")
        self._cancel_timer("provisional")
        self._provisional_level_1 = False
        self._initial_level_1_applied = False
        self._cancel_timer("cry_gap")
        self._cancel_timer("settling")
        self._cancel_timer("attention")

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
