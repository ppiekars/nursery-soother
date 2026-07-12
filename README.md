# Nursery Soother

Nursery Soother is a Home Assistant custom integration that coordinates an
existing cry-detection sensor, camera, and media player into a conservative,
SNOO-inspired soothing workflow.

It provides one exact level control: **Standby**, **Baseline**, and **Level 1**
through **Level 4**. A parent can always select a level directly. An optional
**Automatic operation** switch gives the first cry event an immediate,
reversible Baseline-to-Level-1 response, then allows confirmed, continuing cry
events to move up one level at a time. With automatic operation off, the
integration explains confirmed evidence and suggests the exact next level
instead. A separate **Level lock** switch freezes policy-driven increases and
quiet downshifts at the selected level while preserving direct parent control.

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
    ↓ first cry event while Automatic is on and Baseline is selected
Move immediately to Level 1 provisionally; return after one dwell if unconfirmed
    ↓ repeated cry-event evidence is confirmed
Automatic off: notify and suggest one exact higher level
Automatic on: keep provisional Level 1, or move up exactly one level otherwise
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
- A cry candidate must satisfy the configured confirmation debounce and then
  reach either the event-count or cumulative-active-time threshold.
- With Automatic operation on at Baseline, the first event immediately starts
  a provisional Level 1 response. Confirmation keeps that response; without
  confirmation, it returns to Baseline after the level dwell. No caregiver
  notification or stronger level is triggered by the lone event.
- Automatic operation changes only upward behavior. It increases exactly one
  level, waits for the dwell period, discards evidence used by the prior
  increase, and requires fresh evidence before another increase.
- With automatic operation off, the same evidence sends a notification with an
  exact next-level suggestion. It never changes the level by itself.
- Quiet downshifts are conservative in both modes: each uninterrupted quiet
  interval moves down one level, stopping at Baseline rather than Standby.
- Level lock freezes automatic increases and quiet downshifts. Parents can
  still select any exact level or Standby while it is on. Dependency failures,
  playback takeover, and the fixed attention cutoff remain safety overrides.
- An unresolved confirmed episode has one fixed attention deadline. At expiry,
  the integration enters Standby, stops only its own playback, and asks a
  caregiver to attend. It never plays indefinitely at a high level.
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
5. one or more Companion app notification targets.

After setup, the level remains Standby and automatic operation remains off.
Review every volume on the actual speaker before selecting an active level.
Each cry sensor, camera, and speaker can belong to only one Nursery Soother
entry, preventing two controllers from competing for the same device.

The initial defaults are intentionally low and conservative:

| Setting | Default | Meaning |
| --- | ---: | --- |
| Baseline volume | 10% | Normal continuous soothing level |
| Level 1 volume | 15% | First response level |
| Level 2 volume | 20% | Second response level |
| Level 3 volume | 25% | Third response level |
| Level 4 volume | 30% | Highest policy level |
| Maximum volume | 40% | Hard ceiling for every integration-issued volume command |
| Confirmation debounce | 8 seconds | Default delay before an initial candidate cry can be confirmed |
| Initial evidence threshold | 2 events or 8 active seconds | Required evidence for the first response in an episode |
| Continuing evidence threshold | 1 fresh event or 6 fresh active seconds | Required post-response evidence for each later step |
| Evidence window | 30 seconds | Window used for event count and active time |
| Cry-event gap | 60 seconds | No-event interval that closes the active cry episode |
| Level dwell | 20 seconds | Provisional Level 1 grace and minimum time before another response decision |
| Quiet step-down | 120 seconds | Uninterrupted quiet required for each one-level decrease |
| Attention timeout | 150 seconds | Fixed deadline from episode confirmation before Standby and caregiver attention |

Volume settings must satisfy:

```text
0% <= Baseline <= Level 1 <= Level 2 <= Level 3
   <= Level 4 <= Maximum <= 100%
```

The controller applies the hard maximum again at the media-player boundary.
Use the config entry's **Configure** action to change volumes and timing. Use
**Reconfigure** to replace the cry sensor, camera, speaker, per-level media, or
notification targets. Reconfiguration preserves parent intent but never
changes a Standby entry into an active level.

During v4 migration, stored timings exactly equal to the former defaults—10
seconds for debounce and 30 seconds for level dwell—are interpreted as legacy
defaults and changed to 8 and 20 seconds. This also applies if those exact
values were deliberately selected, because stored intent is indistinguishable.
Other timing values are preserved. v5 entries receive Level lock off during
the v6 migration. The fixed 2/8 initial and 1/6 continuing evidence thresholds
apply to every current entry.

## Entities and standard actions

Nursery Soother creates normal Home Assistant entities. Their generated entity
IDs can be renamed, so automations should select entities from the UI rather
than depend on a copied sample ID.

| Entity type | Purpose | Standard Home Assistant action |
| --- | --- | --- |
| `select` | Exact output: Standby, Baseline, or Level 1–4 | `select.select_option` |
| `switch` | Allow or prevent automatic upward level changes | `switch.turn_on`, `switch.turn_off` |
| `switch` | Freeze policy-driven changes at the current level | `switch.turn_on`, `switch.turn_off` |
| `sensor` | Current policy phase | Read-only |
| `sensor` | Current recommendation and exact suggested next level | Read-only |
| `binary_sensor` | Whether caregiver attention is required | Read-only |
| `number` | Baseline volume percentage | `number.set_value` |
| `number` | Level 1 volume percentage | `number.set_value` |
| `number` | Level 2 volume percentage | `number.set_value` |
| `number` | Level 3 volume percentage | `number.set_value` |
| `number` | Level 4 volume percentage | `number.set_value` |
| `number` | Maximum volume percentage | `number.set_value` |
| `button` | Inject one finite cry event for testing | `button.press` |

