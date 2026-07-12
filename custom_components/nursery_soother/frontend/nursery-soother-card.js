const CARD_TAG = "nursery-soother-card";

const REQUIRED_ENTITIES = Object.freeze({
  camera_entity: "camera",
  level_entity: "select",
  automatic_entity: "switch",
  lock_entity: "switch",
  state_entity: "sensor",
  recommendation_entity: "sensor",
  attention_entity: "binary_sensor",
});

const LEVELS = Object.freeze([
  {
    value: "standby",
    short: "Standby",
    label: "Standby",
    color: "#7c8490",
    ink: "#ffffff",
  },
  {
    value: "baseline",
    short: "Base",
    label: "Baseline",
    color: "#389878",
    ink: "#ffffff",
  },
  {
    value: "level_1",
    short: "L1",
    label: "Level 1",
    color: "#72a84f",
    ink: "#14240e",
  },
  {
    value: "level_2",
    short: "L2",
    label: "Level 2",
    color: "#c49a20",
    ink: "#2c2200",
  },
  {
    value: "level_3",
    short: "L3",
    label: "Level 3",
    color: "#d87336",
    ink: "#ffffff",
  },
  {
    value: "level_4",
    short: "L4",
    label: "Level 4",
    color: "#c94d3f",
    ink: "#ffffff",
  },
]);

const LEVEL_BY_VALUE = new Map(LEVELS.map((level) => [level.value, level]));

const STATE_META = Object.freeze({
  standby: { label: "Standby", color: "#9299a5" },
  soothing: { label: "Soothing", color: "#4caf50" },
  cry_pending: { label: "Cry pending", color: "#ffb300", pulse: true },
  responding: { label: "Responding", color: "#ff7043", pulse: true },
  settling: { label: "Settling", color: "#5c9ded" },
  attention_required: {
    label: "Attention needed",
    color: "#e53935",
    pulse: true,
  },
  unavailable: { label: "Unavailable", color: "#9299a5" },
});

const CAMERA_VIEWS = new Set(["live", "auto", "image"]);
const UNAVAILABLE_STATES = new Set(["unknown", "unavailable"]);
const SNAPSHOT_REFRESH_MS = 10_000;

const FORM_LABELS = Object.freeze({
  camera_entity: "Nursery camera",
  level_entity: "Soothing level",
  automatic_entity: "Automatic operation",
  lock_entity: "Level lock",
  state_entity: "Policy state",
  recommendation_entity: "Recommendation",
  attention_entity: "Attention required",
  camera_view: "Camera view",
});

