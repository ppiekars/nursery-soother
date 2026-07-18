import assert from "node:assert/strict";
import test from "node:test";

class FakeShadowRoot {
  set innerHTML(value) {
    this.html = value;
  }

  addEventListener() {}
}

class FakeHTMLElement {
  constructor() {
    this.events = [];
  }

  attachShadow() {
    this.shadowRoot = new FakeShadowRoot();
    return this.shadowRoot;
  }

  dispatchEvent(event) {
    this.events.push(event);
    return true;
  }

  get isConnected() {
    return true;
  }
}

class FakeCustomEvent {
  constructor(type, options = {}) {
    this.type = type;
    Object.assign(this, options);
  }
}

const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.Element = class {};
globalThis.CustomEvent = FakeCustomEvent;
globalThis.customElements = {
  define: (name, constructor) => registry.set(name, constructor),
  get: (name) => registry.get(name),
};
globalThis.window = { customCards: [] };

await import(
  "../../custom_components/nursery_soother/frontend/nursery-soother-card.js"
);

const Card = registry.get("nursery-soother-card");
const defaultConfig = {
  camera_entity: "camera.nursery",
  level_entity: "select.nursery_soother_level",
  baseline_entity: "switch.nursery_soother_baseline_sound_preview",
  automatic_entity: "switch.nursery_soother_automatic_operation",
  lock_entity: "switch.nursery_soother_level_lock",
  state_entity: "sensor.nursery_soother_state",
  recommendation_entity: "sensor.nursery_soother_recommendation",
  attention_entity: "binary_sensor.nursery_soother_attention_required",
  camera_view: "live",
};

