# Level-based architecture

## Purpose and product boundary

Nursery Soother is a Home Assistant **service** integration that coordinates
entities already owned by other integrations. It does not implement camera,
speaker, mobile-app, HomeKit, Siri, or vendor connectivity.

One config entry consumes:

- one `binary_sensor` whose off-to-on transition means a cry was detected;
- one `camera` for caregiver-facing context;
- one `media_player` for soothing playback and volume control;
- one audio mapping for each active level, selected through the Media browser
  from Home Assistant's Local media source;
- one or more `notify.mobile_app_*` actions for caregiver communication;
- optionally, independently configured standard Home Assistant triggers for a
  physical button's toggle, one-level increase, and one-level decrease
  commands.

Standard Home Assistant entity states, supported features, actions, trigger
selectors, and events remain the integration boundary. Reolink, Sonos, Sonoff,
ZHA, and Zigbee2MQTT are not dependencies. A capability-gated Sonos path uses
the integration's queue, repeat, and crossfade controls, while every other
player retains the generic media-player path. Button triggers are consumed in
the standard form produced by Home Assistant; no button vendor or Zigbee
backend is coupled to controller policy.

## Superseding product contract

The level-based design replaces the development-only Boost, Return to
baseline, Acknowledge, Stop, enabled-switch, and cooldown model. Backward
compatibility with that unpublished model is not required; removing and adding
the integration again is safer than carrying ambiguous transient state.

The locked control surface is:

```text
Standby → Baseline → Level 1 → Level 2 → Level 3 → Level 4
```

- One select represents the exact current output level.
- Standby is off and replaces both Disabled and Stop.
- Standby ignores physical cry events, and the artificial cry-event command is
  a no-op. Automatic operation never leaves it; only an explicit caregiver
  level selection starts a session.
- Baseline and Levels 1–4 each have one configured volume.
- Maximum volume is a separate hard ceiling.
- One switch authorizes automatic **upward** changes only.
- Three independently optional trigger configurations expose toggle, one-level
  increase, and one-level decrease commands. A caregiver may map short press,
  double press, and long press respectively, but gesture names are not part of
  controller policy.
- The button commands are direct caregiver commands and remain available while
  Level lock is on.
- Manual mode produces an exact next-level suggestion and no volume change.
- Quiet downshift applies in both modes, one level per quiet interval, stopping
  at Baseline.
- No command may skip a level automatically, exceed Level 4, exceed Maximum,
  or reuse evidence from a previous automatic increase.
- One bounded unresolved-episode deadline enters Standby and requests
  caregiver attention regardless of mode or current level.
- A dependency or speaker-ownership failure fails safe and never authorizes an
  upward change.

Nursery Soother is not a medical device, baby monitor, or replacement for
direct adult supervision. The attention entity and notifications are
convenience signals; none is a guaranteed alarm path.

## Evidence for an event model

A sanitized read-only query of the configured Reolink cry binary sensor's
available recorder window returned 1,605 complete on/off pulses over 10 days.

