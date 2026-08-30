# Product pitch

**Nursery Soother** is a Home Assistant integration that turns an existing
cry-aware camera and speaker into a privacy-friendly, SNOO-inspired nursery
assistant with explicit, understandable response levels.

This design supersedes the earlier one-boost product pitch. The parent-facing
control is now one ordered level:

```text
Standby → Baseline → Level 1 → Level 2 → Level 3 → Level 4
```

A parent can select any level exactly. An optional Automatic operation switch
gives the first event an immediate, reversible Baseline-to-Level-1 response and
allows confirmed, continuing cry events to move upward one level at a time.
With automatic operation disabled, Nursery Soother reports the first cry event
immediately and suggests the exact next level without changing it.

## Product promise

Nursery Soother combines devices many parents already own into one coherent,
locally controlled response loop:

```text
Brief cry detections arrive as repeated events
    ↓
Aggregate event count and active time in a rolling window
    ↓
Notify immediately in manual mode, or apply a mild provisional automatic response
    ↓
Confirm a cry episode conservatively
    ↓
Suggest or apply exactly one higher soothing level
    ↓
Require fresh evidence before any later increase
    ↓
Step down gradually after quiet
    ↓
Keep playback going and request direct attention at a caregiver deadline
```

It does not attempt to diagnose distress, replace a baby monitor, or automate
care. The integration helps caregivers understand and respond to device
signals while preserving explicit limits and direct oversight.

## Six exact levels

| Level | Meaning |
| --- | --- |
| **Standby** | Off: no integration-owned playback and no automatic response |
| **Baseline** | Normal continuous soothing sound |
| **Level 1** | First response step |
| **Level 2** | Second response step |
| **Level 3** | Third response step |
| **Level 4** | Highest allowed policy step |

Baseline and Levels 1–4 each have an independently configured volume. Every
command is additionally capped by Maximum volume. Volumes must be monotonic,
so the UI and controller enforce:

```text
Baseline <= Level 1 <= Level 2 <= Level 3
         <= Level 4 <= Maximum
```

The output model maps an independently selected Local Media sound to every
active level. Families may select distinct tracks or reuse one item across
multiple levels without changing the response policy.

Standby is a hard off boundary. Physical cry events are ignored, the artificial
cry-event button is a no-op, and Automatic operation cannot start a soothing
session. A caregiver must select Baseline or another active level first.

## Evidence, not a held cry state

The camera communicates detections as short alarm events even though Home
Assistant exposes them through an on/off binary sensor. Aggregate recorder
history for the configured Reolink sensor contained **1,605 pulses over 10
days**. Median on-time was **3.29 seconds**, p90 was **8.12 seconds**, and only
**0.69%** remained on for 15 seconds. The median gap was about 19 seconds.

That pattern makes a traditional “sensor must remain on for 15 seconds”
debounce unsuitable. Nursery Soother instead treats every off-to-on transition
as a detection event and tracks two signals inside a 30-second rolling window:

- number of cry events;
- cumulative sensor-on time.

Manual mode reports the first event immediately. Automatic mode retains the
initial confirmation rule of two events or eight active seconds, with an
eight-second confirmation debounce by default. At Baseline, the first event
immediately applies Level 1 provisionally. Confirmation keeps it; otherwise the
controller returns to Baseline after 25 seconds without notifying caregivers.
After the first response, one fresh event or six fresh active seconds can
authorize the next step once the dwell has elapsed. A 60-second gap without a
new event closes the episode. These defaults are evidence-based
starting points, not a medical assessment, and should be validated against the
actual nursery before automatic operation is enabled.

