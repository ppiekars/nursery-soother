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
release and project metadata. Whenever
`frontend/nursery-soother-card.js` changes, increment `CARD_MODULE_VERSION` in
`custom_components/nursery_soother/frontend.py` so browsers request the new
module after the update.

Create a matching GitHub release tag such as `v0.7.0`. HACS does not treat a
tag without a published GitHub release as a release. Do not publish the tag
until the exact commit has passed automated validation and the live Home
Assistant smoke test.

## Level-based functional release contract

Every release in this product line retains these safety semantics:

- new entries start at Standby with Automatic operation and Level lock off;
- while already in Standby, physical cry events and the simulated event command
  are no-ops, playback is not changed, and Automatic operation cannot leave it;
- one exact select owns Standby, Baseline, and Levels 1–4;
- the former Boost, Return to baseline, Acknowledge, Stop, and enabled-switch
  controls do not reappear under different names;
- Baseline and Levels 1–4 have monotonic configured volumes, all bounded by
  Maximum;
- Baseline and Levels 1–4 each map to a selected local audio item, with reuse of
  the same item allowed;
- compatible Sonos players use one queued item with crossfade and repeat-all;
  the queue is not periodically repopulated, while an owned idle state rebuilds
  it with a freshly resolved Local Media URL;
- Sonos Local Media calls use the `music` media type, refresh cached crossfade
  state around toggles, and wait for inactive state after stop or pause;
- Sonos loop setup replaces the existing queue; normal stop restores captured
  repeat and crossfade values, while external takeover leaves the replacement
  playback, queue, repeat, and crossfade untouched until a caregiver explicitly
  selects an active level from Standby to authorize a fresh session;
- disabling repeat-all or crossfade during an owned loop fails safe to Standby
  and caregiver attention;
- other media players retain direct playback and owned-idle recovery without
  queue, repeat, or crossfade changes;
- off-to-on cry transitions are event samples; one short falling edge is not
  treated as a settled child;
- manual mode sends its exact-level suggestion on the first event, while
  automatic initial confirmation uses two events or eight cumulative active
  seconds in a 30-second window after the configured debounce, which defaults
  to eight seconds;
- in automatic mode at Baseline, the first event applies Level 1 provisionally;
  confirmation retains it, while a lone unconfirmed event returns to Baseline
  after the separate 25-second provisional timeout without notification or
  further escalation;
- a 60-second no-event gap closes the active cry episode;
- manual mode immediately recommends one exact next level on the first event
  and never changes it;
- Automatic operation changes upward only, exactly one level per decision;
- Level lock prevents provisional increases, automatic increases, and quiet
  downshifts while leaving exact parent selections and Standby available;
- each subsequent response decision waits at least 20 seconds and requires one
  fresh event or six fresh active seconds; automatic mode applies the next
  level while manual mode only suggests it;
- both modes step down one level after each uninterrupted 120-second quiet
  interval and stop downshifting at Baseline;
- a 150-second base deadline from the first manual alert or automatic
  confirmation enters Standby and requests caregiver attention if the episode
  remains active, including while Level lock is on; one already-pending event
  gap may receive a bounded, non-renewable grace period;
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
node --check custom_components/nursery_soother/frontend/nursery-soother-card.js
node --test tests/frontend/nursery-soother-card.test.mjs
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
- immediate one-event manual notification plus the two-event and
  eight-active-second automatic initial confirmation paths;
- immediate provisional Baseline-to-Level-1 response, confirmed retention, and
  unconfirmed rollback after the separate 25-second timeout;
- one-event and six-active-second continuing-stage paths;
- the default eight-second automatic confirmation debounce and 60-second event
  gap;
- immediate manual exact-level suggestion without any volume action;
- automatic one-level increase, 20-second dwell, fresh-evidence boundary, and
  Level 4 ceiling;
- Level lock persistence, provisional-response handling, blocked increases,
  deferred quiet downshift, manual override, and attention safety override;