const CARD_STYLES = `
  :host {
    display: block;
    color: var(--primary-text-color, rgba(0, 0, 0, 0.87));
    font-family: var(
      --ha-font-family-body,
      var(--paper-font-body1_-_font-family, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif)
    );
  }

  ha-card {
    position: relative;
    display: block;
    overflow: hidden;
    color: var(--primary-text-color, rgba(0, 0, 0, 0.87));
    background: var(--ha-card-background, var(--card-background-color, #ffffff));
    border-radius: var(--ha-card-border-radius, 12px);
  }

  ha-card.attention {
    animation: ns-glow 1.8s ease-in-out infinite;
  }

  .camera {
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 10;
    overflow: hidden;
    cursor: pointer;
    background: var(--secondary-background-color, #e8eaed);
  }

  .camera:focus-visible {
    outline: 3px solid var(--primary-color, #03a9f4);
    outline-offset: -3px;
  }

  .camera-media,
  .camera-placeholder,
  .camera-snapshot,
  .native-camera {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }

  .camera-media {
    pointer-events: none;
  }

  .camera-placeholder {
    display: grid;
    place-items: center;
    background: repeating-linear-gradient(
      135deg,
      var(--secondary-background-color, #dfe2e6) 0,
      var(--secondary-background-color, #dfe2e6) 14px,
      var(--card-background-color, #eceef0) 14px,
      var(--card-background-color, #eceef0) 28px
    );
  }

  .camera-placeholder[hidden],
  .camera-snapshot[hidden],
  .banner[hidden],
  .entity-warning[hidden] {
    display: none;
  }

  .placeholder-label {
    padding: 6px 10px;
    color: var(--secondary-text-color, rgba(0, 0, 0, 0.62));
    background: color-mix(
      in srgb,
      var(--card-background-color, #ffffff) 84%,
      transparent
    );
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
  }

  .camera-snapshot {
    display: block;
    object-fit: cover;
  }

  .native-camera {
    z-index: 1;
    display: block;
    overflow: hidden;
    --ha-card-background: transparent;
    --ha-card-border-radius: 0;
    --ha-card-border-width: 0;
    --ha-card-box-shadow: none;
  }

  .camera-badge,
  .state-badge {
    position: absolute;
    z-index: 2;
    top: 8px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: calc(100% - 32px);
    color: #ffffff;
    background: rgba(0, 0, 0, 0.58);
    border-radius: 999px;
    box-sizing: border-box;
    line-height: 1;
    white-space: nowrap;
  }

  .camera-badge {
    left: 8px;
    padding: 5px 8px 5px 7px;
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0.04em;
  }

  .state-badge {
    right: 8px;
    overflow: hidden;
    padding: 6px 9px;
    font-size: 11px;
    font-weight: 650;
    text-overflow: ellipsis;
  }

  .camera-dot,
  .state-dot,
  .pill-dot {
    flex: 0 0 auto;
    border-radius: 50%;
  }

  .camera-dot {
    width: 6px;
    height: 6px;
    background: #ff5252;
    animation: ns-pulse 1.6s ease-in-out infinite;
  }

  .camera-badge.is-image .camera-dot,
  .camera-badge.is-unavailable .camera-dot {
    animation: none;
    background: #b0b5bf;
  }

  .state-dot {
    width: 8px;
    height: 8px;
    background: var(--state-color, #9299a5);
  }

  .state-dot.pulse {
    animation: ns-pulse 1.4s ease-in-out infinite;
  }

  .banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-height: 42px;
    padding: 7px 12px;
    box-sizing: border-box;
  }

  .banner-text {
    min-width: 0;
    font-size: 12.5px;
    font-weight: 650;
    line-height: 1.35;
  }

  .attention-banner {
    color: var(--error-color, #c62828);
    background: color-mix(
      in srgb,
      var(--error-color, #e53935) 11%,
      transparent
    );
  }

  .recommendation-banner {
    color: var(--warning-color, #8a5a00);
    background: color-mix(
      in srgb,
      var(--warning-color, #ffb300) 14%,
      transparent
    );
  }

  button {
    margin: 0;
    border: 0;
    font: inherit;
    -webkit-tap-highlight-color: transparent;
  }

  button:not(:disabled) {
    cursor: pointer;
  }

  button:disabled {
    cursor: not-allowed;
    opacity: 0.52;
  }

  button:focus-visible {
    outline: 2px solid var(--primary-color, #03a9f4);
    outline-offset: 2px;
  }

  .banner-action {
    flex: 0 0 auto;
    min-height: 30px;
    padding: 5px 12px;
    color: #ffffff;
    border-radius: 7px;
    font-size: 12px;
    font-weight: 700;
  }

  .attend-button {
    background: var(--error-color, #e53935);
  }

  .set-button {
    background: var(--primary-color, #03a9f4);
  }

  .levels {
    display: grid;
    grid-template-columns: 1.35fr repeat(5, 1fr);
    gap: 5px;
    padding: 12px 12px 8px;
  }

  .level-button {
    min-width: 0;
    min-height: 36px;
    overflow: hidden;
    padding: 7px 2px;
    color: var(--secondary-text-color, rgba(0, 0, 0, 0.62));
    background: transparent;
    border: 1.5px solid var(--divider-color, rgba(0, 0, 0, 0.12));
    border-radius: 7px;
    font-size: 11.5px;
    font-weight: 700;
    text-align: center;
    text-overflow: ellipsis;
    white-space: nowrap;
    transition: background-color 120ms ease, border-color 120ms ease,
      color 120ms ease, transform 120ms ease;
  }

  .level-button.selected {
    color: var(--level-ink, #ffffff);
    background: var(--level-color);
    border-color: var(--level-color);
  }

  .level-button.pending {
    animation: ns-pulse 0.9s ease-in-out infinite;
  }

  .level-button:not(:disabled):active,
  .pill:not(:disabled):active,
  .banner-action:not(:disabled):active {
    transform: translateY(1px);
  }

  .toggles {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    padding: 0 12px 12px;
  }

  .pill {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    min-height: 36px;
    padding: 7px 10px;
    color: var(--secondary-text-color, rgba(0, 0, 0, 0.62));
    background: transparent;
    border: 1.5px solid var(--divider-color, rgba(0, 0, 0, 0.12));
    border-radius: 7px;
    font-size: 12px;
    font-weight: 700;
    transition: background-color 120ms ease, border-color 120ms ease,
      color 120ms ease, transform 120ms ease;
  }

  .pill.is-on {
    color: var(--primary-color, #03a9f4);
    background: color-mix(
      in srgb,
      var(--primary-color, #03a9f4) 10%,
      transparent
    );
    border-color: var(--primary-color, #03a9f4);
  }

  .pill-dot {
    width: 7px;
    height: 7px;
    background: var(--disabled-text-color, rgba(0, 0, 0, 0.28));
  }

  .pill.is-on .pill-dot {
    background: var(--primary-color, #03a9f4);
  }

  .entity-warning {
    padding: 0 12px 11px;
    color: var(--error-color, #c62828);
    font-size: 11.5px;
    line-height: 1.35;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  @keyframes ns-pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.42;
    }
  }

  @keyframes ns-glow {
    0%,
    100% {
      box-shadow: 0 0 0 0 color-mix(
        in srgb,
        var(--error-color, #e53935) 38%,
        transparent
      );
    }
    50% {
      box-shadow: 0 0 0 6px transparent;
    }
  }

  @media (max-width: 360px) {
    .levels {
      gap: 4px;
      padding-inline: 10px;
    }

    .level-button {
      padding-inline: 1px;
      font-size: 11px;
    }

    .toggles {
      padding-inline: 10px;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    ha-card.attention,
    .camera-dot,
    .state-dot.pulse,
    .level-button.pending {
      animation: none;
    }

    .level-button,
    .pill {
      transition: none;
    }
  }

  @media (prefers-contrast: more) {
    .level-button,
    .pill {
      border-width: 2px;
    }
  }
`;

