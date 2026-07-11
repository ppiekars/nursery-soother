# Nursery Soother

Nursery Soother is a planned Home Assistant custom integration that will
coordinate an existing cry-detection sensor, camera, and media player into a
conservative nursery response workflow.

> [!IMPORTANT]
> This repository currently contains an **inert configuration release**. It can
> store selections for a cry-detection sensor, camera, and media player, but it
> creates no entities, registers no actions, listens to no sensors, and never
> controls a speaker or sends a notification.

Nursery Soother is not a medical device, baby monitor, or substitute for direct
adult supervision. Future automation must always be treated as an aid, never as
a safety system.

## Current scope

The current configuration slice includes:

- the HACS-compatible `custom_components/nursery_soother` layout;
- a translated config flow with domain-filtered selectors for one cry-detection
  binary sensor, camera, and media player;
- config-entry storage and setup-time domain validation for those selections;
- unload-safe config-entry lifecycle hooks;
- HACS and Hassfest validation workflows;
- pre-commit file, Ruff, and lockfile checks plus pre-push tests;
- architectural, development, installation, and release documentation;
- smoke tests for setup, reload, unload, and the config flow.

The intended product and later MVP are described in
[product_pitch.md](product_pitch.md). The technical boundaries for future work
are recorded in [docs/architecture.md](docs/architecture.md).

## Install the foundation with HACS

The repository is not yet listed in HACS by default. Add it as a custom
repository:

1. Open HACS in Home Assistant.
2. Open the three-dot menu and choose **Custom repositories**.
3. Enter `https://github.com/ppiekars/nursery-soother`.
4. Select **Integration** as the type, then choose **Add**.
5. Find **Nursery Soother** in HACS and download it.
6. Restart Home Assistant.
7. Go to **Settings > Devices & services**, choose **Add integration**, and
   search for **Nursery Soother**.
8. Select the cry-detection sensor, camera, and media player, then submit the
   setup form.

The expected result is one loaded config entry and no devices, entities, or
actions. That confirms the install path without affecting the nursery.

## Manual installation

Copy `custom_components/nursery_soother` into the `custom_components` directory
inside the Home Assistant configuration directory, restart Home Assistant, and
then add Nursery Soother from **Settings > Devices & services**.

The resulting path must be:

```text
<home-assistant-config>/custom_components/nursery_soother/manifest.json
```

## Removal

1. Remove the Nursery Soother config entry from **Settings > Devices &
   services**.
2. Remove the repository from HACS.
3. Restart Home Assistant.

## Development

See [docs/releasing.md](docs/releasing.md) before publishing a release.

## License

[MIT](LICENSE)