- one-level quiet downshift every 120 seconds in both modes;
- the 150-second base attention deadline, including cancellation on event gap,
  one bounded pending-gap grace period, and Standby when still unresolved;
- exact media-player and notification payloads;
- distinct and reused per-level media mappings and exact-level resolution;
- compatible Sonos one-item queue, repeat-all and crossfade setup, no periodic
  repopulation, fresh-URL idle recovery, queue replacement, normal restoration,
  external-takeover restraint, and generic-player fallback;
- stale notification rejection after level selection, episode close, Standby,
  reload, and restart;
- config-entry setup, reload, unload, and listener/timer cleanup;
- one-time frontend registration, static card URL, and extra-module loading;
- card form filters, native action mapping, exact-next recommendation guards,
  camera fallback/refresh/cancellation, signed snapshot URLs, and attention
  semantics;
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
4. Select a cry binary sensor, camera, media player, one local sound for each
   active level, and test Companion app notification actions.
5. Confirm the level select, Automatic operation and Level lock switches, state
   and recommendation sensors, attention binary sensor, six numbers, and
   Simulate cry event button exist.
6. Confirm the level is Standby and Automatic operation is off.
7. Confirm setup did not play media or change volume.

### Validate the optional dashboard card

1. On Home Assistant 2026.7 or newer, confirm the browser loads
   `/nursery_soother/nursery-soother-card.js` without a JavaScript or network
   error and without a manually configured dashboard resource.
2. Choose **Add card > Nursery Soother** and confirm the visual editor exposes
   `camera_entity`, `level_entity`, `automatic_entity`, `lock_entity`,
   `state_entity`, `recommendation_entity`, `attention_entity`, and the optional
   `camera_view` choice (`live`, `auto`, or `image`).
3. Select the configured camera plus the six entities from the Nursery Soother
   device. Save, reopen the editor, and confirm every selection and camera mode
   round-trips.
4. Exercise all six exact level buttons and the Auto and Lock controls. Confirm
   they call the corresponding native select or switch action and reflect
   entity-state updates from elsewhere in Home Assistant.
5. Produce an `increase_level` recommendation with a valid `suggested_level`.
   Confirm **Set** selects exactly that level and stale or malformed
   recommendations do not issue an action.
6. Exercise `live`, `auto`, and `image` camera modes. Confirm the camera area
   opens the standard camera more-info dialog.
7. Trigger Attention required and confirm the attention banner appears.
   Press **Attend** and verify it only opens the camera more-info dialog: it
   must not clear attention, change level, or perform an Acknowledge action.
8. Rename one referenced entity, update its card field through the visual
   editor, and confirm the card recovers without relying on a default entity
   ID.
9. Replace the custom card with the native fallback in
   `examples/dashboard.yaml`. Confirm camera, level, switches, status, and
   attention remain usable without the custom card.

### Validate exact level and volume behavior

1. Set deliberately low Baseline and Level 1–4 volumes with
   `baseline <= L1 <= L2 <= L3 <= L4 <= maximum`.
2. Select Baseline. Confirm the speaker moves to the exact configured volume
   and starts the selected media.
3. Select each of Levels 1–4 in a non-linear order. Confirm each command uses
   that exact level's volume and does not behave like an increment button.
4. Configure at least two distinct tracks and reuse one track for at least two
   levels. Confirm each level resolves its exact mapping and playback restarts
   only when the source changes.
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

### Validate Sonos continuity and fallback

1. On a compatible Sonos player, note its current queue, repeat mode,
   crossfade state, and group membership. Use a short, loop-ready Local Media
   track and a deliberately low test volume.
2. Select an active level. Confirm Nursery Soother replaces the queue with
   exactly one item, enables crossfade, and selects repeat-all.
3. Listen through a track boundary. Confirm Sonos loops it cleanly without a
   second queue item or a new Home Assistant play action at the boundary.
