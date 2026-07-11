# Functional MVP architecture

## Purpose and product boundary

Nursery Soother is a Home Assistant **service** integration that coordinates
entities already owned by other integrations. It does not implement camera,
speaker, mobile-app, HomeKit, or Siri connectivity.

The functional Suggested MVP consumes:

- one `binary_sensor` whose on state means crying;
- one `camera` for parent-facing context;
- one `media_player` for white-noise playback and volume control;
- one audio item selected from Home Assistant's Media browser;
- one or more `notify.mobile_app_*` actions for parent communication.

The implementation remains device-agnostic. Standard Home Assistant entity
states, supported features, actions, selectors, and events are the integration
boundary. No Reolink, Sonos, Frigate, or Apple SDK belongs in the MVP.

## Locked MVP scope

The MVP implements Suggestions mode with one temporary, parent-authorized
boost. It is intentionally smaller than the complete product vision:

- disabled by default, including migrated foundation entries;
- enabling sets baseline volume and starts configured white noise;
- a continuous cry must pass debounce before parents are notified;
- the integration never increases volume automatically;
- an explicit Boost command sets one configured target, capped by Maximum;
- sustained crying transitions to Attention required without another boost;
- Acknowledge stops escalation but preserves the current volume;
- uninterrupted quiet returns to baseline after the settling period;
- Baseline lowers immediately while remaining enabled;
- Stop disables the controller and stops its playback, using pause only when
  the player does not support stop;
- dependency loss fails safe and produces an alert where possible;
- restart recovery always begins a fresh episode at baseline and never replays
  a prior boost.

Assisted mode, Boost 2, schedules, analytics, multiple detection inputs, a
custom dashboard card, and device-specific behavior are deferred. They must not
be represented by placeholder platforms or dormant configuration in this
release.

## Component layout

```text
custom_components/nursery_soother/
├── __init__.py          # config-entry lifecycle and platform forwarding
├── config_flow.py       # setup, reconfigure, and options flows
├── const.py             # shared keys, defaults, platforms, and action IDs
├── models.py            # typed settings, state, and recommendation models
├── controller.py        # policy, timers, listeners, and guarded side effects
├── entity.py            # shared controller-backed entity behavior
├── sensor.py            # state and recommendation sensors
├── binary_sensor.py     # attention-required indicator
├── switch.py            # enabled control
├── number.py            # baseline, boost, and maximum volume controls
├── button.py            # boost, baseline, acknowledge, and stop commands
├── diagnostics.py       # redacted support data
├── manifest.json        # Home Assistant and HACS metadata
└── translations/
    └── en.json          # flow, entity, state, and error translations
```

The standard entity actions are sufficient for dashboards, automations,
HomeKit Bridge, and Apple Shortcuts. The MVP therefore does not require custom
service actions or `services.yaml`.

## Config-entry lifecycle

One config entry represents one nursery. Multiple independent entries are
supported for homes with more than one nursery, but a cry sensor, camera, or
speaker cannot be shared between entries.

During setup, the integration:

1. validates stored data and safe option relationships;
2. constructs one controller;
3. stores it in typed `ConfigEntry.runtime_data`;
4. registers state and mobile-notification event listeners;
5. forwards setup to every implemented entity platform;
6. starts in Disabled or performs safe baseline recovery if the persisted
   enabled option is true.

The design is event-driven, not polled. The controller uses
`async_track_state_change_event` for selected entity changes, the Home
Assistant event bus for Companion app action responses, and cancellable time
helpers for debounce, escalation, settling, and cooldown.

Every listener and timer cancellation callback is owned by the config entry or
controller and is invoked during unload. Unload cancels future work, stops only
playback that this controller started, and removes platform entities cleanly.
This prevents a manual reload, reconfigure, or removal from orphaning audio;
setup of an enabled entry then performs a fresh baseline recovery. It never
replays a boost during Home Assistant shutdown or startup.

## Configuration ownership

Config-entry `data` contains stable construction dependencies:

- cry sensor entity ID;
- camera entity ID;
- media-player entity ID;
- white-noise media content ID and type;
- configured mobile notification action names.

Config-entry `options` contains parent-adjustable policy values:

- enabled state;
- baseline, boost, and maximum volume percentages;
- debounce, escalation, settling, and cooldown durations.

Runtime data belongs only to the controller:

- current policy state and recommendation;
- current dependency availability;
- active episode identity and acknowledgement;
- timer handles and generation counters;
- active notification tag and accepted action IDs;
- last commanded integration-owned playback and volume state.

The setup flow collects required dependencies. Reconfigure replaces those
dependencies, and the options flow changes safe policy values. Both preserve
the existing enabled choice; neither changes an off entry to on. Reloading an
already enabled entry follows baseline restart recovery. Code must use
`hass.config_entries.async_update_entry`; config-entry data and options are
never mutated in place.

## Volume representation and validation

Parents configure whole percentages from 0 through 100. Media-player actions
receive the corresponding normalized value from 0.0 through 1.0.

Both flow validation and the controller enforce:

```text
0 <= baseline <= boost <= maximum <= 100
```

The controller applies `min(boost, maximum)` before every boost command even
when stored data has bypassed UI validation. A malformed or ambiguous setting
must fail closed rather than issue an unsafe command.

## Policy state machine

The externally visible policy states are:

| State | Meaning |
| --- | --- |
| `disabled` | No response loop is active and integration-owned playback is stopped |
| `baseline` | White noise is active at baseline and the controller is monitoring |
| `cry_pending` | Cry is active or a parent response is pending |
| `boost` | A parent explicitly applied the single temporary boost |
| `attention_required` | Cry persisted or a dependency/playback conflict requires a parent |
| `settling` | Cry is off and uninterrupted quiet is being measured |

The controller also exposes a recommendation sensor. Recommendations explain
the next safe human action or a dependency problem; they do not themselves
perform media-player actions.

### Enable and disable

Turning on the switch is explicit authorization to set the configured baseline
volume and start the selected media. Playback must not start merely because an
entry was installed, migrated, reconfigured, or reloaded while disabled.

Turning off the switch and pressing Stop share the same policy command: cancel
the episode and timers, clear actionable notification state, stop
integration-owned playback, and enter Disabled.

### Cry debounce and suggestion

An on transition from the cry sensor starts a fresh debounce timer. If the
sensor turns off, becomes unknown, or becomes unavailable before it expires,
the timer is canceled and no suggestion is sent.

When debounce expires and all required dependencies remain usable, the
controller enters Cry pending, creates episode-scoped action IDs, and sends the
same actionable notification to every configured parent. It does not change
speaker volume.

### Explicit boost

Boost is accepted only while the controller is enabled and its dependencies are
usable. It sets one capped boost target and enters Boost. A direct dashboard or
Siri boost can begin a temporary episode even when the cry sensor is currently
off; settling then returns it to baseline. The controller rejects a duplicate
or cooldown-limited boost, never schedules a second stage, and never converts a
timeout into a volume increase.

All command entry points—button entity, dashboard, automation, Siri Shortcut,
or notification response—call the same guarded controller method.

### Attention and acknowledgement

After a suggestion, uninterrupted crying starts or continues the escalation
timer. Expiration enters Attention required and notifies parents; it produces
no volume change.

Acknowledge marks the current episode parent-owned, cancels further escalation,
clears or replaces the shared actionable notification, and exposes the
acknowledged recommendation. It does not lower or raise the speaker. Baseline
and Stop remain separate, explicit controls.

### Settling and cooldown

An off transition during an active episode enters Settling. The current volume
is preserved while quiet is measured. A new cry before the settling timer
expires cancels that timer and resumes the same episode without an automatic
increase.

Uninterrupted quiet for the complete settling period sets baseline volume,
ends the episode, and invalidates its notification actions. After an automatic
baseline return, Boost cooldown is measured from the last accepted boost and
rejects another boost until the configured interval has elapsed. Its temporary
recommendation clears when the interval expires. A successful parent-selected
Baseline, Stop, or fresh Enable resets cooldown because those actions establish
a new explicit control boundary.

The diagnostic Simulated cry switch contributes a virtual cry input alongside
the configured sensor. Turning it on and off uses the same debounce, settling,
notification, and escalation state machine as physical sensor edges. It cannot
be enabled while the controller is disabled or unsafe, and Stop clears it.

## Timer and concurrency rules

Home Assistant callbacks can interleave while awaiting service actions. The
controller serializes state-changing commands and associates every timer and
notification action with an episode or generation token.

A callback must verify its token and current state before acting. This makes
duplicate parent responses idempotent and ensures callbacks from canceled
timers, old notifications, reloads, or restarts cannot affect the current
episode.

State transitions should only claim a successful media effect after the
blocking Home Assistant action succeeds. A failed service call transitions to
a safe recommendation or attention state and is logged without retry loops.

## Dependency and side-effect boundary

The controller calls standard Home Assistant actions instead of integration
implementation methods:

- `media_player.volume_set` for baseline and explicit boost;
- `media_player.play_media` for configured white noise;
- `media_player.media_stop`, or pause as a fallback, for Stop or disable;
- selected `notify.mobile_app_*` actions for actionable notifications.

Before issuing an effect, it checks current entity availability, required media
player features, enabled state, episode validity, playback ownership, and
volume bounds. Camera availability is required for a normal parent-facing
response; loss is reported instead of being treated as permission to escalate.

If another source replaces the owned white noise, the controller relinquishes
the speaker, cancels response timers, enters Attention required, and refuses
later Baseline, Boost, or live number-setting effects. Stop then disables the
policy without stopping the parent's replacement media; explicitly turning the
switch off and on starts a fresh baseline session.

Playback ownership uses the configured Home Assistant local-media identity,
not the media player's transient raw URL. Home Assistant may resolve
`media-source://media_source/...` to a signed `/media/...` URL and refresh its
`authSig` while the same item continues. The controller compares the exact Home
Assistant origin, media path, fragment, and all non-signature query fields,
ignoring only `authSig`. A state without a usable media ID remains unverified
and cannot authorize Boost, Baseline, Stop, or live volume effects. A different
path, origin, non-signature query, or explicit user replay relinquishes
ownership.

