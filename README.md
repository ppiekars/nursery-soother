# Nursery Soother

Nursery Soother is a Home Assistant custom integration that coordinates an
existing cry-detection sensor, camera, and media player into a conservative,
SNOO-inspired soothing workflow.

It provides one exact level control: **Standby**, **Baseline**, and **Level 1**
through **Level 4**. A parent can select a level directly. An optional
**Automatic operation** switch gives the first cry event an immediate,
reversible Baseline-to-Level-1 response, then allows confirmed, continuing cry
events to move up one level at a time. With automatic operation off, the
integration immediately reports the first cry event and suggests the exact
next level instead. A separate **Level lock** switch makes the current active
level a policy hold: automatic increases and quiet downshifts stop, while
evidence, notifications, suggestions, and direct parent control continue.

> [!IMPORTANT]
> Nursery Soother is not a medical device, baby monitor, or substitute for
> direct adult supervision. A camera, network, speaker, phone, notification
> service, or Home Assistant itself can fail. Treat every notification and
> control as a convenience, not a safety system.

## Level-based behavior

```text
Standby (off)
    ↓ parent selects Baseline or Level 1–4
Play the sound mapped to that level at its configured, capped volume
    ↓ first cry event
Automatic off: notify immediately and suggest one exact higher level
Automatic on at Baseline: move immediately to Level 1 provisionally
    ↓ automatic evidence confirms, or fresh manual evidence follows the dwell
Keep provisional Level 1, or respond one level at a time
    ↓ fresh cry evidence continues after the dwell period
Repeat one level at a time, never above Level 4 or Maximum volume
    ↓ no new cry event for a full quiet interval
Move down exactly one level; repeat toward Baseline while quiet continues
    ↓ episode remains unresolved for the attention timeout
Enter Standby, stop owned playback, and request caregiver attention
```

The following rules are deliberate:

- New entries start at **Standby** with **Automatic operation** and **Level
  lock** off. Installing, reconfiguring, or reloading a standby entry cannot
  start playback.
- Standby is truly off: physical cry events are ignored, **Simulate cry event**
  is a no-op, and Automatic operation can never leave Standby. A parent must
  first select Baseline or another active level to start a soothing session.
- Selecting any active level is an explicit command. The integration sets that
  level's exact volume and starts or changes to its mapped soothing sound.
- Baseline and Levels 1–4 each have a configured Local Media sound. They may
  reuse one audio item or select different tracks. A level change restarts
  playback only when its mapped sound differs.
- Reolink exposes cry detections as brief binary-sensor pulses. Nursery Soother
  counts rising edges and active time inside a rolling evidence window; it does
  not require the sensor to remain continuously on.
- In manual mode, the first rising edge immediately sends a caregiver
  notification and exact next-level suggestion without changing the speaker.
- In automatic mode, an initial cry candidate must satisfy the configured
  confirmation debounce and reach either the event-count or
  cumulative-active-time threshold.
- With Automatic operation on at Baseline, the first event immediately starts
  a provisional Level 1 response. Confirmation keeps that response; without
  confirmation, it returns to Baseline after the level dwell. No caregiver
  notification or stronger level is triggered by the lone event.
- Automatic operation changes only upward behavior. It increases exactly one
  level, waits for the dwell period, discards evidence used by the prior
  increase, and requires fresh evidence before another increase.
- With automatic operation off, one cry event sends a notification with an
  exact next-level suggestion. It never changes the level by itself.
- Quiet downshifts are conservative in both modes: each uninterrupted quiet
  interval moves down one level, stopping at Baseline rather than Standby.
- Level lock freezes automatic increases and quiet downshifts in either
  operating mode. Evidence, notifications, and exact higher-level suggestions
  continue. Direct parent controls can still select any exact level or Standby.
  Protective fail-safes may also lower output or stop playback.
- An unresolved confirmed episode has one base attention deadline. If a
  no-event gap is already pending when it expires, that gap gets one bounded
  grace period to resolve first. Fresh events cannot extend the grace. An
  episode that remains unresolved then enters Standby, stops only its own
  playback, and asks a caregiver to attend.
- There are no Boost, Return to baseline, or Acknowledge commands. The level
  selector expresses the parent's decision and implicitly resolves the current
  suggestion.