4. Force the still-owned player to idle. Confirm Nursery Soother rebuilds the
   one-item queue and Home Assistant resolves the Local Media source again.
   Record that time-limited Local Media URLs prevent a forever-playback
   guarantee; a stop at URL expiry can introduce a recovery gap.
5. Select Standby. Confirm the owned queue is cleared and the repeat and
   crossfade values from step 1 are restored. If group membership is changed
   during the session, confirm those settings are left untouched instead.
   Confirm the original queue items are not restored.
6. Select an active level again, then start replacement media from another
   source. Confirm Nursery Soother enters visible Standby without stopping
   playback or changing the live queue, repeat mode, or crossfade state.
7. Repeat the active-level and idle-recovery checks with a non-Sonos player, or
   a fixture missing one required Sonos feature. Confirm it uses direct media
   playback and never calls queue, repeat, or crossfade actions.

### Validate pulse evidence in manual mode

1. Select Baseline and leave Automatic operation off.
2. Produce one short cry pulse. Confirm it immediately sends a manual
   suggestion targeting Level 1 but does not change the selected level or
   speaker volume.
3. Confirm the notification reports one event and does not wait for the
   automatic confirmation debounce.
4. Before the 20-second dwell expires, produce another pulse and confirm it
   does not create another response decision.
5. After the dwell, produce one fresh pulse and confirm a later manual
   suggestion can replace the tagged notification.
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
3. Before 20 seconds of dwell, add one fresh event and confirm no early increase.
4. After dwell, confirm that the fresh event authorizes exactly one increase to
   Level 2. Repeat with a held input to cover the six-active-second path.
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
7. Stop events shortly before the base deadline so the 60-second event gap
   would finish just afterward. Confirm attention waits for that pending gap
   and the episode resolves quietly.
8. Repeat, but produce a fresh event during the grace period. Confirm the grace
   is not extended again and the integration enters Standby, stops owned
   playback, marks Attention required, and notifies caregivers regardless of
   the current level or mode.

### Validate simulation, failures, and recovery

1. Press Simulate cry event after accepting the dashboard confirmation. Confirm
   one artificial event is added and clearly identified as a test. In manual
   mode, confirm that press immediately sends the exact-level suggestion. In
   automatic mode at Baseline, confirm it immediately starts the same
   provisional Level 1 response and returns after 25 seconds if left
   unconfirmed.
2. In automatic mode, press it twice to qualify the initial count threshold.
   After the first response, verify that one fresh press can qualify the next
   decision only after the post-response dwell.
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
  notifications, dashboard-card configuration and fallback, safety,
  Sonos continuity and queue replacement, limitations, and troubleshooting;
- `docs/architecture.md` evidence generations, timers, side-effect guards,
  ownership, Sonos native-loop ownership, attention invariants, and frontend
  registration boundary;
- `examples/dashboard.yaml` optional custom card, all seven entity references,
  camera mode, rename guidance, native-card fallback, six numbers, and
  confirmed artificial-event action;
- `product_pitch.md` so implemented per-level sounds and remaining deferred
  features are described accurately.

Search the release tree for obsolete user-facing terms such as `Boost`,
`Return to baseline`, `Acknowledge`, boost cooldown, and an enabled switch.
Historical release notes may retain those words; current product surfaces may
not.

Release notes must highlight:

- removal of the old development-only controls;
- any required remove-and-reintegrate step;
- the six exact levels and Automatic operation default off;
- Level lock default off and its parent-control and safety overrides;
- five level volumes plus Maximum;
- the immediate first-event manual alert and automatic pulse-confirmation
  timing defaults;
- gradual quiet downshift and the bounded 150-second-base attention cutoff;
- the independent Local Media selection for every active level;
- the Sonos one-item crossfade/repeat-all optimization, queue replacement, and
  generic-player fallback;
- the optional auto-loaded dashboard card and its supported Home Assistant
  version;
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