async function settleAction(card) {
  while (card._pendingAction) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

function configuredCard() {
  const card = new Card();
  card.setConfig(defaultConfig);
  card._update = () => {};
  return card;
}

test("registers the card, metadata, and built-in form schema", () => {
  assert.ok(Card);
  const schema = Card.getConfigForm().schema;
  assert.deepEqual(
    schema.map((field) => field.name),
    [
      "camera_entity",
      "level_entity",
      "baseline_entity",
      "automatic_entity",
      "lock_entity",
      "state_entity",
      "recommendation_entity",
      "attention_entity",
      "camera_view",
    ],
  );
  assert.equal(window.customCards.length, 1);
  assert.equal(window.customCards[0].type, "nursery-soother-card");
  assert.equal(window.customCards[0].preview, false);
  assert.deepEqual(schema[0].selector.entity.filter, { domain: "camera" });
  assert.deepEqual(schema[1].selector.entity.filter, {
    domain: "select",
    integration: "nursery_soother",
  });
  assert.deepEqual(configuredCard().getGridOptions(), {
    columns: 12,
    min_columns: 6,
  });
});

test("leaves required entities for deliberate visual-editor selection", () => {
  assert.deepEqual(Card.getStubConfig(), { camera_view: "live" });
});

test("rejects missing fields, incorrect domains, and unknown camera modes", () => {
  const card = new Card();
  assert.throws(() => card.setConfig({}), /camera_entity is required/);
  assert.throws(
    () =>
      card.setConfig({
        ...defaultConfig,
        level_entity: "sensor.not_a_select",
      }),
    /level_entity must reference a select entity/,
  );
  assert.throws(
    () => card.setConfig({ ...defaultConfig, camera_view: "iframe" }),
    /camera_view must be live, auto, or image/,
  );
});

test("keeps attention guidance valid when an active level is still selected", () => {
  const card = configuredCard();
  assert.match(card.shadowRoot.html, /Attention needed — check child and devices/);
  assert.doesNotMatch(card.shadowRoot.html, /reset to Standby/);
  assert.match(card.shadowRoot.html, /class="camera-timer"/);
  assert.match(card.shadowRoot.html, /class="speaker-button"/);
});

test("renders elapsed session time and resets it in Standby", () => {
  const card = configuredCard();
  const classes = new Set();
  const timer = {
    textContent: "00:00",
    attributes: {},
    classList: {
      toggle: (name, enabled) =>
        enabled ? classes.add(name) : classes.delete(name),
    },
    setAttribute: (name, value) => {
      timer.attributes[name] = value;
    },
  };
  card.shadowRoot.querySelector = () => timer;
  const originalNow = Date.now;
  Date.now = () => Date.parse("2026-07-11T13:02:03+00:00");

  card._updateSessionTimer(
    { state: "level_2" },
    {
      attributes: { session_started_at: "2026-07-11T12:00:00+00:00" },
    },
  );
  assert.equal(timer.textContent, "1:02:03");
  assert.equal(timer.attributes.datetime, "PT3723S");
  assert.equal(classes.has("is-active"), true);

  card._updateSessionTimer({ state: "standby" }, { attributes: {} });
  assert.equal(timer.textContent, "00:00");
  assert.equal(timer.attributes.datetime, "PT0S");
  assert.equal(classes.has("is-active"), false);
  Date.now = originalNow;
});

test("does not rewrite unchanged live-region text", () => {
  const card = configuredCard();
  let value = "L1 · Soothing";
  let writes = 0;
  const liveRegion = {
    get textContent() {
      return value;
    },
    set textContent(nextValue) {
      value = nextValue;
      writes += 1;
    },
  };

  card._setTextIfChanged(liveRegion, "L1 · Soothing");
  card._setTextIfChanged(liveRegion, "L1 · Soothing");
  assert.equal(writes, 0);

  card._setTextIfChanged(liveRegion, "L2 · Responding");
  card._setTextIfChanged(liveRegion, "L2 · Responding");
  assert.equal(writes, 1);
});

test("maps exact levels, suggestions, and switch toggles to native services", async () => {
  const card = configuredCard();
  const calls = [];
  card._hass = {
    states: {
      [defaultConfig.level_entity]: { state: "baseline", attributes: {} },
      [defaultConfig.baseline_entity]: { state: "off", attributes: {} },
      [defaultConfig.automatic_entity]: { state: "off", attributes: {} },
      [defaultConfig.lock_entity]: { state: "on", attributes: {} },
      [defaultConfig.recommendation_entity]: {
        state: "increase_level",
        attributes: { suggested_level: "level_1" },
      },
    },
    callService: async (...args) => calls.push(args),
  };

  card._selectLevel("level_2");
  await settleAction(card);
  card._toggleSwitch("baseline_entity", "baseline-preview");
  await settleAction(card);
  card._toggleSwitch("automatic_entity", "automatic");
  await settleAction(card);
  card._toggleSwitch("lock_entity", "lock");
  await settleAction(card);
  card._acceptRecommendation();
  await settleAction(card);

  assert.deepEqual(calls, [
    [
      "select",
      "select_option",
      { entity_id: defaultConfig.level_entity, option: "level_2" },
    ],
    [
      "switch",
      "turn_on",
      { entity_id: defaultConfig.baseline_entity },
    ],
    [
      "switch",
      "turn_on",
      { entity_id: defaultConfig.automatic_entity },
    ],
    ["switch", "turn_off", { entity_id: defaultConfig.lock_entity }],
    [
      "select",
      "select_option",
      { entity_id: defaultConfig.level_entity, option: "level_1" },
    ],
  ]);
});

test("ignores malformed or stale level suggestions", async () => {
  const card = configuredCard();
  const calls = [];
  card._hass = {
    states: {
      [defaultConfig.level_entity]: { state: "level_1", attributes: {} },
      [defaultConfig.recommendation_entity]: {
        state: "increase_level",
        attributes: { suggested_level: "level_4" },
      },
    },
    callService: async (...args) => calls.push(args),
  };

  card._acceptRecommendation();
  await settleAction(card);

  assert.deepEqual(calls, []);
});

test("opens camera more-info without issuing an acknowledgement service", () => {
  const card = configuredCard();
  card._hass = {
    states: {
      [defaultConfig.camera_entity]: {
        entity_id: defaultConfig.camera_entity,
        state: "idle",
        attributes: {},
      },
    },
  };

  card._openCamera();

  assert.equal(card.events.length, 1);
  assert.equal(card.events[0].type, "hass-more-info");
  assert.deepEqual(card.events[0].detail, {
    entityId: defaultConfig.camera_entity,
  });

  card._hass.states[defaultConfig.camera_entity].state = "unavailable";
  card._openCamera();
  assert.equal(card.events.length, 2);
  assert.equal(card.events[1].type, "hass-more-info");
});

test("uses authenticated snapshots and reports service failures", async () => {
  const card = configuredCard();
  card._hass = {
    states: {},
    hassUrl: (path) => `https://ha.example${path}`,
    callService: async () => {
      throw new Error("speaker unavailable");
    },
  };

  assert.equal(
    card._cameraSnapshotUrl({
      entity_id: "camera.nursery",
      attributes: { access_token: "safe token" },
    }),
    "https://ha.example/api/camera_proxy/camera.nursery?token=safe%20token",
  );
  assert.equal(
    card._cameraSnapshotUrl({
      entity_id: "camera.nursery",
      last_updated: "2026-07-12T08:30:00+00:00",
      attributes: { entity_picture: "/api/camera_proxy/camera.nursery" },
    }),
    "https://ha.example/api/camera_proxy/camera.nursery?ns_ts=2026-07-12T08%3A30%3A00%2B00%3A00",
  );
  assert.equal(
    card._cameraSnapshotUrl({
      entity_id: "camera.nursery",
      last_updated: "2026-07-12T08:30:00+00:00",
      attributes: {
        entity_picture: "https://camera-cloud.example/snapshot?signature=signed",
      },
    }),
    "https://camera-cloud.example/snapshot?signature=signed",
  );

  await card._callService("level:baseline", "select", "select_option", {
    entity_id: defaultConfig.level_entity,
    option: "baseline",
  });

  assert.equal(card.events.length, 1);
  assert.equal(card.events[0].type, "hass-notification");
  assert.match(card.events[0].detail.message, /could not apply/);
});

test("uses the native picture-entity renderer for live and auto modes", async () => {
  const card = configuredCard();
  const nativeCard = {
    classList: { add: () => {} },
    remove: () => {},
  };
  let nativeConfig;
  let appended;
  let createCount = 0;
  card._hass = { states: {} };
  card.shadowRoot.querySelector = (selector) =>
    selector === ".camera-media"
      ? { append: (element) => (appended = element) }
      : undefined;
  window.loadCardHelpers = async () => ({
    createCardElement: (config) => {
      createCount += 1;
      nativeConfig = config;
      return nativeCard;
    },
  });

  await card._ensureNativeCamera({ entity_id: defaultConfig.camera_entity });
  await card._ensureNativeCamera({ entity_id: defaultConfig.camera_entity });

  assert.equal(appended, nativeCard);
  assert.equal(nativeCard.hass, card._hass);
  assert.equal(nativeConfig.type, "picture-entity");
  assert.equal(nativeConfig.camera_view, "live");
  assert.equal(nativeConfig.entity, defaultConfig.camera_entity);
  assert.equal(createCount, 1);
  delete window.loadCardHelpers;
});

test("cancels in-flight camera setup and tolerates helper failure", async () => {
  const card = configuredCard();
  let resolveHelpers;
  let appended = false;
  card._hass = { states: {} };
  card.shadowRoot.querySelector = () => ({
    append: () => {
      appended = true;
    },
  });
  window.loadCardHelpers = () =>
    new Promise((resolve) => {
      resolveHelpers = resolve;
    });

  const pending = card._ensureNativeCamera({
    entity_id: defaultConfig.camera_entity,
  });
  card.disconnectedCallback();
  resolveHelpers({
    createCardElement: () => {
      throw new Error("cancelled setup should not create a card");
    },
  });
  await pending;
  assert.equal(appended, false);
  assert.equal(card._cameraCard, undefined);

  window.loadCardHelpers = async () => {
    throw new Error("frontend helper unavailable");
  };
  await card._ensureNativeCamera({ entity_id: defaultConfig.camera_entity });
  assert.equal(card._cameraCard, undefined);
  delete window.loadCardHelpers;
});

test("periodically refreshes image mode and stops when disconnected", () => {
  const card = configuredCard();
  card._config.camera_view = "image";
  card._hass = {
    states: {
      [defaultConfig.camera_entity]: {
        entity_id: defaultConfig.camera_entity,
        state: "idle",
        attributes: {},
      },
    },
  };
  let scheduledCallback;
  let scheduledDelay;
  let clearedTimer;
  let reloads = 0;
  window.setTimeout = (callback, delay) => {
    scheduledCallback = callback;
    scheduledDelay = delay;
    return 42;
  };
  window.clearTimeout = (timer) => {
    clearedTimer = timer;
  };
  card._reloadSnapshot = () => {
    reloads += 1;
  };

  card._scheduleSnapshotRefresh();
  assert.equal(scheduledDelay, 10_000);
  assert.equal(card._snapshotRefreshTimer, 42);

  scheduledCallback();
  assert.equal(reloads, 1);
  assert.equal(card._snapshotRefreshTimer, 42);

  card.disconnectedCallback();
  assert.equal(clearedTimer, 42);
  assert.equal(card._snapshotRefreshTimer, undefined);
  delete window.setTimeout;
  delete window.clearTimeout;
});

test("retries a stable signed snapshot URL without modifying its query", () => {
  const card = configuredCard();
  const signedUrl =
    "https://camera-cloud.example/snapshot?expires=123&signature=signed";
  const snapshot = {
    dataset: { source: signedUrl },
    hidden: false,
    removeAttribute: (name) => {
      assert.equal(name, "src");
      snapshot.src = undefined;
    },
  };
  const label = { textContent: "" };
  const placeholder = {
    hidden: true,
    querySelector: () => label,
  };
  card._hass = {
    states: {
      [defaultConfig.camera_entity]: {
        entity_id: defaultConfig.camera_entity,
        state: "idle",
        last_updated: "2026-07-12T08:30:00+00:00",
        attributes: { entity_picture: signedUrl },
      },
    },
  };
  card.shadowRoot.querySelector = (selector) =>
    selector === ".camera-snapshot" ? snapshot : placeholder;

  card._reloadSnapshot();

  assert.equal(snapshot.src, signedUrl);
  assert.equal(snapshot.dataset.source, signedUrl);
  assert.equal(snapshot.hidden, true);
  assert.equal(placeholder.hidden, false);
  assert.equal(label.textContent, "Refreshing camera");
});

test("keeps the timer cache key across unrelated hass updates", () => {
  const card = configuredCard();
  card._config.camera_view = "image";
  const cameraState = {
    entity_id: defaultConfig.camera_entity,
    state: "idle",
    last_updated: "2026-07-12T08:30:00+00:00",
    attributes: { access_token: "camera-token" },
  };
  const snapshot = {
    dataset: {},
    hidden: false,
    removeAttribute: () => {},
  };
  const placeholder = {
    hidden: true,
    querySelector: () => ({ textContent: "" }),
  };
  card._hass = {
    states: { [defaultConfig.camera_entity]: cameraState },
    hassUrl: (path) => `https://ha.example${path}`,
  };
  card.shadowRoot.querySelector = (selector) =>
    selector === ".camera-snapshot" ? snapshot : placeholder;

  card._reloadSnapshot();
  const refreshedUrl = snapshot.dataset.source;
  const nextHassUpdateUrl = card._snapshotUrlForCurrentView(cameraState);

  assert.equal(nextHassUpdateUrl, refreshedUrl);
  assert.notEqual(nextHassUpdateUrl, card._cameraSnapshotUrl(cameraState));
  assert.match(refreshedUrl, /[?&]ns_ts=\d+$/);
  card._stopSnapshotRefresh();
});
