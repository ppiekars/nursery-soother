## Pitch

**Nursery Soother** is a Home Assistant integration that turns an existing smart camera and speaker into a privacy-friendly, SNOO-inspired nursery assistant.

It uses a camera’s baby-cry detection as an input, plays continuous white noise through a smart speaker, and helps parents respond through controlled volume changes, actionable phone notifications, a shared dashboard, and Siri commands.

Rather than trying to replace a baby monitor or fully automate care, the integration provides a safe, configurable response loop:

```text
Cry detected
    ↓
Evaluate timing and current nursery state
    ↓
Suggest or apply a small, capped white-noise increase
    ↓
Notify both parents with camera and response controls
    ↓
Return to baseline after a quiet period
    ↓
Stop escalating and request attention if crying continues
```

## What the integration does

The integration coordinates existing Home Assistant entities instead of directly implementing support for specific devices.

It consumes:

* a cry-detection binary sensor, such as Reolink E1 Zoom;
* a camera entity for snapshots or live video;
* a media player, such as Sonos Era 100 SL;
* Home Assistant mobile notification targets for both parents.

It provides:

* continuous local white-noise playback;
* configurable baseline and boosted volume levels;
* a hard maximum-volume limit;
* two operating modes:

  * **Suggestions:** ask a parent before changing anything;
  * **Assisted:** perform one or two conservative volume increases automatically;
* cry debounce, cooldown, and inferred settling timers;
* actionable notifications with options such as Boost, Baseline, Acknowledge, Stop, and Open Camera;
* synchronized state and acknowledgement across both parents’ phones;
* a nursery dashboard showing the camera, current mode, volume, recent events, and controls;
* Home Assistant actions and scripts that can be called through Siri and Apple Shortcuts;
* recovery after Home Assistant restarts and alerts when the camera or speaker becomes unavailable.

The integration should remain device-agnostic. Reolink and Sonos are the initial target setup, but any compatible `binary_sensor`, `camera`, and `media_player` could be selected during configuration.

## Core design

The integration is primarily a state machine:

```text
Disabled
Baseline
Cry Pending
Boost 1
Boost 2
Attention Required
Settling
```

It owns the response policy, timers, safety limits, and parent coordination.

Home Assistant’s official integrations continue to own:

* camera connectivity;
* cry-detection entities;
* speaker playback;
* mobile notifications;
* HomeKit and Siri connectivity.

This avoids duplicating Reolink, Sonos, or Apple integrations and keeps the project maintainable.

## Home Assistant experience

After installation, a user would add **Nursery Soother** from the Integrations page and complete a guided setup flow:

1. Select the cry sensor.
2. Select the nursery camera.
3. Select the speaker.
4. Choose a local white-noise media source.
5. Select parent notification targets.
6. Configure baseline, boost, and maximum volume levels.
7. Choose Suggestions or Assisted mode.
8. Configure debounce, settling, and escalation timing.

The integration would then create standard Home Assistant entities such as:

```text
switch.nursery_soother
select.nursery_response_mode
sensor.nursery_state
sensor.nursery_recommendation
binary_sensor.nursery_attention_required
number.nursery_baseline_volume
number.nursery_boost_volume
number.nursery_max_volume
button.nursery_boost
button.nursery_baseline
button.nursery_acknowledge
```

Using standard entities makes the integration work immediately with native dashboards, automations, voice assistants, HomeKit Bridge, and Apple Shortcuts.

An optional custom dashboard card could later provide a more polished, mobile-first nursery interface.

## Deployment through HACS

The project would be built as a standard Home Assistant custom integration and distributed through HACS.

Repository structure:

```text
nursery-soother/
├── custom_components/
│   └── nursery_soother/
│       ├── __init__.py
│       ├── manifest.json
│       ├── config_flow.py
│       ├── controller.py
│       ├── sensor.py
│       ├── binary_sensor.py
│       ├── switch.py
│       ├── select.py
│       ├── number.py
│       ├── button.py
│       ├── services.yaml
│       ├── diagnostics.py
│       └── translations/
│           └── en.json
├── hacs.json
├── README.md
├── LICENSE
└── tests/
```

Deployment flow:

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

The initial release can be installed as a custom HACS repository. Once mature, documented, and widely tested, it could be submitted for inclusion in HACS’s default repository list.

## Suggested MVP

The first version should focus on the smallest reliable product:

* one nursery per configuration entry;
* generic cry sensor, camera, and media-player selection;
* baseline white noise;
* Suggestions mode;
* one temporary boost level;
* hard volume cap;
* actionable notifications;
* acknowledgement from either parent;
* automatic return to baseline;
* standard Home Assistant entities;
* diagnostics and unit-tested state transitions.

Later releases could add:

* a second boost stage;
* Assisted mode;
* custom dashboard card;
* event history and response analytics;
* multiple detection inputs;
* Frigate audio-event support;
* bedtime schedules;
* richer notification previews;
* Home Assistant Repairs warnings for invalid or unavailable devices.

## Positioning

The project is not a SNOO replacement and does not attempt to automate physical soothing. Its value is in combining devices many parents already own into one coherent, locally controlled workflow.

The core proposition is:

> **A reusable Home Assistant integration that connects cry detection, white noise, parent notifications, and voice control into a conservative, configurable nursery response system.**

It provides the convenience of a dedicated smart-nursery product while retaining local control, device choice, transparent automation rules, and clear parent oversight.
