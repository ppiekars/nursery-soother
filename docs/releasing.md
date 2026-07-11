# HACS and release checklist

## Repository readiness

HACS expects a public GitHub repository containing exactly one integration
under `custom_components`, a root `hacs.json`, a useful README, and a custom
integration manifest with a version, documentation URL, issue tracker, and code
owner.

Before publishing a release:

- set the GitHub repository description;
- add topics such as `home-assistant`, `hacs`, `custom-integration`, and
  `nursery`;
- keep GitHub Issues enabled;
- confirm the supported Home Assistant version in a clean test instance;
- run all local checks and review the GitHub validation workflows.

The HACS workflow temporarily ignores `brands`, `description`, and `topics`.
Remove `description` and `topics` from the ignore list after configuring the
repository. Remove `brands` only after compatible brand assets have been added.
Default HACS inclusion requires validation with no ignores.

## Versioning

Use semantic versions. Keep the version in
`custom_components/nursery_soother/manifest.json` aligned with the published
release and project metadata.

Create a matching GitHub release tag such as `v0.3.0`. HACS does not treat a
tag without a published GitHub release as a release. Do not publish the tag
until the exact commit has passed automated validation and the live Home
Assistant smoke test.

## Level-based functional release contract

Every release in this product line retains these safety semantics:

- new entries start at Standby with Automatic operation off;
- while already in Standby, physical cry events and the simulated event command
  are no-ops, playback is not changed, and Automatic operation cannot leave it;
- one exact select owns Standby, Baseline, and Levels 1–4;
- the former Boost, Return to baseline, Acknowledge, Stop, and enabled-switch
  controls do not reappear under different names;
- Baseline and Levels 1–4 have monotonic configured volumes, all bounded by
  Maximum;
- the current single media item is represented by a per-level sound map even
  while all active levels point to the same MP3;
- off-to-on cry transitions are event samples; one short falling edge is not
  treated as a settled child;
- confirmation uses three events or ten cumulative active seconds in a
  30-second window after the configured debounce, which defaults to ten
  seconds;
- a 60-second no-event gap closes the active cry episode;
- manual mode recommends one exact next level and never changes it;
- Automatic operation changes upward only, exactly one level per decision;
- each subsequent automatic increase waits at least 30 seconds and requires
  fresh post-change evidence;
- both modes step down one level after each uninterrupted 120-second quiet
  interval and stop downshifting at Baseline;
- a fixed 150-second deadline from episode confirmation enters Standby and
  requests caregiver attention if the episode remains active;
- unavailable dependencies and playback takeover fail safe;
- restart discards transient evidence, timers, and notification action IDs;
- no action can exceed Level 4, Maximum volume, or alter media the integration
  no longer owns.

Changing any of these rules is a product and safety change, not a routine
refactor. Update architecture, user documentation, tests, dashboard examples,
and release notes together.

## Automated release gate

Run from the repository root:

```console
uv sync --locked
uv run pre-commit run --all-files
uv run pytest --cov --cov-report=term-missing
```

Then confirm the GitHub HACS and Hassfest jobs pass on the release commit.

The test suite must cover:

- complete setup, multiple-entry, reconfigure, options, and development schema
  replacement flows;
- exact level selection and Standby side effects;
- all six volume entities and monotonic/Maximum validation;
- rising-edge event counting and duplicate-state suppression;
- rolling 30-second evidence expiry;
- three-event and ten-active-second confirmation paths;
- the default ten-second confirmation debounce and 60-second event gap;
- manual exact-level suggestion without any volume action;
- automatic one-level increase, 30-second dwell, fresh-evidence boundary, and
  Level 4 ceiling;
- one-level quiet downshift every 120 seconds in both modes;
- attention at 150 seconds from confirmation, including cancellation on event
  gap and Standby at expiry;
- exact media-player and notification payloads;
- same-media mapping for all active levels and per-level-ready resolution;
- stale notification rejection after level selection, episode close, Standby,
  reload, and restart;
- config-entry setup, reload, unload, and listener/timer cleanup;
- missing, unknown, and unavailable cry sensor, camera, speaker, and notify
  targets;
- playback takeover, explicit same-ID user replay, and signed local-media URL
  ownership matching;
- the artificial event button using the real evidence policy;
- all standard entities, unique IDs, virtual-device links, and actions;
- diagnostics redaction for every sensitive configuration and runtime field.

