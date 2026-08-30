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
  assert.deepEqual(Card.getStubConfig(), {});
});

test("rejects missing fields and incorrect domains", () => {
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
});

test("keeps attention guidance valid when an active level is still selected", () => {
  const card = configuredCard();
  assert.match(card.shadowRoot.html, /Attention needed — check child and devices/);
  assert.doesNotMatch(card.shadowRoot.html, /reset to Standby/);
  assert.match(card.shadowRoot.html, /class="session-timer"/);
  assert.match(card.shadowRoot.html, /class="speaker-button"/);
  assert.match(card.shadowRoot.html, /class="camera-button"/);
  assert.doesNotMatch(card.shadowRoot.html, /camera-(?:media|snapshot|placeholder)/);
  assert.doesNotMatch(card.shadowRoot.html, /<img|picture-entity/);
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

test("reports service failures without exposing camera media helpers", async () => {
  const card = configuredCard();
  card._hass = {
    states: {},
    callService: async () => {
      throw new Error("speaker unavailable");
    },
  };

  assert.equal(card._cameraSnapshotUrl, undefined);
  assert.equal(card._ensureNativeCamera, undefined);

  await card._callService("level:baseline", "select", "select_option", {
    entity_id: defaultConfig.level_entity,
    option: "baseline",
  });

  assert.equal(card.events.length, 1);
  assert.equal(card.events[0].type, "hass-notification");
  assert.match(card.events[0].detail.message, /could not apply/);
});