The event interpretation matches the official [Home Assistant Reolink
integration](https://www.home-assistant.io/integrations/reolink/) and its
[upstream push-event
implementation](https://github.com/starkillerOG/reolink_aio/blob/main/reolink_aio/baichuan/baichuan.py#L691-L742).

## Two operating styles

### Manual response

Automatic operation is off by default. The first cry event immediately sends
all configured caregivers one shared, tagged notification with:

- the event evidence observed so far;
- current soothing level;
- an exact next-level suggestion;
- camera access and a safe Standby option when attention is needed.

Identical advice is not redelivered to caregiver targets already reached while
the episode, current level, suggested level, and test status remain unchanged;
a later matching decision retries only missed targets. A changed level or later
episode can alert again.

Ignoring the suggestion changes nothing. Selecting the proposed level from a
phone, dashboard, automation, HomeKit, or Siri calls the same guarded
exact-level command. The selection itself is the caregiver's shared decision;
there is no separate Acknowledge button.

### Automatic response

Automatic operation authorizes upward changes only. At Baseline, the first
event applies Level 1 immediately as a provisional response. Confirmation
keeps Level 1; without confirmation it returns to Baseline after 25 seconds. At
higher levels, confirmation can advance one level and never skip levels. After
every confirmed response the controller:

1. begins a 20-second dwell period;
2. clears evidence consumed by that change;
3. accepts only fresh post-change events for the next decision;
4. advances at most one more level after observing one fresh event or six fresh
   active seconds.

It cannot go beyond Level 4, bypass Maximum volume, reuse old events to race
through the levels, or act while a dependency or playback-ownership check is
unsafe.

Both modes step down one level after each uninterrupted 120-second quiet
interval until Baseline. The first manual alert or automatic confirmation also
starts a 150-second caregiver deadline. If a 60-second no-event gap is already
pending at expiry, it gets one bounded grace period to finish; fresh events do
not extend it. A completed gap closes the episode and cancels attention.
Otherwise Nursery Soother keeps the selected sound playing, pauses further
policy changes, and requests direct attention regardless of mode or level.

## What the integration coordinates

Nursery Soother consumes standard Home Assistant capabilities:

- a cry-detection `binary_sensor`, such as a Reolink camera's baby-cry sensor;
- a `camera` entity for caregiver context;
- a `media_player`, such as Sonos;
- one Local Media browser sound for each active level;
- one or more Companion app `notify.mobile_app_*` actions.

It provides:

- one exact level select containing Standby, Baseline, and Levels 1–4;
- an Automatic operation switch;
- a Level lock switch that freezes policy changes without blocking parents,
  notifications, or suggestions;
- six safe volume controls: five levels plus Maximum;
- a policy-state sensor and recommendation sensor;
- a caregiver-attention binary sensor;
- an artificial cry-event button for controlled testing;
- episode-scoped, synchronized notifications;
- gradual quiet downshift and a bounded caregiver-attention deadline;
- speaker playback ownership checks and restart-safe recovery;
- seamless Sonos looping through one queued item, crossfade, and repeat-all,
  with direct-playback fallback for other speakers;
- diagnostics with sensitive identifiers redacted.

Reolink, Sonos, Apple, and other integrations continue to own connectivity.
Nursery Soother owns only the policy, timers, level decisions, and guarded Home
Assistant actions. On compatible Sonos players it replaces the queue for the
soothing session, cannot restore the prior queue items, and lets Sonos repeat
one item natively; it does not periodically repopulate that queue. Normal stop
restores the previous repeat and crossfade settings, while a parent playback
takeover is left untouched.

## Home Assistant experience

After installation, a user adds **Nursery Soother** from the Integrations page:

1. select the cry sensor;
2. select the nursery camera;
3. select the speaker;
4. choose a **Local media** sound for Baseline and each of Levels 1–4;
5. select parent notification targets;
6. review Baseline, Level 1–4, and Maximum volumes;
7. review evidence, dwell, quiet, and attention timings;
8. leave Standby only after testing the actual speaker;
9. enable Automatic operation only after validating manual suggestions.
10. use Level lock when a parent wants policy output held at an exact level
    while notifications and manual suggestions continue.

The integration creates standard entities similar to:

```text
select.nursery_soother_level
switch.nursery_soother_automatic_operation
switch.nursery_soother_level_lock
switch.nursery_soother_baseline_sound_preview
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

These entities work with native dashboards, automations, HomeKit Bridge, Apple
Shortcuts, and Siri. The optional bundled Nursery Soother dashboard card adds a
compact control surface and camera shortcut without changing those standard
entity boundaries or embedding a camera feed.

## Deployment through HACS

The project is distributed as a standard Home Assistant custom integration:

```text
GitHub repository
    ↓
Added as a custom HACS repository
    ↓
HACS installs custom_components/nursery_soother
    ↓
Home Assistant restarts
    ↓
User adds Nursery Soother through the UI
    ↓
Future versions are delivered through HACS updates
```

Once mature, documented, and broadly tested, the repository can be submitted
for inclusion in HACS's default list.

## Initial product scope

The first level-based release includes:

- one nursery per configuration entry;
- generic cry sensor, camera, media-player, media, and notification selection;
- Standby, Baseline, and four exact response levels;
- one volume per active level plus a hard Maximum;
- immediate manual exact-level suggestions and optional automatic upward response;
- immediate provisional Level 1 response from Baseline in automatic mode;
- pulse-based automatic evidence confirmation;
- fresh evidence and dwell before every subsequent automatic increase;
- gradual quiet step-down and a bounded caregiver-attention deadline;
- episode-scoped notifications without Acknowledge;
- one artificial cry-event button;
- an optional purpose-built control dashboard card with a camera shortcut;
- native one-item crossfade/repeat-all looping on compatible Sonos players,
  with owned-idle recovery and generic-player fallback;
- standard Home Assistant entities, diagnostics, and tested state transitions.

Later releases can add:

- schedules or bedtime windows;
- multiple detection inputs and Frigate audio events;
- long-term response analytics and evidence tuning tools;
- additional notification presentation controls that preserve the no-capture
  privacy boundary.

Backward compatibility with the earlier development-only Boost/Baseline model
is not a product requirement. A clean removal and reintegration is preferable
to preserving ambiguous controls or unsafe historical state.

## Positioning

Nursery Soother is not a SNOO replacement and does not automate physical
soothing. Its value is a transparent control plane for devices parents already
own:

> **A reusable Home Assistant integration that turns cry-event evidence into
> exact, capped soothing levels, caregiver suggestions, and optionally
> conservative one-step automatic responses.**

It offers dedicated-product convenience while retaining local control, device
choice, visible rules, a hard volume ceiling, and direct caregiver authority.