Do not waive a failing safety test to publish a release.

## Live Home Assistant smoke test

Use a test nursery, low volumes, or safe test entities first. Do not begin
against a sleeping child or an unverified speaker.

### Install and configure

1. Install the exact release commit through a custom HACS repository or copy
   `custom_components/nursery_soother` into the Home Assistant config
   directory.
2. Restart Home Assistant and confirm the integration loads without errors.
3. Add Nursery Soother from **Settings > Devices & services**.
4. Select a cry binary sensor, camera, media player, local sound, and test
   Companion app notification actions.
5. Confirm the level select, Automatic operation switch, state and
   recommendation sensors, attention binary sensor, six numbers, and Simulate
   cry event button exist.
6. Confirm the level is Standby and Automatic operation is off.
7. Confirm setup did not play media or change volume.

### Validate exact level and volume behavior

1. Set deliberately low Baseline and Level 1–4 volumes with
   `baseline <= L1 <= L2 <= L3 <= L4 <= maximum`.
2. Select Baseline. Confirm the speaker moves to the exact configured volume
   and starts the selected media.
3. Select each of Levels 1–4 in a non-linear order. Confirm each command uses
   that exact level's volume and does not behave like an increment button.
4. Confirm every active level currently resolves to the same selected MP3.
5. Lower Maximum through a deliberately malformed test fixture, not production
   options, and confirm no command exceeds it.
6. Select Standby. Confirm integration-owned playback stops or safely pauses,
   pending timers are canceled, and Automatic operation does not change the
   level.
7. Generate physical cry events and press Simulate cry event while in Standby.
   Confirm both are no-ops and cannot start a session even with Automatic
   operation on.
8. Select Baseline, then start different media manually. Confirm Nursery
   Soother relinquishes it, moves the visible level to Standby, and does not
   stop the replacement. From that visible Standby state, select an active
   level directly and confirm a fresh owned session starts without an extra
   Standby selection.
9. Explicitly replay the configured sound from a user context. Confirm Nursery
   Soother still relinquishes ownership and moves to visible Standby even when
   the raw media ID is unchanged.

### Validate pulse evidence in manual mode

1. Select Baseline and leave Automatic operation off.
2. Produce one short cry pulse. Confirm it is recorded as one event but does
   not immediately change the level.
3. Produce three off-to-on pulses inside 30 seconds. With debounce configured
   to its ten-second default, confirm no earlier decision and then a manual
   suggestion targeting Level 1.
4. Repeat using fewer than three pulses whose cumulative on-time reaches ten
   seconds. Confirm the alternate OR threshold qualifies.
5. Space events so earlier evidence leaves the 30-second window. Confirm old
   evidence does not qualify.
6. Confirm the notification explains aggregate evidence and offers an exact
   next-level response rather than Boost or Acknowledge.
7. Select the suggested level from one phone. Confirm the exact level applies,
   shared Home Assistant state updates, and stale actions no longer work.
8. Ignore a new manual suggestion and confirm speaker volume remains unchanged.

### Validate automatic operation

1. Select Baseline again, turn on Automatic operation, and create qualifying
   evidence. Confirm exactly one increase to Level 1.
2. Continue generating events used by the original evidence and confirm the
   controller does not immediately move again.
3. Before 30 seconds of dwell, add fresh evidence and confirm no early increase.
4. After dwell, add a newly qualifying evidence pattern and confirm exactly one
   increase to Level 2.
5. Repeat to Level 4. Confirm it never skips a level and never creates a level
   above Level 4.
6. Turn Automatic operation off during an episode. Confirm future evidence
   becomes an exact manual suggestion and does not change level.
7. Manually select a level during automatic operation. Confirm this establishes
   a fresh evidence boundary and older events cannot override it.

### Validate quiet and attention safety

1. From an active response level, stop producing events for less than 120
   seconds, then produce one. Confirm the quiet timer cancels and no downshift
   occurs.
2. Allow 120 seconds of uninterrupted quiet. Confirm exactly one downshift.
3. Keep quiet for successive intervals and confirm one step each time, stopping
   at Baseline rather than entering Standby.
4. Repeat at least one downshift with Automatic operation off to prove quiet
   behavior is mode-independent.