The analysis used Home Assistant's authenticated
[`/api/history/period`](https://developers.home-assistant.io/docs/api/rest/)
endpoint for only the configured entity, requested a longer range than the
recorder retained, sorted `last_changed`, discarded non-binary rows, collapsed
consecutive duplicate snapshots, and paired each on transition with its next
off transition. The access token and raw entity attributes were never emitted
or stored with the aggregates.

| Observation | Result |
| --- | ---: |
| Median on-time | 3.29 seconds |
| p90 on-time | 8.12 seconds |
| Pulses at least 15 seconds | 0.69% |
| Median off-gap | about 19 seconds |

The sensor is therefore a state projection of brief detection events, not a
reliable measurement that stays on while crying continues. A 15-second held-on
debounce would reject more than 99% of observed pulses.

The upstream path supports that interpretation:

- Home Assistant documents baby crying as a push-capable Reolink sound
  detection in the official [Reolink
  integration](https://www.home-assistant.io/integrations/reolink/).
- Home Assistant places cry in its Reolink push-binary-sensor group and reads
  `api.ai_detected(channel, "cry")` in [the integration
  source](https://github.com/home-assistant/core/blob/dev/homeassistant/components/reolink/binary_sensor.py).
- `reolink_aio` receives TCP command 33 `AlarmEvent` messages, reads `AItype`,
  and changes its cached boolean according to whether that set contains the
  detection type. See the [upstream TCP event
  parser](https://github.com/starkillerOG/reolink_aio/blob/main/reolink_aio/baichuan/baichuan.py#L691-L742)
  and [`ai_detected`
  getter](https://github.com/starkillerOG/reolink_aio/blob/main/reolink_aio/api.py#L3416-L3448).

Nursery Soother consumes the standard binary sensor but normalizes every
off-to-on transition into a cry event. Falling edges close active-time spans;
they are not interpreted as proof that the child is settled.

## Component layout

```text
custom_components/nursery_soother/
├── __init__.py          # integration-global frontend and config-entry lifecycle
├── config_flow.py       # setup, reconfigure, and options flows
├── const.py             # shared keys, defaults, platforms, and action IDs
├── models.py            # levels, state, recommendation, and settings models
├── controller.py        # evidence policy, timers, and guarded side effects
├── entity.py            # shared controller-backed entity behavior
├── frontend.py          # static card path and extra-module registration
├── frontend/
│   └── nursery-soother-card.js  # optional Lovelace card and visual editor
├── select.py            # exact Standby/Baseline/Level 1–4 control
├── switch.py            # Automatic operation and Level lock controls
├── sensor.py            # policy-state and recommendation sensors
├── binary_sensor.py     # attention-required indicator
├── number.py            # five level volumes and Maximum
├── button.py            # artificial cry-event command
├── diagnostics.py       # redacted support data
├── manifest.json        # Home Assistant and HACS metadata
└── translations/
    └── en.json          # flow, entity, level, state, and error translations
```

Standard entity actions remain the integration contract for dashboards,
automations, HomeKit Bridge, and Apple Shortcuts. The optional frontend card is
a compact client of that contract, not an alternate controller or service API.

## Frontend registration and card boundary

The manifest lists `frontend` as an optional `after_dependency`, preserving
headless operation. When `frontend` is loaded, integration-level `async_setup`
calls `async_register_frontend` once per Home Assistant run. The helper serves
`custom_components/nursery_soother/frontend/nursery-soother-card.js` at
`/nursery_soother/nursery-soother-card.js` with cache headers disabled and adds
a `CARD_MODULE_VERSION` query to the extra JavaScript module URL. The versioned
URL prevents a HACS update from reusing an older browser cache. The card is
therefore available in Lovelace without a manually configured resource. A
headless Home Assistant instance skips this registration. Registration is
global rather than config-entry-owned; unloading an entry does not unregister
the static path or module URL.

The `custom:nursery-soother-card` configuration is an explicit set of entity
registry references:

| Key | Domain | Role |
| --- | --- | --- |
| `camera_entity` | `camera` | Camera image/stream and more-info target |
| `level_entity` | `select` | Exact Standby, Baseline, or Level 1–4 control |
| `automatic_entity` | `switch` | Automatic operation control |
| `lock_entity` | `switch` | Level lock control |
| `state_entity` | `sensor` | Current policy phase |
| `recommendation_entity` | `sensor` | Recommendation and `suggested_level` |
| `attention_entity` | `binary_sensor` | Direct-care attention state |

All seven keys are required. Optional `camera_view` accepts `live` (the
default), `auto`, or `image`. `getConfigForm` exposes the same schema through
the visual editor on the supported Home Assistant 2026.7 baseline.
`getStubConfig` defaults only the camera mode and deliberately leaves every
required entity for the user to select, so multiple nurseries or renamed
registry entries cannot be silently mixed by naming guesses. A later entity
rename therefore requires updating the Lovelace config.

The card reads state from those entities and invokes only standard Home
Assistant select and switch actions. Its six level controls select exact
options with `select.select_option`, Auto and Lock call `switch.turn_on` or
`switch.turn_off`, and a valid recommendation can select its exact
`suggested_level`. `live` and `auto` delegate rendering to a native
picture-entity card and fall back to an authenticated snapshot; `image` uses
that snapshot directly and refreshes it every 10 seconds. Camera taps open Home
Assistant's normal camera more-info dialog. The attention banner's Attend
action opens the same dialog and deliberately does not acknowledge or clear
attention or change the level.
Configuration-only volume numbers and the artificial-event button stay on
native entity surfaces. Native cards remain a complete fallback if the
optional module cannot render.

## Config-entry lifecycle

One schema-v7 config entry represents one nursery. Multiple independent entries
are supported, but a cry sensor, camera, or speaker cannot be shared between
entries.

During setup, the integration:

1. validates stored dependencies and safe option relationships;
2. constructs one controller;
3. stores it in typed `ConfigEntry.runtime_data`;
4. registers cry-state, mobile-notification, and optional button action-trigger
   listeners;
5. forwards setup to every implemented entity platform;
6. remains in Standby or performs safe recovery at the persisted exact level.

The controller is event-driven. It uses
`async_track_state_change_event` for selected entity changes, the Home
Assistant event bus for Companion app action responses, and cancellable time
helpers for confirmation, event gap, level dwell, quiet step-down, and
attention deadlines.

Every listener and cancellation callback is owned by the config entry or
controller and is invoked during unload. Unload cancels future work and stops
only integration-owned playback. No old event buffer, automatic step, timer,
notification action, or button-trigger callback can run after reload or
removal.

## Configuration ownership

Config-entry `data` contains stable construction dependencies:

- cry sensor entity ID;
- camera entity ID;
- media-player entity ID;
- configured soothing media content ID and type for every active level;
- configured mobile notification action names;
- independently optional generic Home Assistant trigger configurations for
  physical toggle, increase, and decrease commands.

The configuration flow stores a complete map for Baseline and Levels 1–4.
Each entry may point to a distinct local-media item, while selecting the same
item more than once remains valid. Policy transitions resolve only the target
level's mapping.

Config-entry `options` contains caregiver-adjustable policy values:

- exact selected level;
- Automatic operation choice;
- Level lock choice;
- Baseline and Level 1–4 volume percentages;
- Maximum volume percentage;
- confirmation debounce;
- evidence-window duration;
- cry-event gap;
- provisional Level 1 timeout;
- automatic level dwell;
- quiet step-down interval;
- attention timeout.

Runtime evidence belongs only to the controller:

- current level, policy state, and recommendation;
- current dependency availability and playback ownership;
- active episode identity and confirmation time;
- recent cry-event timestamps and completed/active on-time spans;
- evidence generation consumed by the last level increase;
- timer handles and generation counters;
- active notification tag and accepted action IDs;
- last integration-commanded media identity, level, and volume.

Setup collects required dependencies. Reconfigure replaces them, and Configure
changes policy values. Reconfigure also adds, replaces, or removes any physical
button action trigger. Code uses
`hass.config_entries.async_update_entry`; config-entry data and options are
never mutated in place.

## Level, volume, and sound representation

The ordered `SoothingLevel` values are:

| Value | Output |
| --- | --- |
| `standby` | No integration-owned playback |
| `baseline` | Baseline sound and volume |
| `level_1` | Level 1 sound and volume |
| `level_2` | Level 2 sound and volume |
| `level_3` | Level 3 sound and volume |
| `level_4` | Level 4 sound and volume |

Caregivers configure whole percentages from 0 through 100. Media-player
actions receive normalized values from 0.0 through 1.0. Both flow validation
and the controller enforce:

```text
0 <= baseline <= level_1 <= level_2 <= level_3
  <= level_4 <= maximum <= 100
```

The controller applies `min(level_volume, maximum)` immediately before every
volume action, even if stored values bypassed UI validation. Standby has no
volume or media target. A malformed or ambiguous setting fails closed rather
than issue a command.

An exact manual level selection is not an increment request. It resolves the
level's sound and volume, validates dependencies and ownership, completes both
media-player actions, and only then publishes the selected level. Automatic
policy and the explicitly configured physical Increase trigger may derive the
next level; the physical Decrease trigger may derive the previous level. Each
derives at most one step.

## Policy phase versus output level

Output level and policy phase are separate dimensions. The level select always
reports the desired exact output. The state sensor explains the controller's
current reasoning:

| State | Meaning |
| --- | --- |
| `standby` | Output is off; no automatic response loop is active |
| `soothing` | An active level is playing and no cry decision is pending |
| `cry_pending` | Candidate event evidence is inside confirmation evaluation |
| `responding` | A confirmed episode is active and a level response or suggestion is being observed |
| `settling` | Quiet is being measured before a one-level downshift |
| `attention_required` | Direct caregiver attention or dependency recovery is requested |

The recommendation sensor exposes one of `none`, `start`, `wait`,
`increase_level`, `observe`, `attend`, `settling`, or `check_devices`.
For `increase_level`, the `suggested_level` attribute contains the exact target;
consumers must not reconstruct it from translated text.

The state sensor's attributes are the public explainability boundary. They
contain a stable `explanation` key, a privacy-safe `evidence` snapshot, every
active policy deadline in `countdowns`, and the earliest deadline duplicated as
`next_countdown` plus `next_countdown_at`. Deadline values are absolute UTC ISO
8601 timestamps, not decrementing second counts. This lets a dashboard render a
smooth local countdown without polling or forcing Home Assistant state writes
once per second. The evidence snapshot includes its `observed_at` timestamp and
whether the cry sensor was active at that instant for the same reason.

Countdowns represent distinct gates and outcomes, so several may coexist. A
confirmation or level-dwell gate becoming eligible does not claim that evidence
is sufficient; the event and active-time thresholds remain authoritative. Timer
cancellation removes its corresponding deadline, and a one-shot controller
refresh at an otherwise inactive gate boundary prevents expired timestamps from
remaining in Home Assistant state.

## Cry evidence algorithm

### Event normalization

For a usable configured binary sensor:

- `off → on` appends one cry-event timestamp and begins an active on-time span;
- `on → off` closes that span and contributes its duration to rolling active
  time;
- repeated identical states do not append events;
- `unknown`, `unavailable`, or missing state is dependency loss, not quiet;
- restart snapshots do not become cry events unless there is a real transition
  after the new controller starts.

Evidence is retained only inside the rolling evidence window and only for the
current episode/generation.

### Evidence defaults

The evidence-based initial defaults are:

| Policy value | Default |
| --- | ---: |
| Automatic confirmation debounce | 8 seconds by default |
| Evidence window | 30 seconds |
| Manual initial event-count threshold | 1 rising edge, immediately |
| Automatic initial event-count threshold | 2 rising edges |
| Automatic initial active-time threshold | 8 cumulative seconds |
| Continuing event-count threshold | 1 fresh rising edge |
| Continuing active-time threshold | 6 fresh cumulative seconds |
| Cry-event gap | 60 seconds |
| Provisional Level 1 timeout | 25 seconds |
| Post-response level dwell | 20 seconds |

In manual mode, the first rising edge qualifies immediately. In automatic
mode, the first event starts a candidate and the configured confirmation
debounce. An automatic candidate may confirm only after that delay—eight
seconds with defaults—and when the current 30-second window contains either at
least two rising edges or at least eight cumulative active seconds. After the
first response, each stage uses the same rolling window but needs one fresh
rising edge or six fresh active seconds. The conditions are an OR. Evidence
falling out of the window cannot qualify later.

When Automatic operation is on and the selected level is Baseline, that first
event also applies Level 1 provisionally. This output change does not mark the
episode confirmed, start the attention deadline, send a notification, or make
the continuing-stage threshold eligible. If initial evidence confirms, the
controller retains Level 1, consumes the initial evidence, and observes the
remainder of the dwell from the actual provisional response time. If initial
evidence does not confirm, Level 1 returns to Baseline after the separate
25-second provisional timeout. The rollback delay is never shorter than the
confirmation debounce.

Every new event refreshes the 60-second event-gap timer. If it expires before
or after confirmation, the cry episode closes and its attention deadline is
canceled. The falling edge of one short pulse does not close an episode.

### Manual response

When Automatic operation is off, the first rising edge immediately:

1. leaves the current level unchanged;
2. computes exactly one next active level when available;
3. exposes `increase_level` and the exact suggested target;
4. sends one shared, tagged evidence summary to all configured caregivers;
5. starts the base attention deadline without waiting for the automatic
   confirmation debounce.

Each qualified evidence decision creates one current tagged notification. Once
the stage resets, fresh evidence that qualifies after the dwell period may
replace it with a newer suggestion during the same episode.

Selecting any exact level is the caregiver's decision and replaces a separate
Acknowledge action. A stale notification cannot change a later episode. At
Level 4 there is no higher suggestion; the recommendation is Observe or Attend
as the deadline approaches.

### Automatic response and fresh evidence

When Automatic operation is on, the first event can provisionally move
Baseline to Level 1 as described above. Confirmation retains that response
instead of advancing again. From any other starting level, confirmation can
advance exactly one active level. Standby ignores cry evidence and cannot be
left by the policy. A caregiver must explicitly select Baseline or another
active level before event monitoring and automatic response begin.

After each automatic increase, the controller:

1. records a new evidence generation boundary at the completed level command;
2. clears or marks consumed every older event and active-time contribution;
3. starts the 20-second level-dwell timer;
4. accepts only post-boundary evidence toward another increase;
5. requires both elapsed dwell and a newly satisfied continuing-stage
   threshold.

This fresh-evidence rule prevents one dense cluster or delayed callback from
walking through several levels. A manual exact-level selection also establishes
a new boundary, so earlier evidence cannot immediately override the parent.
Level 4 has no automatic successor.

### Physical-button commands

Setup and reconfigure may store three independently optional Home Assistant
trigger selector values: Toggle, Increase, and Decrease. A useful button
mapping is short press to Toggle, double press to Increase, and long press to
Decrease, but the integration stores the selected trigger configurations and
does not interpret gesture names. ZHA, Zigbee2MQTT, or another integration may
supply any of the actions without a Sonoff- or vendor-specific path.

The Toggle callback enters Baseline from Standby and enters Standby from any
active level. The Increase callback advances exactly one active level only
when the current level is active; it is a no-op in Standby and at Level 4. The
Decrease callback moves down exactly one active level only when the current
level is active; it is a no-op in Standby and at Baseline. All callbacks enter
the same serialized, guarded controller command path used by other direct
caregiver controls.

These are direct caregiver commands rather than policy decisions. Level lock
does not block them, but dependency validation, playback ownership, exact
level media resolution, and the Maximum-volume ceiling still apply.

### Level lock

Level lock freezes policy-driven output changes at the current exact level.
While locked, cry evidence can still confirm, reset stages, start the attention
deadline, and notify caregivers, but it cannot provisionally or automatically
increase the level. Quiet expiry is remembered without applying a downshift;
unlocking starts a fresh quiet interval before that deferred decrease.

Exact parent selections and configured physical-button commands remain
available while locked and become the new held level. The lock does not
override dependency fail-safe, playback takeover handling, Maximum volume, or
the bounded attention deadline.

### Quiet downshift

When no new cry evidence is present, the controller enters Settling. In both
manual and automatic modes, each uninterrupted 120-second quiet interval moves
down exactly one active level. A new cry event cancels the current quiet timer.

Quiet downshift stops at Baseline. Only an explicit Standby selection or the
attention safety deadline turns playback off.

### Bounded attention deadline

The first manual alert or automatic confirmation starts one 150-second base
attention timer for the episode. It is not restarted by a suggestion, manual
selection, automatic increase, dwell, or quiet downshift. If the event-gap
timer closes the episode first, the attention timer is canceled.

If the base deadline arrives while the physical sensor is off and an event-gap
timer is already pending, that existing gap receives one bounded grace period
to finish. The attention callback is ordered just after the gap deadline. A
fresh event during this grace restarts the event-gap timer but cannot extend
attention again, preserving a finite safety cutoff.

If the episode remains active after the base deadline and any one-time quiet
grace, the controller:

1. enters Standby;
2. stops or pauses only integration-owned playback;
3. invalidates pending level actions;
4. exposes Attention required and an Attend recommendation;
5. notifies every available caregiver surface.

The deadline applies at every level and in both modes. It prevents indefinite
high-volume playback and makes unresolved crying a direct-care boundary.

## Artificial cry event

The configuration-category **Simulate cry event** button contributes one
finite rising-edge sample to the same evidence stream. It is an event, not a
persistent switch or alternate sensor state.

Each press must:

- use the real evidence window, mode-specific initial timing, notifications,
  level policy, cap, and attention rules;
- be distinguishable as simulated in diagnostics and caregiver messages;
- avoid mutating the physical cry entity;
- require the same dependency and ownership safety checks;
- be canceled safely by Standby, unload, or dependency loss.

One press tests the immediate manual path; repeated presses intentionally allow
testing the automatic event-count threshold. In Standby every press is a no-op.
The example dashboard requires confirmation because active-session simulated
events can send real notifications and, with Automatic operation enabled, can
command the speaker.

## Timer and concurrency rules

Home Assistant callbacks can interleave while awaiting service actions. The
controller serializes state-changing commands and associates every timer,
evidence window, and notification action with an episode or generation token.

A callback must verify its token, evidence generation, exact level, and current
state before acting. This ensures:

- duplicate state events do not inflate event count;
- rapid or duplicate physical-button callbacks cannot race, skip a level, or
  apply a stale command;
- an event consumed by one increase cannot authorize another;
- a canceled dwell or quiet callback cannot change a manually selected level;
- an old attention callback cannot stop a new session;
- duplicate phone responses are idempotent;
- callbacks from reloads or old notifications cannot affect the new
  controller.

State transitions claim a media effect only after the awaited Home Assistant
action succeeds. A failed action enters a safe recommendation or attention
state and is logged without an unbounded retry loop.

## Dependency and side-effect boundary

The controller calls standard Home Assistant actions:

- `media_player.volume_set` for an active level's capped volume;
- `media_player.play_media` for that level's mapped sound;
- `media_player.media_stop`, or pause as a fallback, for Standby;
- `media_player.clear_playlist` and `media_player.repeat_set` for a compatible
  Sonos loop;
- `switch.turn_on` or `switch.turn_off` for that Sonos player's crossfade;
- selected `notify.mobile_app_*` actions for caregiver notifications.

Before issuing an effect, it checks entity availability, required media-player
features, selected level, evidence/episode validity where applicable, playback
ownership, and volume bounds. Camera availability is required for a normal
caregiver response; loss is reported rather than treated as permission to
increase.

### Sonos native-loop boundary

The optimized path is selected only when the configured entity is registered
by the Sonos integration, its paired crossfade switch can be identified, and
the player exposes clear-playlist, enqueue, and repeat controls. The controller
captures the initial repeat mode, crossfade state, and Sonos group membership;
enables crossfade when needed; clears the queue; enqueues the selected Local
Media track once with playback; and selects repeat-all. Sonos owns every loop
boundary after that setup. There is no timer or periodic queue repopulation.
Because Sonos crossfade state is subscription-backed and can lag the device,
the controller explicitly refreshes that entity before capture and after each
toggle. Local Media MIME types are normalized to Sonos's `music` media type at
the service boundary. A stop or pause must publish an inactive state before a
track replacement clears ownership and begins new queue setup.

This is a one-item queue, not two duplicate entries. It is also intentionally
destructive to the previous Sonos queue: starting a soothing session replaces
that queue, and Nursery Soother cannot reconstruct it later. A normal Standby,
unload, or owned-playback stop clears the integration-owned queue and restores
the captured repeat and crossfade values only if the same group still exposes
the values Nursery Soother set. It does not overwrite a later parent setting.
If group membership changed, cleanup leaves repeat and crossfade untouched.

An external playback takeover is a stronger ownership boundary. The controller
forgets its native-loop snapshot and makes no stop, clear-queue, repeat, or
crossfade request, even though that means the temporary settings may remain.
The replacement media and its current controls belong to the parent.

An exact active-level selection while the controller visibly reports Standby
is the deliberate exception to that restraint. It is an explicit caregiver
command to take over the speaker. For Sonos, the controller captures the live
repeat, crossfade, and group values, stops or pauses the replacement playback,
waits for the inactive state, and only then performs the normal guarded queue
setup. Setup race checks remain in force after that one authorized stop.

Unexpected idle, off, or paused state while playback is still owned triggers a
fresh setup of the same one-item queue. The Local Media source is resolved
again by Home Assistant, refreshing a signed media URL rather than depending
on the URL from the original start. Local Media signing currently has a 24-hour
lifetime, so the static queue is not a forever-playback guarantee: a session
that crosses URL expiry may stop and incur a gap before idle recovery can
resolve a fresh URL.
The supported target is an overnight session within that URL lifetime. Players
without the full Sonos capability set use direct `play_media`; they receive the
same owned-idle recovery but no queue, repeat, or crossfade mutation.

The active loop watches both the media player and its paired crossfade switch.
If repeat-all or crossfade no longer holds, the controller stops the owned
loop, enters Standby, and reports attention rather than continuing to advertise
seamless playback.

If another source replaces the owned sound, the controller relinquishes the
speaker, moves the visible output level to Standby, cancels response timers,
requests attention, and refuses automatic or live-volume effects. It does not
stop or alter the parent's replacement media. From that visible Standby state,
a later explicit active-level selection directly authorizes a fresh owned
session after validation, including stopping and replacing active Sonos audio;
no redundant Standby selection is required.

Playback ownership uses the configured Home Assistant local-media identity,
not a player's transient raw URL. Home Assistant may resolve
`media-source://media_source/...` to a signed `/media/...` URL and refresh its
`authSig` while the same item continues. The controller treats that local
media-source identifier and its Home Assistant-hosted resolved URL as one
identity. For resolved URLs it requires the configured Home Assistant origin,
path, fragment, and all non-signature query fields while ignoring only
`authSig`. A missing media ID is unverified and cannot authorize level,
volume, or stop effects. A different origin, path, or non-signature query
relinquishes ownership. An explicit user-context replay also relinquishes
ownership, including a replay with the exact same raw media ID.

When a cry sensor, camera, speaker, or notification target becomes unknown,
unavailable, or missing, the controller:

1. cancels unsafe automatic work and evidence timers;
2. performs no upward level change;
3. exposes `check_devices` and attention as appropriate;
4. alerts through every notification surface that still works;
5. waits for an explicit caregiver action or fresh valid transition.

Notification failures are isolated per target. If no phone works, the state,
recommendation, attention entity, and logs remain the local fallback.

## Notification coordination

Companion app actionable notification identifiers are global within a Home
Assistant instance. Nursery Soother prefixes them with its domain and includes
the config entry and episode identity. Only the active action map is accepted.

Manual-mode suggestions include sanitized evidence count, cumulative active
time, current level, exact proposed level, and camera access. Automatic-mode
messages identify the completed exact level change and fresh evidence that
authorized it. Attention and dependency messages prioritize direct care,
camera access, and Standby.

Each notification uses a stable per-entry tag so newer policy state replaces
older state. An accepted exact-level action synchronizes controller state and
clears or replaces the tag for all configured caregivers. There is no
Acknowledge action; the level choice is the shared response.

## Standard entity contract

All entities share one virtual Nursery Soother device, use stable unique IDs,
set `has_entity_name = True`, and receive updates from the controller instead
of polling.

| Platform | Entities | Behavior |
| --- | --- | --- |
| `select` | Level | Exact Standby, Baseline, or Level 1–4 command |
| `switch` | Automatic operation, Level lock | Choose automatic response and freeze policy-driven level changes |
| `sensor` | State, Recommendation | Read-only policy values and safe explanatory attributes |
| `binary_sensor` | Attention required | True only when direct caregiver attention is requested |
| `number` | Baseline, Level 1, Level 2, Level 3, Level 4, Maximum volume | Persist validated percentages and apply safe live updates |
| `button` | Simulate cry event | Append one artificial event through the real policy |

Expected default entity IDs are samples, not internal identity:

```text
select.nursery_soother_level
switch.nursery_soother_automatic_operation
switch.nursery_soother_level_lock
sensor.nursery_soother_state
sensor.nursery_soother_recommendation
binary_sensor.nursery_soother_attention_required
number.nursery_soother_baseline_volume
number.nursery_soother_level_1_volume
number.nursery_soother_level_2_volume
number.nursery_soother_level_3_volume
number.nursery_soother_level_4_volume
number.nursery_soother_maximum_volume
button.nursery_soother_simulate_cry_event
```

Entity IDs are user-editable registry data and are never controller identity.
Consumers use standard `select.select_option`, `switch.turn_on`,
`switch.turn_off`, `number.set_value`, and `button.press` actions.
The optional physical-button bindings are config-entry trigger attachments,
not new entities or a vendor-specific action API.

## Restart and development schema safety

Persistent configuration stores caregiver intent, not transient evidence or
episode progress. The integration never persists cry-event buffers,
confirmation time, dwell state, attention deadline, or actionable-notification
tokens for restoration.

On restart or reload:

- Standby stays Standby and issues no media action;
- a persisted active level may be recovered only at its exact capped volume
  and mapped sound after dependencies validate;
- old event evidence and automatic increases are discarded;
- the currently on physical sensor is not retroactively counted as multiple
  events;
- old notification actions cannot be accepted.

Config-entry schema v7 intentionally provides no migration or backward
compatibility. An existing entry must be removed and added again, then fully
configured against the v7 schema. Old data must never be interpreted as v7
button triggers or allowed to start playback, infer Automatic operation, or
bypass Standby and volume validation.

## Privacy and diagnostics

Policy evaluation remains inside Home Assistant. The controller does not save
camera images or notification contents and does not contact device vendors.
Selected integrations retain responsibility for their own network behavior.

Diagnostics redact:

- notification action names and device targets;
- media content IDs, URLs, and filesystem paths;
- camera and snapshot URLs;
- episode IDs, notification action tokens, and tags;
- credentials and webhook identifiers.

Diagnostics may include non-sensitive option values, current level and policy
state, aggregate evidence counts/durations, dependency availability categories,
timer presence without private identifiers, whether an event was simulated,
and recent error categories. Logs describe transitions and error classes
without raw camera payloads or notification bodies.

## Testing strategy

- Evidence tests cover rising-edge normalization, duplicate states, rolling
  expiry, the immediate one-event manual threshold, two-event and
  eight-active-second automatic confirmation, the default eight-second
  automatic debounce boundary, one-event or six-active-second continuing
  stages, and the 60-second event gap.
- Automatic tests prove the first event provisionally moves Baseline to Level
  1, confirmation retains it without a second increase, an unconfirmed response
  rolls back after the 25-second provisional timeout, subsequent increases
  respect 20-second dwell and require fresh evidence, the controller stops at
  Level 4, and no increase reuses a prior generation.
- Manual tests prove the first event immediately produces an exact suggestion
  without a level or volume change and that the selected exact level is the
  implicit response.
- Quiet tests prove one downshift per 120-second uninterrupted interval in both
  modes, interruption by a new event, and a floor at Baseline.
- Attention tests prove the 150-second base timer begins at the first manual
  alert or automatic confirmation, is not extended by level changes, lets one
  already-pending event gap resolve first, cannot be extended repeatedly by
  fresh events, and otherwise enters Standby with caregiver attention.
- Safety tests cover monotonic configuration, the runtime Maximum cap,
  Standby, dependency loss, playback takeover, failed media actions, Sonos
  one-item repeat-all setup, idle rebuilding, and conservative setting cleanup.
- Simulation tests prove one press contributes one event and uses the same
  policy without mutating the physical sensor.
- Physical-button tests cover independent optional attachment, toggle at
  Standby and every active level, one-step increase and decrease, boundary
  no-ops, Level-lock bypass, rapid callbacks, gesture-agnostic dispatch, and
  unload cleanup through generic Home Assistant trigger configurations.
- Notification tests cover evidence summaries, exact-level action IDs,
  cross-phone synchronization, partial delivery, and stale-action rejection.
- Config-flow tests cover setup, reconfigure, options, single-resource
  ownership, timing bounds, volume relationships, independently optional action
  triggers, and deliberate v7 remove-and-re-add schema replacement.
- Entity tests cover stable unique IDs, device linkage, level select options,
  Automatic operation, six numbers, state propagation, and standard actions.
- Lifecycle tests cover Standby startup, exact-level recovery, unload cleanup,
  restart evidence reset, and removal without orphan callbacks.
- Diagnostics tests prove every sensitive field is redacted.

The controller remains deterministic and exposes side effects through small
Home Assistant adapters so transition tests remain fast.

## Upstream Home Assistant contracts

The implementation follows current official documentation for [config entry
lifecycle and platform
forwarding](https://developers.home-assistant.io/docs/config_entries_index/),
[`ConfigEntry.runtime_data`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/),
[event and timer
helpers](https://developers.home-assistant.io/docs/integration_listen_events/),
[entity naming and updates](https://developers.home-assistant.io/docs/core/entity/),
[binary-sensor entities](https://developers.home-assistant.io/docs/core/entity/binary-sensor/),
[local media sources](https://www.home-assistant.io/integrations/media_source/),
and [Companion app actionable
notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications/).
