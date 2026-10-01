/*
 * Calm card: the line the lamps come on at, against the daylight right now.
 *
 * Point it at a room's "Waarom" sensor:
 *
 *   type: custom:calm-card
 *   entity: sensor.keuken_calm_waarom
 *
 * The bar runs from dark on the left to bright on the right, on a log scale
 * because that is how the eye spaces light. The streak is the line: drag it.
 * The marker is the daylight Calm sees now. Left of the line, the lamps help
 * out; right of it, the daylight does the job on its own.
 *
 * Plain web component, no build step and no library: it ships inside the
 * integration and has to keep working across Home Assistant updates.
 */

const MIN_LUX = 1;
const MAX_LUX = 1000;

const WORDS = {
  nl: {
    on: "aan",
    off: "uit",
    now: "nu",
    label: "Lampen aan als het donkerder is dan",
    lux: "lux",
    missing: "Kies de Waarom-sensor van een Calm-ruimte.",
    levels: ["donker", "schemer", "daglicht", "fel daglicht"],
    daylight: (d) => `Nu ${d} lux daglicht op de sensor.`,
    daylightWithLamps: (d, m) =>
      `Nu ongeveer ${d} lux daglicht: de sensor meet ${m}, de rest komt van de lampen.`,
    staysOn: "Bij deze stand blijven de lampen aan.",
    goesOn: "Bij deze stand gaan de lampen aan.",
    goesOff: "Bij deze stand gaan de lampen zo uit.",
    staysOff: "Bij deze stand blijven de lampen uit.",
    darkEnough: "Bij deze stand is het nu donker genoeg voor de lampen.",
    lightEnough: "Bij deze stand is er nu genoeg daglicht.",
  },
  en: {
    on: "on",
    off: "off",
    now: "now",
    label: "Lights on when darker than",
    lux: "lux",
    missing: "Choose the Why sensor of a Calm room.",
    levels: ["dark", "dusk", "daylight", "bright daylight"],
    daylight: (d) => `Now ${d} lux of daylight on the sensor.`,
    daylightWithLamps: (d, m) =>
      `Now about ${d} lux of daylight: the sensor reads ${m}, the rest is the lamps.`,
    staysOn: "At this setting the lamps stay on.",
    goesOn: "At this setting the lamps come on.",
    goesOff: "At this setting the lamps go out shortly.",
    staysOff: "At this setting the lamps stay off.",
    darkEnough: "At this setting it is dark enough for the lamps now.",
    lightEnough: "At this setting there is enough daylight now.",
  },
};

// Where the words for a setting change, in lux on the sensor.
const LEVEL_EDGES = [5, 50, 300];

// Reasons under which Calm is following the daylight, so what the line
// says now is what the lamps do. Otherwise something else decides for the
// moment (nobody home, somebody's hand, an override) and the card says only
// what the line means.
const FOLLOWING = new Set(["daylight", "returning", "morning", "daytime", "evening", "night"]);

function levelWord(words, lux) {
  const index = LEVEL_EDGES.findIndex((edge) => lux < edge);
  return words.levels[index === -1 ? words.levels.length - 1 : index];
}

function toPosition(lux) {
  const clamped = Math.min(MAX_LUX, Math.max(MIN_LUX, lux || MIN_LUX));
  return Math.log(clamped / MIN_LUX) / Math.log(MAX_LUX / MIN_LUX);
}

function toLux(position) {
  const p = Math.min(1, Math.max(0, position));
  const lux = MIN_LUX * Math.pow(MAX_LUX / MIN_LUX, p);
  // Whole lux below a hundred, fives above: finer than a sensor can tell
  // apart is only noise on a slider.
  return lux < 100 ? Math.round(lux) : Math.round(lux / 5) * 5;
}

class CalmCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("calm-card needs an entity: the room's Why sensor");
    }
    this._config = config;
    this._dragging = null;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this._build();
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 3;
  }

  static getStubConfig(hass) {
    const found = Object.keys(hass.states).find(
      (id) => id.startsWith("sensor.") && hass.states[id].attributes.threshold_entity
    );
    return { entity: found || "" };
  }

  _words() {
    const lang = (this._hass && this._hass.language) || "nl";
    return WORDS[lang.startsWith("nl") ? "nl" : "en"];
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px 16px 20px; }
        .top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .name { font-size: 1.1em; font-weight: 500; color: var(--primary-text-color); }
        .lamp {
          width: 12px; height: 12px; border-radius: 50%; flex: none;
          background: var(--disabled-color, #bdbdbd);
          transition: background 300ms, box-shadow 300ms;
        }
        .lamp.lit { background: #f5b44a; box-shadow: 0 0 10px 3px rgba(245, 180, 74, 0.55); }
        .why { margin-top: 6px; color: var(--secondary-text-color); font-size: 0.92em; line-height: 1.4; }
        .bar {
          position: relative; height: 44px; margin-top: 30px; border-radius: 12px;
          background: linear-gradient(90deg, #1b2130 0%, #3a3e4b 25%, #8e897f 58%, #e8dbc1 82%, #fff4df 100%);
          touch-action: none; cursor: pointer; user-select: none;
        }
        .zone {
          position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center;
          font-size: 0.72em; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
          pointer-events: none;
        }
        .zone.on { left: 0; color: #f6d9a8; }
        .zone.off { right: 0; color: #463d2c; }
        .handle {
          position: absolute; top: -6px; bottom: -6px; width: 6px; margin-left: -3px; border-radius: 3px;
          background: var(--primary-color, #c7771a);
          box-shadow: 0 0 0 3px var(--card-background-color, #fff);
          cursor: grab;
        }
        .handle:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 4px; }
        .handle.dragging { cursor: grabbing; }
        .value {
          position: absolute; bottom: calc(100% + 10px); transform: translateX(-50%);
          font-size: 0.8em; font-weight: 600; color: var(--primary-text-color); white-space: nowrap;
        }
        .now {
          position: absolute; top: 100%; transform: translateX(-50%); margin-top: 3px;
          display: flex; flex-direction: column; align-items: center; pointer-events: none;
        }
        .now::before { content: ""; width: 2px; height: 10px; background: var(--primary-text-color); }
        .now span { font-size: 0.75em; font-weight: 600; color: var(--primary-text-color); }
        .label {
          display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
          margin-top: 18px; font-size: 0.92em; font-weight: 500; color: var(--primary-text-color);
        }
        .word { font-weight: 400; color: var(--secondary-text-color); white-space: nowrap; }
        .verdict { margin-top: 30px; font-size: 0.92em; line-height: 1.45; color: var(--secondary-text-color); }
        .missing { color: var(--error-color, #db4437); }
      </style>
      <ha-card>
        <div class="top"><span class="name"></span><span class="lamp"></span></div>
        <div class="why"></div>
        <div class="label"><span class="label-text"></span><span class="word"></span></div>
        <div class="bar">
          <div class="zone on"></div>
          <div class="zone off"></div>
          <div class="handle" role="slider" tabindex="0"><div class="value"></div></div>
          <div class="now"><span></span></div>
        </div>
        <div class="verdict" aria-live="polite"></div>
      </ha-card>
    `;
    const root = this.shadowRoot;
    this._el = {
      name: root.querySelector(".name"),
      lamp: root.querySelector(".lamp"),
      why: root.querySelector(".why"),
      bar: root.querySelector(".bar"),
      on: root.querySelector(".zone.on"),
      off: root.querySelector(".zone.off"),
      handle: root.querySelector(".handle"),
      value: root.querySelector(".value"),
      now: root.querySelector(".now"),
      nowText: root.querySelector(".now span"),
      labelText: root.querySelector(".label-text"),
      word: root.querySelector(".word"),
      verdict: root.querySelector(".verdict"),
    };
    const bar = this._el.bar;
    bar.addEventListener("pointerdown", (event) => this._startDrag(event));
    bar.addEventListener("pointermove", (event) => this._moveDrag(event));
    bar.addEventListener("pointerup", (event) => this._endDrag(event));
    bar.addEventListener("pointercancel", () => this._cancelDrag());
    this._el.handle.addEventListener("keydown", (event) => this._key(event));
  }

  _state() {
    return this._hass && this._config ? this._hass.states[this._config.entity] : undefined;
  }

  _render() {
    if (!this._el) return;
    const words = this._words();
    const state = this._state();
    if (!state || !state.attributes.threshold_entity) {
      this._el.name.textContent = "Calm";
      this._el.why.textContent = words.missing;
      this._el.why.classList.add("missing");
      return;
    }
    this._el.why.classList.remove("missing");
    const a = state.attributes;
    const threshold = this._dragging !== null ? this._dragging : Number(a.threshold_lux);
    const daylight = Number(a.daylight_lux);
    const line = toPosition(threshold) * 100;

    this._el.name.textContent = a.room || state.attributes.friendly_name || "Calm";
    this._el.lamp.classList.toggle("lit", Boolean(a.lit));
    this._el.why.textContent = state.state;
    this._el.on.style.width = `${line}%`;
    this._el.off.style.width = `${100 - line}%`;
    this._el.on.textContent = line > 12 ? words.on : "";
    this._el.off.textContent = 100 - line > 12 ? words.off : "";
    this._el.handle.style.left = `${line}%`;
    this._el.handle.classList.toggle("dragging", this._dragging !== null);
    this._el.handle.setAttribute("aria-valuemin", String(MIN_LUX));
    this._el.handle.setAttribute("aria-valuemax", String(MAX_LUX));
    this._el.handle.setAttribute("aria-valuenow", String(threshold));
    this._el.handle.setAttribute("aria-label", words.label);
    this._el.value.textContent = `${threshold} ${words.lux}`;
    this._el.now.style.left = `${toPosition(daylight) * 100}%`;
    this._el.nowText.textContent = `${words.now} · ${Math.round(daylight)}`;
    this._el.labelText.textContent = words.label;
    this._el.word.textContent = levelWord(words, threshold);
    this._el.verdict.textContent = this._verdict(words, a, threshold, daylight);
  }

  /** What the line means right now, in the words of the room: how much
   *  daylight there is, and what the lamps do at this setting. Worked out
   *  again while dragging, so the sentence answers the setting under the
   *  finger rather than the one that was saved. */
  _verdict(words, a, threshold, daylight) {
    const d = Math.round(daylight);
    const sensor = a.light_sensor && this._hass.states[a.light_sensor];
    const measured = sensor ? Number(sensor.state) : NaN;
    const now =
      a.lit && Number.isFinite(measured) && Math.round(measured) - d >= 1
        ? words.daylightWithLamps(d, Math.round(measured))
        : words.daylight(d);
    const dark = daylight < threshold;
    let then;
    if (!FOLLOWING.has(a.why)) then = dark ? words.darkEnough : words.lightEnough;
    else if (dark) then = a.lit ? words.staysOn : words.goesOn;
    else then = a.lit ? words.goesOff : words.staysOff;
    return `${now} ${then}`;
  }

  _positionOf(event) {
    const rect = this._el.bar.getBoundingClientRect();
    return (event.clientX - rect.left) / rect.width;
  }

  _startDrag(event) {
    if (!this._state()) return;
    this._el.bar.setPointerCapture(event.pointerId);
    this._dragging = toLux(this._positionOf(event));
    this._render();
  }

  _moveDrag(event) {
    if (this._dragging === null) return;
    this._dragging = toLux(this._positionOf(event));
    this._render();
  }

  _endDrag(event) {
    if (this._dragging === null) return;
    const lux = toLux(this._positionOf(event));
    this._dragging = null;
    this._set(lux);
  }

  _cancelDrag() {
    this._dragging = null;
    this._render();
  }

  _key(event) {
    const state = this._state();
    if (!state) return;
    const current = Number(state.attributes.threshold_lux);
    const step = event.shiftKey ? 0.1 : 0.02;
    let position = toPosition(current);
    if (event.key === "ArrowRight" || event.key === "ArrowUp") position += step;
    else if (event.key === "ArrowLeft" || event.key === "ArrowDown") position -= step;
    else return;
    event.preventDefault();
    this._set(toLux(position));
  }

  _set(lux) {
    const state = this._state();
    if (!state) return;
    this._hass.callService("number", "set_value", {
      entity_id: state.attributes.threshold_entity,
      value: lux,
    });
    this._render();
  }
}

if (!customElements.get("calm-card")) {
  customElements.define("calm-card", CalmCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "calm-card",
    name: "Calm",
    description: "The line the lamps come on at, against the daylight right now.",
    preview: true,
  });
}