5. Confirm a cry episode and keep it active. Change levels manually or
   automatically and confirm the original 150-second attention deadline is not
   extended.
6. Before that deadline in a separate episode, allow the 60-second event gap to
   expire. Confirm the attention timer is canceled.
7. Keep another episode active through 150 seconds. Confirm the integration
   enters Standby, stops owned playback, marks Attention required, and notifies
   caregivers regardless of the current level or mode.

### Validate simulation, failures, and recovery

1. Press Simulate cry event after accepting the dashboard confirmation. Confirm
   one artificial event is added and clearly identified as a test.
2. Press it enough times to qualify the count threshold and confirm it uses the
   same manual or automatic path as physical events.
3. Repeat a pending episode while making each selected dependency unavailable
   in turn. Confirm there is no upward level change and remaining surfaces
   report the problem.
4. Make one notification target fail. Confirm other targets are still
   attempted.
5. Reload the config entry during Cry pending and Responding. Confirm no old
   timer, evidence, or notification action changes the new controller.
6. Restart Home Assistant during an episode. Confirm evidence and deadlines are
   not replayed and no automatic increase occurs from stale state.
7. Leave the entry at Standby, restart, and confirm no media or volume command
   is issued.
8. Download diagnostics and inspect them for media IDs, camera URLs,
   notification targets, event/action tokens, webhook IDs, paths, or
   credentials. None may be present.
9. Check logs for uncaught exceptions, retry loops, raw media/notification
   content, and secrets.
10. Unload and remove the entry. Confirm all entities, listeners, and timers are
    gone and no later callback runs.

## Development schema replacement gate

Backward compatibility with the unpublished Boost/Baseline design is not
required. Do not add aliases, hidden legacy entities, or unsafe migrations just
to retain it.

Before releasing the level model:

1. install the previous development version in a disposable test instance;
2. record that removal/reintegration is required in the release notes when the
   entry cannot be migrated unambiguously;
3. remove the old entry and integration artifacts;
4. restart Home Assistant and confirm old entities are gone;
5. install the new release and create a fresh entry;
6. confirm Standby, Automatic off, conservative volumes, and new timing defaults;
7. confirm no old Boost, Baseline, Acknowledge, Stop, cooldown, or enabled
   entity remains in the device or entity registry;
8. explicitly select an active level only after reviewing speaker volumes.

Any compatibility code that is retained must still map unknown old state to
Standby and Automatic off. It must never infer an active level, media action,
event evidence, or caregiver response.

## Documentation gate

Before tagging, compare the implementation with:

- `README.md` level semantics, event evidence, defaults, entities,
  notifications, safety, limitations, and troubleshooting;
- `docs/architecture.md` evidence generations, timers, side-effect guards,
  ownership, and attention invariants;
- `examples/dashboard.yaml` level select, Automatic operation switch, six
  numbers, status entities, and confirmed artificial-event action;
- `product_pitch.md` so per-level sounds and other deferred features are not
  accidentally advertised as implemented.

Search the release tree for obsolete user-facing terms such as `Boost`,
`Return to baseline`, `Acknowledge`, boost cooldown, and an enabled switch.
Historical release notes may retain those words; current product surfaces may
not.

Release notes must highlight:

- removal of the old development-only controls;
- any required remove-and-reintegrate step;
- the six exact levels and Automatic operation default off;
- five level volumes plus Maximum;
- the pulse-event confirmation rule and timing defaults;
- gradual quiet downshift and the 150-second Standby/attention cutoff;
- the single MP3 currently reused by every active level;
- any change that can start, stop, or alter speaker playback;
- the reminder that Nursery Soother is not a medical device or substitute for
  adult supervision.

## Publish and post-release verification

1. Merge or fast-forward the verified commit to the release branch.
2. Create the signed or annotated version tag.
3. Publish a GitHub release containing user-visible changes, reintegration
   instructions, supported Home Assistant version, and known limitations.
4. Install the published artifact through HACS in a clean Home Assistant test
   instance; do not rely only on a working-tree copy.
5. Repeat Standby startup, exact-level selection, manual suggestion, one-step
   automatic increase, quiet downshift, attention cutoff, and restart checks.
6. Confirm HACS reports the expected installed version and future updates.
7. Monitor the issue tracker and test-instance logs before promoting the
   release more broadly.