When a cry sensor, camera, speaker, or notification target becomes unknown,
unavailable, or missing, the controller:

1. cancels unsafe pending escalation;
2. performs no automatic volume increase;
3. exposes a check-devices recommendation and attention state as appropriate;
4. alerts through every notification surface that still works;
5. waits for an explicit parent action or a fresh, valid state transition.

Notification failures are isolated per parent so one unavailable phone does
not prevent the other from receiving an alert. If no mobile target works, the
state, recommendation, attention entity, and logs remain the local fallback.

## Notification coordination

Companion app actionable notification identifiers are global within a Home
Assistant instance. Nursery Soother prefixes them with its domain and includes
the config entry and current episode identity. Only the active action map is
accepted.

Each notification uses a stable per-entry tag so newer state replaces older
state on a phone. When a parent responds, the controller validates the current
episode, synchronizes controller state, and clears or replaces the tag for
every configured parent. Tapping the notification opens the camera through a
client-side entity URI and does not modify controller state.

Mobile operating systems have different action-count and presentation limits.
The payload can choose the most relevant controls for the current phase; the
native dashboard always exposes the full command set.

## Standard entity contract

All entities share one virtual Nursery Soother device, use stable unique IDs,
set `has_entity_name = True`, and receive updates from the controller rather
than polling.

| Platform | Entities | Behavior |
| --- | --- | --- |
| `switch` | Enabled, Simulated cry | Start/stop the policy or inject a diagnostic cry input |
| `sensor` | State, Recommendation | Read-only enum state from controller memory |
| `binary_sensor` | Attention required | True only when parent attention is currently requested |
| `number` | Baseline, Boost, Maximum volume | Persist validated percentage options |
| `button` | Boost, Baseline, Acknowledge, Stop | Stateless commands routed to controller |

Entity IDs are user-editable registry data and must never be used as internal
identity. Automations, dashboards, HomeKit Bridge, and Shortcuts use standard
`switch.turn_on`, `switch.turn_off`, `number.set_value`, and `button.press`
actions.

## Restart and migration safety

Persistent configuration stores parent intent, not transient episode progress.
The integration never persists an actionable notification token, boost phase,
or timer deadline for restoration.

On restart or reload:

- a disabled entry remains Disabled and issues no speaker action;
- an enabled entry discards the old episode, starts at baseline, and treats any
  current cry as a fresh input requiring a complete debounce;
- an old boost is never replayed;
- an old notification action cannot be accepted.

Migration from the inert foundation adds safe defaults and explicitly stores
`enabled = false`. Missing newly required media or notification configuration
is surfaced for reconfiguration rather than guessed.

## Privacy and diagnostics

Policy evaluation remains inside Home Assistant. The controller does not save
camera images or notification contents and does not make direct network calls
to device vendors. The selected integrations retain responsibility for their
own network behavior.

Diagnostics must redact:

- notification action names and device targets;
- media content IDs, URLs, and filesystem paths;
- camera and snapshot URLs;
- episode IDs, notification action tokens, and tags;
- credentials and webhook identifiers.

Diagnostics may include non-sensitive option values, controller state,
dependency availability categories, timer presence without exact private
identifiers, and recent error categories. Logs describe transitions and error
classes without captured media or notification message bodies.

## Testing strategy

- Policy tests cover safety-critical state/event paths, including duplicate and
  stale callbacks.
- Time-controlled tests cover debounce cancellation, persistent-cry attention,
  settling interruption, baseline return, and cooldown.
- Safety tests assert that Suggestions mode never increases volume without an
  explicit parent action and that every command respects Maximum.
- Side-effect tests assert exact media-player and notification calls, plus
  negative assertions for rejected commands.
- Config-flow tests cover setup, multiple entries, domain validation, media and
  notification selection, invalid volume relationships, reconfigure, options,
  and foundation migration.
- Entity tests cover unique IDs, virtual-device linkage, availability, state
  propagation, and standard actions.
- Lifecycle tests cover setup, reload, unload, listener/timer cleanup, disabled
  startup, enabled baseline recovery, and stale-notification rejection.
- Failure tests cover every unavailable dependency and partial notification
  delivery.
- Diagnostics tests prove every sensitive field is redacted.

The controller should remain deterministic and expose all side effects through
small Home Assistant adapters so exhaustive transition tests stay fast.

## Upstream Home Assistant contracts

The implementation follows the current official documentation for [config
entry lifecycle and platform forwarding](https://developers.home-assistant.io/docs/config_entries_index/),
[`ConfigEntry.runtime_data`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/),
[event and timer helpers](https://developers.home-assistant.io/docs/integration_listen_events/),
[entity naming and updates](https://developers.home-assistant.io/docs/core/entity/),
[local media sources](https://www.home-assistant.io/integrations/media_source/),
and [Companion app actionable
notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications/).
