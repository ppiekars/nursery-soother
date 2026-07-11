# Nursery Soother

Nursery Soother is a Home Assistant custom integration that coordinates an
existing cry-detection sensor, camera, and media player into a conservative
nursery response workflow.

It starts locally hosted white noise at a configured baseline volume, waits for
continuous cry detection, and asks a parent before applying one temporary,
capped volume boost. It never raises the volume automatically.

> [!IMPORTANT]
> Nursery Soother is not a medical device, baby monitor, or substitute for
> direct adult supervision. A camera, network, speaker, phone, notification
> service, or Home Assistant itself can fail. Treat every notification and
> control as a convenience, not a safety system.

## Suggested MVP behavior

The functional MVP implements **Suggestions mode** with one temporary boost:

```text
Disabled by default
    ↓ parent enables Nursery Soother
Set baseline volume and start configured white noise
    ↓ cry remains on for the debounce period
Notify all configured parents with response controls
    ↓ parent explicitly chooses Boost
Set the configured boost volume, never above the hard maximum
    ↓ cry remains on for the escalation period
Mark attention required; never increase again automatically
    ↓ cry remains off for the full settling period
Return to baseline
```

The following rules are deliberate:

- A new or migrated entry is disabled. Installation and upgrade cannot start
  playback or change speaker volume by themselves.
- Enabling is an explicit parent action. It sets the baseline volume and starts
  the selected white-noise media.
- Cry detection must remain on for the configured debounce period before an
  actionable notification is sent.
- Only a parent pressing **Boost** can increase volume. The requested boost is
  capped by the configured maximum.
- Continued crying eventually changes the integration to **Attention
  required**. There is no second boost and no automatic volume escalation.
- **Acknowledge** stops escalation and synchronizes the response across
  parents, but deliberately keeps the current speaker volume.
- When the cry sensor remains off for the complete settling period, the
  integration returns the speaker to baseline automatically. A new cry during
  that period cancels the return timer.
- **Baseline** returns to baseline immediately without disabling the
  integration. **Stop** disables the integration and stops playback, or pauses
  when that is the player's only safe halt action.
- Missing, unknown, or unavailable dependencies fail safe. The integration
  does not boost and reports the problem through the remaining available
  notification and Home Assistant surfaces.
- After a Home Assistant restart, an enabled entry begins a fresh episode at
  baseline. A boost or notification action from before the restart is never
  replayed.

## Requirements

Before setup, make these available in Home Assistant:

- Home Assistant 2026.7.0 or newer;
- a `binary_sensor` whose on state means crying is detected;
- a `camera` for the parent-facing notification and dashboard view;
- a `media_player` that supports setting volume, playing the selected media,
  and stopping or pausing playback;
