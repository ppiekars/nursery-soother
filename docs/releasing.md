# HACS and release checklist

## Repository readiness

HACS expects a public GitHub repository containing exactly one integration
under `custom_components`, a root `hacs.json`, a useful README, and a custom
integration manifest with a version, documentation URL, issue tracker, and code
owner.

Before the first push intended for HACS testing:

- set the GitHub repository description;
- add topics such as `home-assistant`, `hacs`, `custom-integration`, and
  `nursery`;
- keep GitHub Issues enabled;
- run the local checks in [CONTRIBUTING.md](../CONTRIBUTING.md).

The HACS workflow temporarily ignores `brands`, `description`, and `topics` so
the code scaffold can be validated before remote metadata and artwork exist.
Remove `description` and `topics` from the ignore list after configuring the
repository. Remove `brands` only after acceptable brand assets have been added
in the form expected by Home Assistant/HACS. Default HACS inclusion requires
the validation action to pass with no ignores.

## Versioning

Use semantic versions. Keep the version in
`custom_components/nursery_soother/manifest.json` aligned with the release. Use
a matching GitHub release tag such as `v0.1.0`; a tag without a published GitHub
release is not treated as a release by HACS.

## Foundation smoke test

1. Push the repository metadata and scaffold.
2. Add the repository to HACS as a custom **Integration** repository.
3. Download it and restart Home Assistant.
4. Add Nursery Soother from **Settings > Devices & services**.
5. Confirm the config entry is loaded.
6. Confirm that no device, entity, action, listener-driven behavior, playback,
   or notification is created by this foundation release.
7. Reload and then delete the config entry to verify lifecycle cleanup.

## First functional release gate

Do not call a release functional until it includes tests for its safety
invariants, documented configuration and entities, restart behavior, unload
behavior, diagnostics redaction, and failure handling for unavailable source
entities.