function entityDomain(entityId) {
  return typeof entityId === "string" ? entityId.split(".", 1)[0] : "";
}

function isUsableState(stateObj) {
  return Boolean(stateObj) && !UNAVAILABLE_STATES.has(stateObj.state);
}

function isUsableSwitchState(stateObj) {
  return isUsableState(stateObj) && ["on", "off"].includes(stateObj.state);
}

function nextLevel(currentValue) {
  const index = LEVELS.findIndex((level) => level.value === currentValue);
  return index >= 0 && index + 1 < LEVELS.length ? LEVELS[index + 1] : undefined;
}

class NurserySootherCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._domReady = false;
    this._pendingAction = undefined;
    this._cameraCard = undefined;
    this._cameraKey = undefined;
    this._cameraGeneration = 0;
    this._snapshotRefreshTimer = undefined;
    this._snapshotRefreshToken = undefined;
  }

  static getConfigForm() {
    return {
      schema: [
        {
          name: "camera_entity",
          required: true,
          selector: { entity: { filter: { domain: "camera" } } },
        },
        {
          name: "level_entity",
          required: true,
          selector: {
            entity: {
              filter: { domain: "select", integration: "nursery_soother" },
            },
          },
        },
        {
          name: "automatic_entity",
          required: true,
          selector: {
            entity: {
              filter: { domain: "switch", integration: "nursery_soother" },
            },
          },
        },
        {
          name: "lock_entity",
          required: true,
          selector: {
            entity: {
              filter: { domain: "switch", integration: "nursery_soother" },
            },
          },
        },
        {
          name: "state_entity",
          required: true,
          selector: {
            entity: {
              filter: { domain: "sensor", integration: "nursery_soother" },
            },
          },
        },
        {
          name: "recommendation_entity",
          required: true,
          selector: {
            entity: {
              filter: { domain: "sensor", integration: "nursery_soother" },
            },
          },
        },
        {
          name: "attention_entity",
          required: true,
          selector: {
            entity: {
              filter: {
                domain: "binary_sensor",
                integration: "nursery_soother",
              },
            },
          },
        },
        {
          name: "camera_view",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "live", label: "Live" },
                { value: "auto", label: "Auto" },
                { value: "image", label: "Image" },
              ],
            },
          },
        },
      ],
      computeLabel: (schema) => FORM_LABELS[schema.name],
      computeHelper: (schema) => {
        if (schema.name === "camera_view") {
          return "Live and Auto use Home Assistant's camera card; Image uses the current snapshot.";
        }
        return undefined;
      },
    };
  }

  static getStubConfig() {
    // Required entities stay intentionally blank so multiple nurseries or renamed
    // registry entries can never be silently mixed by a naming heuristic.
    return { camera_view: "live" };
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Nursery Soother card configuration is required.");
    }

    for (const [key, domain] of Object.entries(REQUIRED_ENTITIES)) {
      const entityId = config[key];
      if (typeof entityId !== "string" || !entityId.trim()) {
        throw new Error(`${key} is required.`);
      }
      if (entityDomain(entityId) !== domain) {
        throw new Error(`${key} must reference a ${domain} entity.`);
      }
    }

    const cameraView = config.camera_view ?? "live";
    if (!CAMERA_VIEWS.has(cameraView)) {
      throw new Error("camera_view must be live, auto, or image.");
    }

    const oldCameraKey = this._config
      ? `${this._config.camera_entity}|${this._config.camera_view}`
      : undefined;
    this._config = { ...config, camera_view: cameraView };
    const newCameraKey = `${this._config.camera_entity}|${cameraView}`;
    if (oldCameraKey !== newCameraKey) {
      this._stopSnapshotRefresh();
      this._resetCameraCard();
    }

    this._ensureDom();
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureDom();
    if (this._cameraCard) {
      this._cameraCard.hass = hass;
    }
    this._update();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    this._ensureDom();
    this._update();
  }

  disconnectedCallback() {
    this._cameraGeneration += 1;
    this._stopSnapshotRefresh();
  }

  getCardSize() {
    const attention =
      this._state(this._config?.attention_entity)?.state === "on" ||
      this._state(this._config?.state_entity)?.state === "attention_required";
    const currentLevel = this._state(this._config?.level_entity)?.state;
    const recommendationState = this._state(
      this._config?.recommendation_entity,
    );
    const recommendation =
      recommendationState?.state === "increase_level" &&
      recommendationState.attributes?.suggested_level === nextLevel(currentLevel)?.value;
    return attention || recommendation ? 7 : 6;
  }

  getGridOptions() {
    return {
      columns: 12,
      min_columns: 6,
    };
  }

  _ensureDom() {
    if (this._domReady || !this.shadowRoot) {
      return;
    }

    const levelButtons = LEVELS.map(
      (level) => `
        <button
          class="level-button"
          type="button"
          data-action="select-level"
          data-level="${level.value}"
          style="--level-color:${level.color};--level-ink:${level.ink}"
          aria-label="Set soothing level to ${level.label}"
          aria-pressed="false"
        >${level.short}</button>
      `,
    ).join("");

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card role="region" aria-label="Nursery Soother">
        <div
          class="camera"
          data-action="open-camera"
          role="button"
          tabindex="0"
          aria-label="Open nursery camera details"
        >
          <div class="camera-media" aria-hidden="true">
            <div class="camera-placeholder">
              <span class="placeholder-label">Camera preview</span>
            </div>
            <img class="camera-snapshot" alt="" hidden>
          </div>
          <div class="camera-badge">
            <span class="camera-dot"></span>
            <span class="camera-label">LIVE</span>
          </div>
          <div class="state-badge">
            <span class="state-dot"></span>
            <span class="status-text">Unavailable</span>
          </div>
        </div>

        <div
          class="banner attention-banner"
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
          hidden
        >
          <span class="banner-text">Attention needed — check child and devices</span>
          <button
            class="banner-action attend-button"
            type="button"
            data-action="open-camera"
          >Attend</button>
        </div>

        <div
          class="banner recommendation-banner"
          role="status"
          aria-live="polite"
          aria-atomic="true"
          hidden
        >
          <span class="banner-text recommendation-text"></span>
          <button
            class="banner-action set-button"
            type="button"
            data-action="accept-recommendation"
          >Set</button>
        </div>

        <div class="levels" role="group" aria-label="Soothing level">
          ${levelButtons}
        </div>

        <div class="toggles">
          <button
            class="pill automatic-button"
            type="button"
            data-action="toggle-automatic"
            aria-label="Toggle automatic operation"
            aria-pressed="false"
          >
            <span class="pill-dot"></span>
            <span>Auto</span>
          </button>
          <button
            class="pill lock-button"
            type="button"
            data-action="toggle-lock"
            aria-label="Toggle level lock"
            aria-pressed="false"
          >
            <span class="pill-dot"></span>
            <span>Lock</span>
          </button>
        </div>

        <div class="entity-warning" role="alert" hidden></div>
        <span class="sr-only state-announcer" aria-live="polite"></span>
      </ha-card>
    `;

    const snapshot = this.shadowRoot.querySelector?.(".camera-snapshot");
    snapshot?.addEventListener("load", () => {
      const placeholder = this.shadowRoot.querySelector(".camera-placeholder");
      snapshot.hidden = false;
      placeholder.hidden = true;
    });
    snapshot?.addEventListener("error", () => {
      const placeholder = this.shadowRoot.querySelector(".camera-placeholder");
      snapshot.hidden = true;
      snapshot.dataset.source = "";
      placeholder.hidden = false;
      placeholder.querySelector(".placeholder-label").textContent =
        "Camera preview unavailable";
    });

    this.shadowRoot.addEventListener("click", (event) => {
      const actionElement = event
        .composedPath()
        .find((node) => node instanceof Element && node.dataset?.action);
      if (actionElement) {
        this._handleAction(actionElement);
      }
    });

    this.shadowRoot.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      const actionElement = event
        .composedPath()
        .find((node) => node instanceof Element && node.dataset?.action);
      if (actionElement?.dataset.action === "open-camera") {
        event.preventDefault();
        this._openCamera();
      }
    });

    this._domReady = true;
  }

  _state(entityId) {
    return entityId && this._hass?.states
      ? this._hass.states[entityId]
      : undefined;
  }

  _update() {
    if (!this._domReady || !this._config || !this._hass) {
      return;
    }

    const levelState = this._state(this._config.level_entity);
    const automaticState = this._state(this._config.automatic_entity);
    const lockState = this._state(this._config.lock_entity);
    const policyState = this._state(this._config.state_entity);
    const recommendationState = this._state(
      this._config.recommendation_entity,
    );
    const attentionState = this._state(this._config.attention_entity);
    const cameraState = this._state(this._config.camera_entity);

    const level = LEVEL_BY_VALUE.get(levelState?.state);
    const rawPolicyState = isUsableState(policyState)
      ? policyState.state
      : "unavailable";
    const meta = STATE_META[rawPolicyState] ?? STATE_META.unavailable;
    const attention =
      attentionState?.state === "on" || rawPolicyState === "attention_required";
    const suggestedValue = recommendationState?.attributes?.suggested_level;
    const suggestedLevel = LEVEL_BY_VALUE.get(suggestedValue);
    const expectedSuggestedLevel = nextLevel(levelState?.state);
    const showRecommendation =
      !attention &&
      recommendationState?.state === "increase_level" &&
      Boolean(suggestedLevel) &&
      suggestedLevel.value === expectedSuggestedLevel?.value;

    const card = this.shadowRoot.querySelector("ha-card");
    card.classList.toggle("attention", attention);

    const stateDot = this.shadowRoot.querySelector(".state-dot");
    stateDot.style.setProperty("--state-color", meta.color);
    stateDot.classList.toggle("pulse", Boolean(meta.pulse));
    const statusText = level
      ? `${level.short} · ${meta.label}`
      : meta.label;
    this._setTextIfChanged(
      this.shadowRoot.querySelector(".status-text"),
      statusText,
    );
    this._setTextIfChanged(
      this.shadowRoot.querySelector(".state-announcer"),
      `Nursery Soother: ${statusText}.`,
    );

    const attentionBanner = this.shadowRoot.querySelector(".attention-banner");
    attentionBanner.hidden = !attention;
    const attendButton = this.shadowRoot.querySelector(".attend-button");
    attendButton.disabled = !cameraState;

    const recommendationBanner = this.shadowRoot.querySelector(
      ".recommendation-banner",
    );
    recommendationBanner.hidden = !showRecommendation;
    this._setTextIfChanged(
      this.shadowRoot.querySelector(".recommendation-text"),
      showRecommendation ? `Suggests ${suggestedLevel.short}` : "",
    );
    this.shadowRoot.querySelector(".set-button").disabled =
      !showRecommendation ||
      !isUsableState(levelState) ||
      Boolean(this._pendingAction);

    const levelAvailable =
      isUsableState(levelState) && LEVEL_BY_VALUE.has(levelState.state);
    for (const button of this.shadowRoot.querySelectorAll(".level-button")) {
      const selected = button.dataset.level === levelState?.state;
      const pending = this._pendingAction === `level:${button.dataset.level}`;
      button.classList.toggle("selected", selected);
      button.classList.toggle("pending", pending);
      button.setAttribute("aria-pressed", String(selected));
      button.disabled = !levelAvailable || Boolean(this._pendingAction);
    }

    this._updatePill(
      this.shadowRoot.querySelector(".automatic-button"),
      automaticState,
    );
    this._updatePill(this.shadowRoot.querySelector(".lock-button"), lockState);

    const missingEntities = Object.keys(REQUIRED_ENTITIES)
      .map((key) => this._config[key])
      .filter((entityId) => !this._state(entityId));
    const warning = this.shadowRoot.querySelector(".entity-warning");
    warning.hidden = missingEntities.length === 0;
    this._setTextIfChanged(
      warning,
      missingEntities.length
        ? `Entity not found: ${missingEntities.join(", ")}`
        : "",
    );

    this._updateCamera(cameraState);
  }

  _updatePill(button, stateObj) {
    const available = isUsableSwitchState(stateObj);
    const on = available && stateObj.state === "on";
    button.classList.toggle("is-on", on);
    button.setAttribute("aria-pressed", String(on));
    button.disabled = !available || Boolean(this._pendingAction);
  }

  _setTextIfChanged(element, text) {
    if (element.textContent !== text) {
      element.textContent = text;
    }
  }

  _updateCamera(cameraState) {
    const placeholder = this.shadowRoot.querySelector(".camera-placeholder");
    const placeholderLabel = this.shadowRoot.querySelector(
      ".placeholder-label",
    );
    const snapshot = this.shadowRoot.querySelector(".camera-snapshot");
    const badge = this.shadowRoot.querySelector(".camera-badge");
    const cameraLabel = this.shadowRoot.querySelector(".camera-label");
    const camera = this.shadowRoot.querySelector(".camera");
    const statusText = this.shadowRoot.querySelector(".status-text").textContent;
    const cameraAvailable = isUsableState(cameraState);
    const snapshotUrl = cameraAvailable
      ? this._snapshotUrlForCurrentView(cameraState)
      : undefined;

    placeholderLabel.textContent = cameraAvailable
      ? "Camera preview"
      : "Camera unavailable";
    if (!snapshotUrl) {
      placeholder.hidden = false;
      snapshot.hidden = true;
      snapshot.dataset.source = "";
      snapshot.removeAttribute("src");
    } else if (snapshot.dataset.source !== snapshotUrl) {
      placeholder.hidden = false;
      snapshot.hidden = true;
      snapshot.dataset.source = snapshotUrl;
      snapshot.src = snapshotUrl;
    } else if (snapshot.complete && snapshot.naturalWidth > 0) {
      placeholder.hidden = true;
      snapshot.hidden = false;
    } else if (snapshot.complete) {
      placeholder.hidden = false;
      snapshot.hidden = true;
      placeholderLabel.textContent = "Camera preview unavailable";
    }
    snapshot.alt = cameraState?.attributes?.friendly_name ?? "Nursery camera";

    badge.classList.toggle("is-image", this._config.camera_view === "image");
    badge.classList.toggle("is-unavailable", !cameraAvailable);
    cameraLabel.textContent = !cameraAvailable
      ? "OFFLINE"
      : this._config.camera_view === "live"
        ? "LIVE MODE"
        : this._config.camera_view === "auto"
          ? "AUTO"
          : "IMAGE";
    const cameraName =
      cameraState?.attributes?.friendly_name ?? "nursery camera";
    camera.setAttribute(
      "aria-label",
      cameraState
        ? `Open ${cameraName} details. ${cameraAvailable ? "Camera available" : "Camera offline"}. Nursery Soother: ${statusText}.`
        : `Nursery camera not found. Nursery Soother: ${statusText}.`,
    );
    camera.setAttribute("aria-disabled", String(!cameraState));
    camera.tabIndex = cameraState ? 0 : -1;

    if (!cameraAvailable) {
      this._stopSnapshotRefresh();
      this._resetCameraCard();
      return;
    }

    if (this._config.camera_view === "image") {
      this._scheduleSnapshotRefresh();
      this._resetCameraCard();
      return;
    }

    this._stopSnapshotRefresh();
    this._ensureNativeCamera(cameraState);
  }

  _cameraSnapshotUrl(cameraState, refreshToken = cameraState.last_updated) {
    const attributes = cameraState.attributes ?? {};
    let url = attributes.entity_picture_local ?? attributes.entity_picture;
    if (!url) {
      const token = attributes.access_token;
      const tokenQuery = token ? `?token=${encodeURIComponent(token)}` : "";
      url = `/api/camera_proxy/${cameraState.entity_id}${tokenQuery}`;
    }

    if (typeof url !== "string") {
      return undefined;
    }

    if (/^(?:data:|blob:)/i.test(url)) {
      return url;
    }
    const isHomeAssistantProxy = url.startsWith("/api/camera_proxy/");
    const resolvedUrl =
      /^(?:https?:)/i.test(url) || typeof this._hass?.hassUrl !== "function"
        ? url
        : this._hass.hassUrl(url);
    if (!isHomeAssistantProxy || !refreshToken) {
      return resolvedUrl;
    }
    const separator = resolvedUrl.includes("?") ? "&" : "?";
    return `${resolvedUrl}${separator}ns_ts=${encodeURIComponent(refreshToken)}`;
  }

  _snapshotUrlForCurrentView(cameraState) {
    const refreshToken =
      this._config?.camera_view === "image" &&
      this._snapshotRefreshToken !== undefined
        ? this._snapshotRefreshToken
        : cameraState.last_updated;
    return this._cameraSnapshotUrl(cameraState, refreshToken);
  }

  _scheduleSnapshotRefresh() {
    if (
      this._snapshotRefreshTimer !== undefined ||
      !this.isConnected ||
      this._config?.camera_view !== "image" ||
      !isUsableState(this._state(this._config.camera_entity))
    ) {
      return;
    }

    this._snapshotRefreshTimer = window.setTimeout(() => {
      this._snapshotRefreshTimer = undefined;
      this._reloadSnapshot();
      this._scheduleSnapshotRefresh();
    }, SNAPSHOT_REFRESH_MS);
  }

  _stopSnapshotRefresh() {
    this._snapshotRefreshToken = undefined;
    if (this._snapshotRefreshTimer === undefined) {
      return;
    }
    window.clearTimeout(this._snapshotRefreshTimer);
    this._snapshotRefreshTimer = undefined;
  }

  _reloadSnapshot() {
    const cameraState = this._state(this._config?.camera_entity);
    if (!isUsableState(cameraState) || !this._domReady) {
      return;
    }
    this._snapshotRefreshToken = Date.now();
    const snapshotUrl = this._cameraSnapshotUrl(
      cameraState,
      this._snapshotRefreshToken,
    );
    if (!snapshotUrl) {
      return;
    }

    const snapshot = this.shadowRoot.querySelector(".camera-snapshot");
    const placeholder = this.shadowRoot.querySelector(".camera-placeholder");
    placeholder.hidden = false;
    placeholder.querySelector(".placeholder-label").textContent =
      "Refreshing camera";
    snapshot.hidden = true;
    snapshot.dataset.source = snapshotUrl;
    snapshot.removeAttribute("src");
    snapshot.src = snapshotUrl;
  }

  async _ensureNativeCamera(cameraState) {
    const desiredKey = `${this._config.camera_entity}|${this._config.camera_view}`;
    if (this._cameraCard && this._cameraKey === desiredKey) {
      this._cameraCard.hass = this._hass;
      return;
    }

    const generation = ++this._cameraGeneration;
    this._removeNativeCamera();

    if (typeof window.loadCardHelpers !== "function") {
      return;
    }

    try {
      const helpers = await window.loadCardHelpers();
      if (
        generation !== this._cameraGeneration ||
        !this.isConnected ||
        !this._config ||
        desiredKey !==
          `${this._config.camera_entity}|${this._config.camera_view}`
      ) {
        return;
      }

      const nativeCard = await Promise.resolve(
        helpers.createCardElement({
          type: "picture-entity",
          entity: cameraState.entity_id,
          camera_view: this._config.camera_view,
          fit_mode: "cover",
          show_name: false,
          show_state: false,
          tap_action: { action: "none" },
          hold_action: { action: "none" },
          double_tap_action: { action: "none" },
        }),
      );
      if (generation !== this._cameraGeneration) {
        return;
      }

      nativeCard.classList.add("native-camera");
      nativeCard.hass = this._hass;
      this.shadowRoot.querySelector(".camera-media").append(nativeCard);
      this._cameraCard = nativeCard;
      this._cameraKey = desiredKey;
    } catch (_error) {
      // The authenticated camera snapshot remains visible as the stable fallback.
    }
  }

  _removeNativeCamera() {
    if (this._cameraCard) {
      this._cameraCard.remove();
      this._cameraCard = undefined;
    }
    this._cameraKey = undefined;
  }

  _resetCameraCard() {
    this._cameraGeneration += 1;
    this._removeNativeCamera();
  }

  _handleAction(element) {
    if (element.disabled) {
      return;
    }

    switch (element.dataset.action) {
      case "select-level":
        this._selectLevel(element.dataset.level);
        break;
      case "toggle-automatic":
        this._toggleSwitch("automatic_entity", "automatic");
        break;
      case "toggle-lock":
        this._toggleSwitch("lock_entity", "lock");
        break;
      case "accept-recommendation":
        this._acceptRecommendation();
        break;
      case "open-camera":
        this._openCamera();
        break;
      default:
        break;
    }
  }

  _selectLevel(level) {
    if (!LEVEL_BY_VALUE.has(level)) {
      return;
    }
    this._callService(`level:${level}`, "select", "select_option", {
      entity_id: this._config.level_entity,
      option: level,
    });
  }

  _toggleSwitch(configKey, actionName) {
    const entityId = this._config[configKey];
    const stateObj = this._state(entityId);
    if (!isUsableSwitchState(stateObj)) {
      return;
    }
    const service = stateObj.state === "on" ? "turn_off" : "turn_on";
    this._callService(actionName, "switch", service, { entity_id: entityId });
  }

  _acceptRecommendation() {
    const recommendation = this._state(this._config.recommendation_entity);
    const suggestedLevel = recommendation?.attributes?.suggested_level;
    const currentLevel = this._state(this._config.level_entity)?.state;
    if (
      recommendation?.state !== "increase_level" ||
      !LEVEL_BY_VALUE.has(suggestedLevel) ||
      nextLevel(currentLevel)?.value !== suggestedLevel
    ) {
      return;
    }
    this._selectLevel(suggestedLevel);
  }

  _openCamera() {
    const cameraState = this._state(this._config.camera_entity);
    if (!cameraState) {
      return;
    }
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        bubbles: true,
        composed: true,
        detail: { entityId: this._config.camera_entity },
      }),
    );
  }

  async _callService(actionName, domain, service, data) {
    if (this._pendingAction || typeof this._hass?.callService !== "function") {
      return;
    }

    this._pendingAction = actionName;
    this._update();
    try {
      await this._hass.callService(domain, service, data);
    } catch (_error) {
      this.dispatchEvent(
        new CustomEvent("hass-notification", {
          bubbles: true,
          composed: true,
          detail: {
            message: "Nursery Soother could not apply the requested action.",
          },
        }),
      );
    } finally {
      this._pendingAction = undefined;
      this._update();
    }
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, NurserySootherCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "Nursery Soother",
    description: "Camera, state, exact levels, Automatic operation, and Level lock.",
    preview: false,
    documentationURL:
      "https://github.com/ppiekars/nursery-soother#dashboard",
  });
}