- an audio item available from Home Assistant's Media browser, preferably from
  [local media](https://www.home-assistant.io/integrations/media_source/#local-media);
- one or more Home Assistant Companion app notification actions named
  `notify.mobile_app_*`.

The integration is device-agnostic. Reolink and Sonos are useful examples, but
no vendor-specific API or SDK is required.

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
4. white-noise audio from the Media browser;
5. one or more Companion app notification targets.

After setup, the entry remains disabled. Review the behavior options before
turning on the Nursery Soother switch.

Each cry sensor, camera, and speaker can belong to only one Nursery Soother
entry, preventing two controllers from competing for the same nursery device.

The default settings are intentionally conservative:

| Setting | Default | Meaning |
| --- | ---: | --- |
| Baseline volume | 20% | Normal white-noise volume |
| Boost volume | 30% | Target of an explicit Boost action |
| Maximum volume | 40% | Hard ceiling for integration-issued volume commands |
| Cry debounce | 15 seconds | Required uninterrupted cry before a suggestion |
| Escalation | 120 seconds | Sustained cry before attention is required |
| Settling | 120 seconds | Required uninterrupted quiet before baseline |
| Boost cooldown | 60 seconds | Rate-limits reboost after an automatic baseline return |

Volume settings must satisfy:

```text
0% <= baseline <= boost <= maximum <= 100%
```

Use the config entry's **Configure** action to change volumes and timing. Use
**Reconfigure** to replace the cry sensor, camera, speaker, media source, or
notification targets. Both flows preserve the current enabled choice; neither
turns a disabled entry on automatically. If the entry is already enabled, its
safe reload begins again at baseline.

## Entities and standard actions

Nursery Soother creates normal Home Assistant entities. Their generated entity
IDs depend on the installation and can be renamed without changing their
behavior, so automations should select the entities from the UI rather than
copy an assumed ID.

| Entity type | Purpose | Standard Home Assistant action |
| --- | --- | --- |
| `switch` | Enable or disable the response loop | `switch.turn_on`, `switch.turn_off` |
| `button` | Inject one finite cry event for testing | `button.press` |
| `sensor` | Current policy state | Read-only |
| `sensor` | Current recommendation | Read-only |
| `binary_sensor` | Whether parent attention is required | Read-only |
| `number` | Baseline volume percentage | `number.set_value` |
| `number` | Boost volume percentage | `number.set_value` |
| `number` | Maximum volume percentage | `number.set_value` |
| `button` | Apply the one explicit boost | `button.press` |
| `button` | Return to baseline | `button.press` |
| `button` | Acknowledge the current episode | `button.press` |
| `button` | Stop or pause playback and disable the integration | `button.press` |

The state sensor uses the values `disabled`, `baseline`, `cry_pending`,
`boost`, `attention_required`, and `settling`. Button presses and notification
responses all call the same controller, so the volume cap and state checks are
enforced consistently.

Press **Simulate cry event** to inject one finite test detection without
changing the real cry sensor. It passes through the configured debounce, sends
the normal actionable notification clearly marked as a test, and then releases
automatically into settling. Repeated presses while that event is active are
coalesced, and Stop always cancels it. A successful explicit Baseline or a
fresh enabled session also clears any prior Boost cooldown, so a parent control
is never rejected because of an older session.

## Parent notifications

After a debounced cry, every configured parent receives the same episode-scoped
notification with Boost, Baseline, and Acknowledge. An Attention required
notification instead offers Acknowledge, Baseline, and Stop. Tapping the
notification itself opens the configured camera. Accepted responses update the
shared Home Assistant state, clear the notification from each configured
phone, and actions from an earlier completed episode are ignored.

Notification presentation differs between iOS and Android, and the operating
system can limit how many buttons are visible. See the official Companion app
documentation for [actionable
notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications/).

## Dashboard

Nursery Soother uses native entities, so no custom dashboard card is required.
A starting view is available in
[`examples/dashboard.yaml`](examples/dashboard.yaml). Before pasting it into a
manual dashboard, replace every sample entity ID with the IDs shown on the
Nursery Soother device page and replace the sample camera entity.

## Siri and Apple Shortcuts

No Nursery Soother-specific Siri bridge is needed. Apple Shortcuts can use the
Home Assistant Companion app's standard **Press button**, **Control switch**,
or **Perform action** actions. For example, a “Nursery boost” shortcut can
press the integration's Boost button entity, while “Stop nursery soother” can
press Stop or turn off its switch.

The same switch and button entities can also be exposed through HomeKit Bridge.
See Home Assistant's current [Apple App Intents and Siri Shortcuts
guide](https://companion.home-assistant.io/docs/integrations/siri-shortcuts/).

## Privacy and diagnostics

The response policy runs inside Home Assistant. Nursery Soother does not upload
camera media, implement its own cloud service, or contact a device vendor. The
selected camera, media player, and Companion app integrations retain their own
normal connectivity and privacy characteristics.

Diagnostics are available from the config entry menu. They include policy and
availability information useful for troubleshooting, while redacting
notification targets, media identifiers and URLs, camera or snapshot URLs,
episode action tokens, and credentials. Logs record transitions and error
categories, not captured nursery media or notification contents.

## Limitations and deferred features

This release implements the **Suggested MVP**, not every later idea in
[`product_pitch.md`](product_pitch.md). In particular, it does not include:

- Assisted mode or any automatic volume increase;
- a second boost stage;
- schedules or bedtime windows;
- multiple cry-detection inputs;
- event analytics or response history;
- a custom frontend card;
- device-specific Reolink, Sonos, or Frigate behavior.

The controller requests playback again when an owned player reports idle, off,
or paused. Continuous behavior still depends on the selected player and media
item, so choose a long white-noise file and confirm it on the actual speaker
before relying on it overnight. If a parent starts different media, Nursery
Soother relinquishes the speaker, enters Attention required, and will not
change or stop that media. Turn Nursery Soother off and on to resume.

## Troubleshooting

### The integration is installed but nothing plays

This is expected until the Nursery Soother switch is turned on. Confirm that
the entry is enabled, the media player is available, the selected media can be
played manually, and its integration supports volume set, play media, and
stop.

### Notifications do not arrive

Open **Developer tools > Actions** and test each selected
`notify.mobile_app_*` action. Check Companion app notification permissions and
remote access. A notify target renamed or recreated after setup must be chosen
again through Reconfigure.

### The notification arrived but Boost did nothing

Confirm the notification belongs to the current episode, the Nursery Soother
switch is still on, and the media player is available. Old actions are ignored
after settling, Stop, reload, or restart. Check whether the integration entities
are unavailable, then review Home Assistant logs or download diagnostics for a
dependency problem.

### Volume does not match the configured value

Nursery Soother displays volume as a percentage. It never requests a value
above Maximum, even if Boost is configured incorrectly in stored data. The
speaker integration may round to its own supported volume step.

### The state reports a dependency problem

Check the selected cry sensor, camera, media player, and notification actions.
After the unavailable entity recovers, an enabled controller restarts safely at
baseline. Download diagnostics from the config entry menu if it does not
recover.

### An upgrade asks for reconfiguration

Entries created by the earlier inert foundation are migrated in the disabled
state. Select the new media and notification fields, review all limits, and
only then enable the switch. The migration never replays an old boost or starts
the speaker.

## Removal

1. Turn off the Nursery Soother switch or press Stop.
2. Remove its config entry from **Settings > Devices & services**.
3. Remove the repository from HACS if it is no longer needed.
4. Restart Home Assistant after removing the custom integration files.

## Development

See [`docs/architecture.md`](docs/architecture.md) for the implementation
contract and [`docs/releasing.md`](docs/releasing.md) before publishing a
release.

## License

[MIT](LICENSE)
