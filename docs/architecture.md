# Architecture foundations

## Purpose and boundary

Nursery Soother will be a Home Assistant **helper** integration. It will
coordinate entities already owned by Home Assistant integrations; it will not
implement Reolink, Sonos, mobile-app, HomeKit, or Siri connectivity.

The integration should consume standard Home Assistant interfaces:

- a `binary_sensor` for cry detection;
- a `camera` for a parent-facing view or snapshot;
- a `media_player` for white-noise playback;
- Home Assistant notification actions for parent communication.

No vendor SDK belongs in the initial architecture. Device-specific workarounds
should only be considered when a standard Home Assistant capability cannot
express a necessary behavior.

## Planned component layout

Only files needed by the inert foundation exist today. Add modules when they
have an implemented responsibility and tests; empty platform files make the
installation surface harder to reason about.

```text
custom_components/nursery_soother/
├── __init__.py          # config-entry lifecycle
├── config_flow.py       # setup; options/reconfigure later
├── const.py             # shared constants
├── manifest.json        # Home Assistant and HACS metadata
└── translations/en.json # complete English UI strings
```

Expected MVP modules, added incrementally:

```text
controller.py            # deterministic response state machine
models.py                # typed state/configuration models
entity.py                # shared entity base, if needed
sensor.py                # response state and recommendation
binary_sensor.py         # attention-required state
switch.py                # integration enabled state
select.py                # suggestions/assisted mode
number.py                # safe volume configuration
button.py                # explicit parent commands
diagnostics.py           # redacted support data
services.yaml            # only if actions cannot be modeled as entities
```

## Lifecycle

Each nursery will eventually be represented by a config entry. Setup will
validate referenced entities, build one controller, register entity-state
listeners, and forward setup to the implemented entity platforms. Unload must
remove every listener and timer and unload all platforms cleanly.

Because inputs are Home Assistant state changes rather than a polled external
API, the design should be event-driven. Listener unsubscribe callbacks and
timers should be attached to the config entry with `entry.async_on_unload`.

The foundation temporarily allows one inert config entry. Revisit
`single_config_entry` when the real config flow establishes whether multiple
nurseries are supported in the first functional release.

## Configuration ownership

- Config-entry `data`: stable selections required to construct the integration,
  such as the input sensor, camera, and speaker.
- Config-entry `options`: parent-adjustable behavior such as mode, volume
  limits, debounce, cooldown, and settling durations.
- Runtime state: the controller's current state, timers, acknowledgement, and
  transient recommendations.

Use Home Assistant selectors in the UI and validate entity domains and required
capabilities. Never assume that an entity ID encodes a vendor or model.

## Safety invariants

These invariants must be enforced by the controller rather than only by the UI:

1. Never set volume above the configured hard maximum.
2. Never increase volume without the configured debounce and cooldown checks.
3. Suggestions mode never changes the speaker without an explicit parent
   action.
4. Automated escalation is finite and transitions to attention required.
5. Unavailable or ambiguous inputs fail safe and notify; they do not escalate.
6. Restart recovery must not replay a stale boost command.
7. Disabling or unloading cancels timers and stops further automatic commands.

## Privacy and diagnostics

Processing should remain inside Home Assistant and use the user's existing
integrations. Diagnostics must redact notification targets, media URLs, camera
URLs, snapshot paths, and any credentials. Logs should describe transitions and
error categories without including captured media or notification content.

## Testing strategy

- Full config-flow coverage, including duplicate, invalid, and reconfigure
  paths.
- Table-driven unit tests for every state-machine transition and safety cap.
- Config-entry setup, reload, unload, and restart-recovery tests.
- Entity availability tests for missing and unavailable dependencies.
- Service/action tests that assert the exact media-player and notification
  calls, including negative assertions when safety checks reject an action.

The controller should be deterministic and mostly independent of Home
Assistant so its transition tests remain fast and exhaustive.
