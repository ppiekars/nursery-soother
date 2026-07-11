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
repository. Remove `brands` only after compatible brand assets have been
added. Default HACS inclusion requires validation with no ignores.

## Versioning

Use semantic versions. Keep the version in
`custom_components/nursery_soother/manifest.json` aligned with the published
release. A functional release must have a newer version than the inert
foundation.

Create a matching GitHub release tag such as `v0.2.0`. HACS does not treat a tag
without a published GitHub release as a release. Do not publish the tag until
the exact commit has passed both automated validation and the live Home
Assistant smoke test.

## Functional release contract

Every release in the Suggested MVP line must retain these safety semantics:

- new and migrated entries are disabled by default;
- enable sets baseline and starts only the configured media;
- cry debounce creates a parent suggestion, not a volume increase;
- Boost is explicit, single-stage, and capped;
- persistent crying requests attention without further volume escalation;
- Acknowledge stops escalation while preserving current volume;
- uninterrupted quiet returns to baseline;
- Stop disables and stops integration-owned playback;
- unavailable dependencies fail safe and alert where possible;
- restart begins a fresh episode at baseline and never restores a boost.

Assisted mode and a second boost are not part of this release line. A change to
any of these rules is a product and safety change, not a routine refactor, and
requires updated architecture, user documentation, tests, and release notes.

## Automated release gate

Run from the repository root:

```console
uv sync --locked
uv run pre-commit run --all-files
uv run pytest --cov --cov-report=term-missing
```

Then confirm the GitHub HACS and Hassfest jobs pass on the release commit.

The test suite must cover:

- complete setup, multiple-entry, reconfigure, options, and migration flows;
- safety-critical controller transitions and timer boundaries;
- negative assertions proving no automatic increase in Suggestions mode;
- volume cap enforcement for buttons, notification actions, and automations;
- exact media-player and notification payloads;
- synchronized, idempotent acknowledgement by either parent;
- stale action rejection after settling, Stop, reload, and restart;
- config-entry setup, reload, unload, and listener/timer cleanup;
- disabled startup and enabled baseline recovery;
- missing, unknown, and unavailable cry sensor, camera, speaker, and notify
  targets;
- all standard entities, unique IDs, virtual-device links, and actions;
- diagnostics redaction for every sensitive configuration and runtime field.

Do not waive a failing safety test to publish a release.

## Live Home Assistant smoke test

Use a test nursery or safe test entities first. Do not begin against a sleeping
child or an unverified speaker volume.

### Install and configure

1. Install the exact release commit through a custom HACS repository or copy
   `custom_components/nursery_soother` into the Home Assistant config
   directory.
2. Restart Home Assistant and confirm the integration loads without errors.
3. Add Nursery Soother from **Settings > Devices & services**.
4. Select a cry binary sensor, camera, media player, local audio item, and at
   least two test Companion app notification actions when available.
5. Confirm the entry and all entities exist but the enabled switch is off.
6. Confirm setup and migration did not play media or change volume.

### Validate normal behavior

1. Set a low, safe Baseline, Boost, and Maximum with
   `baseline <= boost <= maximum`.
2. Turn on the integration switch. Confirm the speaker moves to Baseline and
   starts exactly the selected media.
3. Hold the test cry sensor on for less than Debounce, then turn it off.
   Confirm no suggestion or boost occurs.
4. Hold it on beyond Debounce. Confirm all configured parents receive the same
   current-episode notification and the speaker remains at Baseline.
5. Press Boost on one phone. Confirm one capped boost occurs and the Home
   Assistant state updates for both parents.
6. Repeat with a Maximum below the requested Boost through a deliberately
   malformed test fixture, not through production configuration. Confirm the
   controller never requests a volume above Maximum.
7. Leave cry on beyond Escalation. Confirm Attention required becomes true and
   there is no second volume increase.
8. Acknowledge from either phone. Confirm escalation stops, shared state
   updates, and current volume is unchanged.
9. Turn cry off briefly, then back on before Settling expires. Confirm baseline
   is not applied early.
10. Turn cry off for the full Settling duration. Confirm the speaker returns to
    Baseline and stale notification actions no longer work.
11. Press Baseline during a boost. Confirm immediate baseline while the switch
    stays on.
12. Press Stop. Confirm playback stops, timers are canceled, and the switch is
    off.

### Validate failures and recovery

1. Repeat a pending episode while making each selected dependency unavailable
   in turn. Confirm there is no boost and the remaining local or mobile
   surfaces report the problem.
2. Make one notification target fail. Confirm other targets are still
   attempted.
3. Reload the config entry during Cry pending and during Boost. Confirm no old
   timer or notification action changes the new controller.
4. Restart Home Assistant during Boost. Confirm the old boost is not replayed;
   an enabled entry starts fresh at Baseline.
5. Disable the entry, restart again, and confirm no media or volume command is
   issued.
6. Download diagnostics and inspect them manually for media identifiers,
   camera URLs, notification targets, action tokens, webhook IDs, paths, or
   credentials. None may be present.
7. Check Home Assistant logs for uncaught exceptions, repeated retry loops,
   media or notification contents, and secret values.
8. Unload and remove the entry. Confirm all Nursery Soother entities,
   listeners, and timers are gone and no later callback runs.

## Upgrade and migration gate

Test upgrade from every previously published config-entry version.

For the inert foundation migration specifically:

1. create or restore an old entry containing only cry sensor, camera, and media
   player selections;
2. install the functional release and restart;
3. confirm the migrated entry is disabled;
4. confirm no speaker or notification action was called;
5. reconfigure the entry with white-noise media and notification targets;
6. confirm all conservative defaults are present and valid;
7. explicitly enable only after reviewing those values.

A migration must never infer a media source, notification target, enabled
state, old episode, or old volume command.

## Documentation gate

Before tagging, compare the implementation with:

- `README.md` setup, defaults, state semantics, entities, limitations, and
  troubleshooting;
- `docs/architecture.md` lifecycle and safety invariants;
- `examples/dashboard.yaml` entity types and current dashboard action syntax;
- `product_pitch.md` so deferred features remain clearly labeled rather than
  accidentally advertised as implemented.

Release notes must highlight configuration migrations and any change that can
start, stop, or alter speaker playback. Include the reminder that Nursery
Soother is not a medical device or substitute for adult supervision.

## Publish and post-release verification

1. Merge or fast-forward the verified commit to the release branch.
2. Create the signed or annotated version tag.
3. Publish a GitHub release containing user-visible changes, migration notes,
   supported Home Assistant version, and known limitations.
4. Install the published artifact through HACS in a clean Home Assistant test
   instance; do not rely only on a working-tree copy.
5. Repeat disabled-startup, enable, explicit Boost, Acknowledge, quiet return,
   Stop, and restart checks.
6. Confirm HACS reports the expected installed version and future updates.
7. Monitor the issue tracker and logs from the test instance before promoting
   the release more broadly.