The state sensor reports `standby`, `soothing`, `cry_pending`, `responding`,
`settling`, or `attention_required`. The level select is the authoritative
output control; state and recommendation explain what the policy is doing and
why. When recommendation is `increase_level`, its `suggested_level` attribute
contains the exact target such as `level_2`.

While an active level is selected, press **Simulate cry event** to inject one
artificial rising-edge event without changing the physical sensor. Repeated
presses can exercise the same event-count, evidence-window, manual suggestion,
and automatic level paths used by real detections. Two presses can qualify the
initial response; after that response, one fresh press can qualify a later
decision once the post-response dwell has elapsed. In Standby the test is a
no-op, just as physical events cannot start a session. The example dashboard
asks for confirmation because an active-session test can send real
notifications and, when automatic operation is enabled, can affect the speaker.

## Manual and automatic operation

With **Automatic operation off**, each qualified evidence decision sends one
shared, tagged notification that explains why crying was inferred—for example,
the number of detections and active seconds in the evidence window—and
recommends the exact next level. Only the current notification is visible:
after the dwell period, fresh qualifying evidence in the same episode may
replace it with a newer suggestion. Choosing the proposed level from the
notification, dashboard, automation, or Siri calls the same guarded exact-level
command. Ignoring the suggestion does not change speaker volume.

With **Automatic operation on** at Baseline, the first cry event immediately
starts a provisional Level 1 response. If initial evidence confirms, Level 1
is retained and the normal continuing-evidence policy begins. If it does not
confirm within the 20-second level dwell, the controller returns to Baseline
without notifying caregivers. At higher starting levels, initial confirmation
still precedes any increase. A later advance requires both the dwell and either
one fresh post-change event or six fresh active seconds; evidence that justified
the previous response is never reused. Automatic operation cannot skip a
level, exceed Level 4, bypass Maximum, leave Standby, or override a dependency
or playback-ownership fault. A parent must select an active level before cry
response monitoring begins.

With **Level lock on**, evidence and caregiver notifications continue, but the
policy cannot increase or quietly decrease the level. A parent may still select
another exact level or Standby. Unlocking resumes normal policy timing; a quiet
downshift deferred by the lock receives a fresh quiet interval. The fixed
attention deadline, dependency fail-safe, and playback-ownership protections
can still enter Standby or lower output as required.

In both modes, each 120-second uninterrupted quiet interval moves down one
level until Baseline. If the episode remains unresolved for 150 seconds from
confirmation, the controller enters Standby and requests direct caregiver
attention. A 60-second no-event gap closes the episode and cancels that
attention deadline.

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
the nursery camera, policy status, recommendation, attention state, exact level
control, Automatic operation, and Level lock. When Home Assistant's frontend is
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
`live` and `auto` use Home Assistant's picture-entity camera behavior, while
`image` refreshes the authenticated camera snapshot every 10 seconds. Tapping
any camera view opens the standard camera more-info dialog. When attention is
required, **Attend** opens that same camera dialog; it does not acknowledge or clear attention,
change the level, or call a separate Acknowledge action.
Volume numbers and **Simulate cry event** remain available through native
entity cards and the device page rather than this compact card.

Entity IDs are registry data and can be renamed. Select the configured camera
and the six entities shown on the Nursery Soother device page instead of
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
queue with one copy of the selected track, enables the player's crossfade, and
sets repeat to **All**. Sonos then loops that single queue item itself. Home
Assistant does not repopulate the queue at every track boundary, so the loop
does not depend on a periodic timer or a new play command when the track ends.

The Sonos optimization deliberately replaces any queue that existed before the
soothing session and cannot restore those prior queue items. On a normal
Standby, unload, or owned-playback stop, Nursery Soother clears its queue and
restores the repeat and crossfade values it observed before the session. It
restores a setting only when it still has the value Nursery Soother applied;
if the Sonos group changed, it leaves both settings alone. If a parent or
another source takes over playback, Nursery Soother makes no queue, repeat, or
crossfade call, because preserving the replacement audio takes priority over
restoring its snapshot.

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
level directly to authorize a new owned session; no extra Standby selection is
required. An explicit user replay is treated as parent-owned media even when it
uses the same raw media ID as the configured sound.

## Troubleshooting

### The integration is installed but nothing plays

This is expected while the level is Standby. Select Baseline at a low test
volume. Confirm that the media player is available, the selected media plays
manually, and the player supports volume set, play media, and stop or pause.

### Crying is visible in history but no suggestion appears

Nursery Soother evaluates a rolling event pattern, not one isolated pulse.
For the first response, check that at least two rising edges or eight cumulative
active seconds occur inside 30 seconds and that the configured confirmation
debounce—eight seconds by default—has elapsed. Later steps accept one fresh
event or six fresh active seconds after the dwell. Confirm the integration is
not in Standby and its camera, speaker, and notification targets are available.
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