- Missing, unknown, or unavailable dependencies fail safe. The controller does
  not increase the level and reports the problem through the remaining Home
  Assistant and notification surfaces.
- Every commanded level remains bounded by the configured Maximum volume. Test
  the actual speaker at deliberately low values before using automatic mode.

## Why cry detections are treated as events

A read-only analysis of the configured Reolink cry sensor's available recorder
window found **1,605 complete pulses over 10 days**. The median on-time was
**3.29 seconds**, p90 was **8.12 seconds**, and only **0.69%** of pulses lasted
at least 15 seconds. The median off-gap between pulses was about 19 seconds.
A 15-second continuously-on debounce would therefore discard more than 99% of
the observed detections.

Home Assistant's Reolink integration lists baby crying as a push-capable sound
detection. Its upstream implementation receives Reolink alarm messages,
updates whether the `AItype` set contains `cry`, and exposes that transient
value through a binary sensor. Nursery Soother deliberately consumes each
off-to-on transition as an event sample while still using the ordinary Home
Assistant entity boundary. See the official [Reolink integration
documentation](https://www.home-assistant.io/integrations/reolink/), the
[Home Assistant Reolink binary-sensor
implementation](https://github.com/home-assistant/core/blob/dev/homeassistant/components/reolink/binary_sensor.py),
and the upstream [`reolink_aio` TCP event
parser](https://github.com/starkillerOG/reolink_aio/blob/main/reolink_aio/baichuan/baichuan.py#L691-L742).

The history figures are sanitized aggregate timings. No camera media,
credentials, entity attributes, or notification contents were retained.

## Requirements

Before setup, make these available in Home Assistant:

- Home Assistant 2026.7.0 or newer;
- a `binary_sensor` whose on transition means a cry was detected;
- a `camera` for parent-facing notification and dashboard context;
- a `media_player` that supports setting volume, playing the selected media,
  and stopping or pausing playback;
- local audio selected for each active level through Home Assistant's
  [Local media source](https://www.home-assistant.io/integrations/media_source/#local-media);
- one or more Home Assistant Companion app notification actions named
  `notify.mobile_app_*`.

A physical button is optional. If used, its desired actions must be exposed to
Home Assistant as triggers by an integration such as ZHA or Zigbee2MQTT.

The integration uses Home Assistant entity and action boundaries. Reolink and
Sonos remain optional, and no vendor SDK is required. When the selected player
is Sonos and exposes the required queue and repeat features plus its paired
crossfade switch, Nursery Soother uses those standard Home Assistant controls
for seamless looping. Other players use the generic playback path described
below.

## Installation with HACS

This repository is not yet listed in HACS by default. Add it as a custom
repository:

1. Open HACS.
2. Open the three-dot menu and choose **Custom repositories**.
3. Enter `https://github.com/ppiekars/nursery-soother`.
4. Select **Integration** as the type, then choose **Add**.
5. Find **Nursery Soother** in HACS and download it.
6. Restart Home Assistant.
7. Go to **Settings > Devices & services**, choose **Add integration**, and
   search for **Nursery Soother**.

### Manual installation

Copy `custom_components/nursery_soother` into the `custom_components` directory
inside the Home Assistant configuration directory, then restart Home
Assistant. The resulting manifest path must be:

```text
<home-assistant-config>/custom_components/nursery_soother/manifest.json
```

## Setup and configuration

The setup flow asks for the stable dependencies of one nursery:

1. cry-detection binary sensor;
2. nursery camera;
3. media player;
4. a **Local media** sound for Baseline and each of Levels 1–4 (the same item
   may be selected more than once);
5. one or more Companion app notification targets;
6. optionally, any of the independently configured **Toggle trigger**,
   **Increase trigger**, and **Decrease trigger** actions for a physical button.

After setup, the level remains Standby and automatic operation remains off.
Review every volume on the actual speaker before selecting an active level.
Each cry sensor, camera, and speaker can belong to only one Nursery Soother
entry, preventing two controllers from competing for the same device.

### Optional physical-button triggers

One useful mapping is short press to **Toggle trigger**, double press to
**Increase trigger**, and long press to **Decrease trigger**. Each action is
independently optional. Nursery Soother runs whichever Home Assistant trigger
you select; it does not interpret or hardcode gesture names.

- Toggle enters Baseline from Standby and enters Standby from any active level.
- Increase moves up exactly one active level only while the soother is on. It
  does nothing in Standby or at Level 4.
- Decrease moves down exactly one active level only while the soother is on. It
  does nothing in Standby or at Baseline.

All three actions are direct parent commands, so Level lock does not block them.
Normal dependency, playback-ownership, and Maximum-volume safeguards still
apply. The fields use generic Home Assistant trigger selectors and work with
the actions exposed by ZHA, Zigbee2MQTT, and other integrations; Nursery
Soother contains no Sonoff- or other vendor-specific button handling.

The initial defaults are intentionally low and conservative:

| Setting | Default | Meaning |
| --- | ---: | --- |
| Baseline volume | 10% | Normal continuous soothing level |
| Level 1 volume | 15% | First response level |
| Level 2 volume | 20% | Second response level |
| Level 3 volume | 25% | Third response level |
| Level 4 volume | 30% | Highest policy level |
| Maximum volume | 40% | Hard ceiling for every integration-issued volume command |
| Confirmation debounce | 8 seconds | Default delay before an automatic initial candidate can be confirmed |
| Manual initial threshold | 1 event | Sends the first caregiver suggestion immediately |
| Automatic initial threshold | 2 events or 8 active seconds | Required evidence to confirm the first automatic response |
| Continuing evidence threshold | 1 fresh event or 6 fresh active seconds | Required post-response evidence for each later step |
| Evidence window | 30 seconds | Window used for event count and active time |
| Cry-event gap | 60 seconds | No-event interval that closes the active cry episode |
| Provisional Level 1 timeout | 25 seconds | Grace for confirming the immediate first-event response |
| Level dwell | 20 seconds | Minimum time before another response decision |
| Quiet step-down | 120 seconds | Uninterrupted quiet required for each one-level decrease |
| Attention timeout | 2.5 minutes | Configurable base deadline from the first manual alert or automatic confirmation; one pending no-event gap may finish first |

Volume settings must satisfy:

```text
0% <= Baseline <= Level 1 <= Level 2 <= Level 3
   <= Level 4 <= Maximum <= 100%
```

The controller applies the hard maximum again at the media-player boundary.
Use the config entry's **Configure** action to change volumes and timing. Use
**Reconfigure** to replace the cry sensor, camera, speaker, per-level media, or
notification targets, or to add, replace, or remove any physical-button action
trigger. Reconfiguration preserves parent intent but never changes a Standby
entry into an active level.

Configuration-entry schema v7 deliberately has no migration or backward
compatibility. Remove and re-add any existing Nursery Soother entry after
updating to this version, then configure it again.

## Entities and standard actions

Nursery Soother creates normal Home Assistant entities. Their generated entity
IDs can be renamed, so automations should select entities from the UI rather
than depend on a copied sample ID.

| Entity type | Purpose | Standard Home Assistant action |
| --- | --- | --- |
| `select` | Exact output: Standby, Baseline, or Level 1–4 | `select.select_option` |
| `switch` | Allow or prevent automatic upward level changes | `switch.turn_on`, `switch.turn_off` |
| `switch` | Freeze policy-driven changes at the current level | `switch.turn_on`, `switch.turn_off` |
| `switch` | Play or stop Baseline sound while the system remains in Standby | `switch.turn_on`, `switch.turn_off` |
| `sensor` | Current policy phase | Read-only |
| `sensor` | Current recommendation and exact suggested next level | Read-only |
| `binary_sensor` | Whether caregiver attention is required | Read-only |
| `number` | Baseline volume percentage | `number.set_value` |
| `number` | Level 1 volume percentage | `number.set_value` |
| `number` | Level 2 volume percentage | `number.set_value` |
| `number` | Level 3 volume percentage | `number.set_value` |
| `number` | Level 4 volume percentage | `number.set_value` |
| `number` | Maximum volume percentage | `number.set_value` |
| `number` | Attention deadline in minutes | `number.set_value` |
| `button` | Inject one finite cry event for testing | `button.press` |

The state sensor reports `standby`, `soothing`, `cry_pending`, `responding`,
`settling`, or `attention_required`. The level select is the authoritative
output control; state and recommendation explain what the policy is doing and
why. When recommendation is `increase_level`, its `suggested_level` attribute
contains the exact target such as `level_2`.

The state sensor also exposes a structured, privacy-safe explanation contract
for dashboards and automations. `explanation` is a stable machine-readable key;
`evidence` contains the current event and active-time totals plus their current
thresholds; and `countdowns` maps every active policy clock to an absolute UTC
ISO 8601 deadline. `next_countdown` and `next_countdown_at` identify the earliest
one as a convenience. Consumers should calculate remaining time from the
absolute timestamp instead of expecting the integration to update every second.

Countdown keys may include `confirmation_gate`, `level_dwell`,
`provisional_rollback`, `cry_gap`, `quiet_step_down`, and `attention_deadline`.
Several clocks can be active together because, for example, a response dwell,
the cry-event gap, and the base attention deadline have different meanings.
Expired or cancelled clocks are removed on the corresponding controller update.
The custom dashboard card does not render these attributes yet.

Explanation keys are `standby`, `soothing`, `gathering_initial_evidence`,
`confirmation_debounce`, `provisional_response`,
`gathering_continuing_evidence`, `level_dwell`, `caregiver_decision`,
`caregiver_attention`, `quiet_step_down`, `level_locked`,
`attention_required`, and `check_devices`.

While an active level is selected, press **Simulate cry event** to inject one
artificial rising-edge event without changing the physical sensor. Repeated
presses can exercise the same event-count, evidence-window, manual suggestion,
and automatic level paths used by real detections. One press sends the initial
manual suggestion immediately; two presses can qualify the initial automatic
response. After that response, one fresh press can qualify a later decision
once the post-response dwell has elapsed. In Standby the test is a
no-op, just as physical events cannot start a session. The example dashboard
asks for confirmation because an active-session test can send real
notifications and, when automatic operation is enabled, can affect the speaker.

## Manual and automatic operation

With **Automatic operation off**, the first cry event immediately sends one
shared, tagged notification that reports the evidence and recommends the exact
next level. It does not wait for the confirmation debounce and never changes
speaker volume. Only the current notification is visible:
after the dwell period, fresh qualifying evidence in the same episode may
replace it with a newer suggestion. Choosing the proposed level from the
notification, dashboard, automation, or Siri calls the same guarded exact-level
command. Ignoring the suggestion does not change speaker volume.

With **Automatic operation on** at Baseline, the first cry event immediately
starts a provisional Level 1 response. If initial evidence confirms, Level 1
is retained and the normal continuing-evidence policy begins. If it does not
confirm within the 25-second provisional timeout, the controller returns to
Baseline without notifying caregivers. At higher starting levels, initial
confirmation still precedes any increase. A later advance requires both the
dwell and either one fresh post-change event or six fresh active seconds;
evidence that justified the previous response is never reused. Automatic
operation cannot skip a
level, exceed Level 4, bypass Maximum, leave Standby, or override a dependency
or playback-ownership fault. A parent must select an active level before cry
response monitoring begins.

With **Level lock on**, evidence, caregiver notifications, and exact
higher-level suggestions continue, but the policy cannot increase or quietly
decrease the level. A parent may still select any exact level, accept a
suggestion, or select Standby. Unlocking resumes normal policy timing; a quiet
downshift deferred by the lock receives a fresh quiet interval. The base
attention deadline, dependency fail-safe, and playback-ownership protections
can still enter Standby or lower output as required.

In both modes, each 120-second uninterrupted quiet interval moves down one
level until Baseline. The first manual alert or automatic confirmation starts
the configured attention deadline, which defaults to 2.5 minutes. If a
60-second no-event gap is already pending when that deadline arrives, it gets
one bounded chance to finish; new events cannot extend that grace. A completed
gap closes the episode and cancels attention. Otherwise the controller enters
Standby and requests direct caregiver attention. The native **Attention
deadline** number changes the duration for future episodes without moving a
deadline already in progress.

## Parent notifications

Manual suggestions include the evidence summary, current level, and exact
proposed level. Automatic notifications state the level change and its reason.
Notifications include one static camera frame, but deliberately omit the live
camera attachment so expanding one keeps its action buttons available instead
of opening an embedded camera player.

There is no separate Acknowledge action. A parent's exact-level selection is
the shared decision, synchronizes state for every phone, and invalidates stale
episode actions. Tapping the notification itself still opens the configured
camera.

Notification presentation differs between iOS and Android, and the operating
system can limit visible actions. See the official Companion app documentation
for [actionable
notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications/).

## Dashboard

Nursery Soother ships an optional `custom:nursery-soother-card` that combines
the nursery camera, elapsed session time, independent Baseline playback, policy
status, recommendation, attention state, exact level control, Automatic
operation, and Level lock. When Home Assistant's frontend is
available, the integration serves and loads the card module automatically from
`/nursery_soother/nursery-soother-card.js`; do not add a separate dashboard
resource. On Home Assistant 2026.7 or newer, choose **Add card > Nursery
Soother** to configure it with the built-in visual editor.

Manual YAML uses these keys:

| Key | Required | Value |
| --- | --- | --- |
| `type` | Yes | `custom:nursery-soother-card` |
| `camera_entity` | Yes | Configured nursery `camera` |
| `level_entity` | Yes | Nursery Soother level `select` |
| `baseline_entity` | Yes | Baseline sound preview `switch` |
| `automatic_entity` | Yes | Automatic operation `switch` |
| `lock_entity` | Yes | Level lock `switch` |
| `state_entity` | Yes | Policy-state `sensor` |
| `recommendation_entity` | Yes | Recommendation `sensor` |
| `attention_entity` | Yes | Attention-required `binary_sensor` |
| `camera_view` | No | `live` (default), `auto`, or `image` |

```yaml
type: custom:nursery-soother-card
camera_entity: camera.nursery
level_entity: select.nursery_soother_level
baseline_entity: switch.nursery_soother_baseline_sound_preview
automatic_entity: switch.nursery_soother_automatic_operation
lock_entity: switch.nursery_soother_level_lock
state_entity: sensor.nursery_soother_state
recommendation_entity: sensor.nursery_soother_recommendation
attention_entity: binary_sensor.nursery_soother_attention_required
camera_view: live
```

The six level buttons call `select.select_option` for Standby, Baseline, or an
exact Level 1–4. The **Auto** and **Lock** controls call `switch.turn_on` or
`switch.turn_off` from the current switch state, and **Set** applies the exact
level exposed in the recommendation sensor's `suggested_level` attribute.
The camera timer runs from the active session's start and resets in Standby.
The speaker control toggles Baseline playback independently while Standby is
selected, so it does not start a session, the response policy, or the timer.
`live` and `auto` use Home Assistant's picture-entity camera behavior, while
`image` refreshes the authenticated camera snapshot every 10 seconds. Tapping
any camera view opens the standard camera more-info dialog. When attention is
required, **Attend** opens that same camera dialog; it does not acknowledge or clear attention,
change the level, or call a separate Acknowledge action.
Volume and attention-deadline numbers and **Simulate cry event** remain
available through native entity cards and the device page rather than this
compact card.

Entity IDs are registry data and can be renamed. Select the configured camera
and the seven entities shown on the Nursery Soother device page instead of
relying on the sample IDs above. If an entity is renamed after the card is
configured, update that field in the visual editor or YAML. A complete starting
view is available in
[`examples/dashboard.yaml`](examples/dashboard.yaml), including a commented
native-card fallback. The custom card is optional: standard Home Assistant
cards and actions continue to expose the integration's full entity contract.

## Siri and Apple Shortcuts

No Nursery Soother-specific Siri bridge is needed. Apple Shortcuts can use the
Home Assistant Companion app's standard **Select option**, **Control switch**,
or **Perform action** actions. For example, “Nursery Level 2” can select
`level_2`, while “Nursery standby” can select `standby`.

The same select and switch entities can be exposed through HomeKit Bridge. See
Home Assistant's current [Apple App Intents and Siri Shortcuts
guide](https://companion.home-assistant.io/docs/integrations/siri-shortcuts/).

## Playback continuity

For a compatible Sonos player, Nursery Soother replaces the current Sonos
queue with one copy of the selected track, starts that queue transport, enables
the player's crossfade, and sets repeat to **All**. Establishing the queue
transport first lets an explicit start take over an inactive Spotify Connect or
other external transport that rejects Sonos play-mode changes even after it has
stopped. Sonos then loops that single queue item itself. Home Assistant does not
repopulate the queue at every track boundary, so the loop does not depend on a
periodic timer or a new play command when the track ends. Nursery Soother
refreshes Sonos's subscription-backed crossfade entity before capturing or
confirming it, normalizes Local Media's MIME type to Sonos's `music` media type,
and waits for stop state before replacing a track.

The Sonos optimization deliberately replaces any queue that existed before the
soothing session and cannot restore those prior queue items. On a normal
Standby, unload, or owned-playback stop, Nursery Soother clears its queue and
restores the repeat and crossfade values it observed before the session. It
restores a setting only when it still has the value Nursery Soother applied;
if the Sonos group changed, it leaves both settings alone. If a parent or
another source takes over playback, Nursery Soother makes no queue, repeat, or
crossfade call, because preserving the replacement audio takes priority over
restoring its snapshot.

If repeat-all or crossfade is disabled during an owned native loop, Nursery
Soother stops claiming seamless playback, enters Standby, and requests
caregiver attention.

If an owned Sonos session unexpectedly reports idle, off, or paused, Nursery
Soother rebuilds the one-item queue. That recovery asks Home Assistant to
resolve the configured Local Media item again, so a new signed media URL is
used when necessary. Home Assistant Local Media URLs currently have a 24-hour
signature lifetime, so an uninterrupted Sonos queue must not be treated as a
forever guarantee. A
session that reaches URL expiry can stop before the idle recovery rebuilds it,
with an audible interruption; the tested target is an overnight session within
that URL lifetime. A player that does not expose the complete Sonos feature set
falls back to one direct `play_media` call and the same idle-state recovery,
without changing a queue, repeat mode, or crossfade setting. Test continuity
and stop behavior on the actual speaker before relying on either path.

## Privacy and diagnostics

The response policy runs inside Home Assistant. Nursery Soother does not upload
camera media, implement its own cloud service, or contact a device vendor. The
selected camera, media-player, and Companion app integrations retain their own
normal connectivity and privacy characteristics.

Diagnostics include policy, aggregate evidence, and availability information
useful for troubleshooting while redacting notification targets, media
identifiers and URLs, camera or snapshot URLs, episode action tokens, and
credentials. Logs record transitions and error categories, not nursery media
or notification contents.

## Limitations and deferred features

The level-to-sound model is separate from level-to-volume policy, and every
active level has its own Local Media selection. This release does not include
schedules, multiple cry inputs, long-term event analytics, or device-specific
Reolink or Frigate behavior. Sonos has the bounded playback-continuity
optimization described above; all policy and ownership rules remain shared
with other players.

The controller requests playback again when its owned player reports idle,
off, or paused. Continuous behavior still depends on the selected player and
media item, so verify it on the actual speaker.
If a parent starts different media, Nursery Soother relinquishes the speaker,
moves its visible level to Standby, requests attention, and does not alter or
stop that media. From that visible Standby state, select the desired active
level directly to authorize replacing the current playback with a new owned
session; no extra Standby selection is required. On Sonos, that explicit
selection stops the current playback, captures its repeat and crossfade values,
and replaces the queue. An explicit user replay is treated as parent-owned
media even when it uses the same raw media ID as the configured sound.

## Troubleshooting

### The integration is installed but nothing plays

This is expected while the level is Standby. Select Baseline at a low test
volume. Confirm that the media player is available, the selected media plays
manually, and the player supports volume set, play media, and stop or pause.

### Crying is visible in history but no suggestion appears

In manual mode, one rising edge should notify immediately. In automatic mode,
check that at least two rising edges or eight cumulative active seconds occur
inside 30 seconds and that the configured confirmation debounce—eight seconds
by default—has elapsed. Later steps accept one fresh event or six fresh active
seconds after the dwell. Confirm the integration is not in Standby and its
camera, speaker, and notification targets are available.
In automatic mode at Baseline, one isolated event may still produce a quiet,
provisional Level 1 response, but it intentionally does not notify unless the
episode confirms.

### Automatic operation does not increase again immediately

This is intentional. After an automatic increase, the integration waits for
the level dwell period and requires fresh cry evidence. Events used for the
previous increase cannot authorize the next one.

### Notifications do not arrive

Open **Developer tools > Actions** and test each selected
`notify.mobile_app_*` action. Check Companion app notification permissions and
remote access. A target renamed or recreated after setup must be selected again
through Reconfigure.
