import "./style.css";

import { createConfiguredRun, health, listCrops, listLimits, listPresets } from "./api";
import { TimelineChart } from "./chart";
import { CROP_SWATCHES, stageColor, stageCss, rampCss, rampFor } from "./palette";
import { CROP_CHANNEL, FieldScene, type HoverInfo } from "./scene";
import {
  applyDataI18n,
  dn,
  dnd,
  getLang,
  langs,
  onLangChange,
  setLang,
  t,
  tOpt,
  type Lang,
} from "./i18n";
import { decodeChannel, type Bundle, type CropOption, type Limits, type Preset } from "./types";

const CHANNEL_ORDER = [
  CROP_CHANNEL,
  "lai",
  "biomass",
  "height",
  "stress",
  "moisture",
  "nitrogen",
  "disease",
  "stage",
];

const DATE_ORIGIN = Date.UTC(2026, 0, 1);

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`缺少 DOM 节点 #${id}`);
  return node as T;
}

function format(value: number, digits = 1): string {
  if (!Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 100) return value.toFixed(0);
  return value.toFixed(digits);
}

function dateForDoy(doy: number): string {
  const date = new Date(DATE_ORIGIN + (doy - 1) * 86_400_000);
  const month = date.getUTCMonth() + 1;
  const day = date.getUTCDate();
  return t("date.format", [month, day]);
}

interface Grid {
  rows: number;
  cols: number;
}

/** Remap a painted layout onto another grid, by nearest relative position.
 *
 * Mirrors `ScenarioConfig.with_grid` on the server: changing the size of the
 * field must stretch what is already planted, not throw it away.
 */
function remapCrops(source: string[], from: Grid, to: Grid): string[] {
  if (source.length !== from.rows * from.cols || !source.length) {
    return source;
  }
  const out: string[] = [];
  for (let row = 0; row < to.rows; row += 1) {
    const sourceRow = Math.min(from.rows - 1, Math.round((row * from.rows) / to.rows));
    for (let col = 0; col < to.cols; col += 1) {
      const sourceCol = Math.min(from.cols - 1, Math.round((col * from.cols) / to.cols));
      out.push(source[sourceRow * from.cols + sourceCol] as string);
    }
  }
  return out;
}

class App {
  private readonly scene: FieldScene;
  private readonly chart: TimelineChart;
  private bundle: Bundle | null = null;
  private presets: Preset[] = [];
  private crops: CropOption[] = [];
  private limits: Limits | null = null;
  private channel = "lai";
  private index = 0;
  private playing = false;
  private speed = 90;
  private lastTick = 0;
  private brush = "tomato";
  private baseCrop = "tomato";
  private canMix = true;
  private rows = 5;
  private cols = 12;
  private days = 130;
  private cropMap: string[] = [];
  /** Set once the user drags the horizon slider; until then it follows crops. */
  private daysPinned = false;
  private paletteSignature = "";
  private running = false;
  private readonly channelBuffers = new Map<string, Float32Array>();
  /** Status line, kept separately so a language flip can re-render it. */
  private statusKind:
    | "connecting"
    | "noBackend"
    | "connected"
    | "dirty"
    | "running"
    | "ready"
    | "failed" = "connecting";
  private statusParams: Array<string | number> = [];

  constructor() {
    this.scene = new FieldScene(el<HTMLCanvasElement>("stage"));
    this.chart = new TimelineChart(el("chart"));
    this.chart.onScrub = (index) => this.setDay(index);
    this.scene.onHover = (info) => this.showTooltip(info);

    this.bindControls();
  }

  start(): void {
    // Paint the stored language before the first await: bootstrap bails out
    // early when the backend is down, and the copy must not stay hidden.
    applyDataI18n();
    void this.bootstrap();
  }

  private async bootstrap(): Promise<void> {
    const runButton = el<HTMLButtonElement>("run");
    const overlay = el("overlay");
    const overlayText = el("overlay-text");

    try {
      const info = await health();
      this.setStatus("connected", [info.version]);
    } catch {
      this.setStatus("noBackend");
      overlayText.textContent = t("overlay.noBackend");
      return;
    }

    try {
      this.presets = await listPresets();
    } catch (error) {
      overlayText.textContent = t("overlay.presetsFail", [String(error)]);
      return;
    }

    const select = el<HTMLSelectElement>("preset");
    select.innerHTML = "";
    for (const preset of this.presets) {
      const option = document.createElement("option");
      option.value = preset.id;
      option.textContent = dn(preset);
      option.title = dnd(preset);
      select.append(option);
    }

    // ?preset=salvador-greenhouse deep-links straight into a scenario; without
    // it we open on the scenario the server calls its default.
    const requested = new URLSearchParams(location.search).get("preset");
    const fallback = this.presets.find((preset) => preset.default)?.id ?? this.presets[0]?.id;
    select.value =
      requested && this.presets.some((p) => p.id === requested) ? requested : (fallback ?? "");

    // The crop list and the field-size bounds come from the server so that
    // adding a TOML template or widening a validator needs no client change.
    this.crops = await listCrops().catch(() => []);
    this.limits = await listLimits().catch(() => null);
    this.adoptPreset();
    if (this.limits) {
      for (const [key, input] of [
        ["rows", el<HTMLInputElement>("rows")],
        ["cols", el<HTMLInputElement>("cols")],
        ["days", el<HTMLInputElement>("days")],
      ] as const) {
        const bound = this.limits[key];
        if (!bound) continue;
        input.min = String(bound.min);
        input.max = String(bound.max);
      }
    }

    // Nothing runs until the button is pressed: opening the page should show a
    // field and a configuration, not a progress cover over an empty canvas.
    overlay.hidden = true;
    runButton.disabled = false;

    // Wire the language switch and paint the UI in the stored language.  The
    // options come from the locale registry, so adding one needs no HTML edit.
    const langSelect = el<HTMLSelectElement>("lang");
    langSelect.innerHTML = "";
    for (const { code, label } of langs()) {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = label;
      langSelect.append(option);
    }
    langSelect.value = getLang();
    langSelect.addEventListener("change", (event) => {
      setLang((event.target as HTMLSelectElement).value as Lang);
    });
    applyDataI18n();
    onLangChange(() => this.applyLanguage());
    this.markDirty();
  }

  // --- i18n value resolvers -------------------------------------------------

  /** Localised crop name from a `/api/crops` option. */
  private cropName(crop: CropOption): string {
    return dn(crop);
  }

  /** Localised crop name from a bundle `meta.crops` entry. */
  private metaCropName(entry: {
    label: string;
    label_en?: string | null;
    label_es?: string | null;
  }): string {
    return dn(entry);
  }

  /** Localised phenology-stage name. */
  private stageName(stage: {
    label: string;
    label_en?: string | null;
    label_es?: string | null;
  }): string {
    return dn(stage);
  }

  /** Localised name of a server channel, by key into the current bundle. */
  private channelName(name: string): string {
    const meta = this.bundle?.channels[name];
    return meta ? dn(meta) : name;
  }

  /** Localised soil / terrain name, falling back to the server's raw value. */
  private enumName(group: "soil" | "terrain", value: string): string {
    return tOpt(`${group}.${value}`) ?? value;
  }

  private setStatus(kind: typeof this.statusKind, params: Array<string | number> = []): void {
    this.statusKind = kind;
    this.statusParams = params;
    this.renderStatus();
  }

  private renderStatus(): void {
    const key =
      this.statusKind === "dirty"
        ? this.bundle
          ? "status.dirtyHasBundle"
          : "status.dirtyNoBundle"
        : this.statusKind;
    el("status").textContent = t(key, this.statusParams);
  }

  /** Re-render every language-dependent string after the UI language flips. */
  private applyLanguage(): void {
    applyDataI18n();
    this.renderStatus();
    this.renderGridHint();
    this.renderDaysHint();
    this.renderChannels();
    this.renderPalette();
    this.renderCropMix();
    if (this.bundle) {
      this.renderYield();
      this.renderFieldInfo();
      this.renderLegend();
      this.updateMetrics();
      this.chart.refresh();
      this.setDay(this.index);
    }
  }

  // --------------------------------------------------------------------------

  private bindControls(): void {
    el<HTMLButtonElement>("run").addEventListener("click", () => void this.runScenario());

    el<HTMLSelectElement>("preset").addEventListener("change", () => {
      this.adoptPreset();
      this.markDirty();
    });

    el<HTMLInputElement>("paint-mode").addEventListener("change", (event) => {
      const checked = (event.target as HTMLInputElement).checked;
      this.scene.setPaintEnabled(checked);
    });
    this.scene.setPaintEnabled(el<HTMLInputElement>("paint-mode").checked);

    this.scene.onPick = (row, col) => {
      this.paint(row, col);
      this.markDirty();
    };

    el<HTMLButtonElement>("fill-all").addEventListener("click", () => {
      this.cropMap = this.cropMap.map(() => this.brush);
      this.pushLayout();
      this.markDirty();
    });
    el<HTMLButtonElement>("fill-bands").addEventListener("click", () => {
      const bands = this.crops.filter((crop) => crop.mixable).map((crop) => crop.key);
      if (!bands.length) return;
      this.cropMap = this.cropMap.map((_, cell) => {
        const col = cell % this.cols;
        return bands[
          Math.min(bands.length - 1, Math.floor((col * bands.length) / this.cols))
        ] as string;
      });
      this.pushLayout();
      this.markDirty();
    });
    el<HTMLButtonElement>("reset-crops").addEventListener("click", () => {
      this.adoptPreset();
      this.pushLayout();
      this.markDirty();
    });

    for (const key of ["rows", "cols", "days"] as const) {
      const input = el<HTMLInputElement>(key);
      // `input` keeps the label live and `change` adopts the value; neither
      // simulates — a drag across the slider would otherwise fire a run per
      // pixel, and the button is the only thing that starts one.
      input.addEventListener("input", () => this.syncGridLabel());
      input.addEventListener("change", () => {
        this.applyGridSize();
        this.markDirty();
      });
    }

    el<HTMLButtonElement>("play").addEventListener("click", () => this.togglePlay());
    el<HTMLInputElement>("day").addEventListener("input", (event) => {
      const input = event.target as HTMLInputElement;
      this.setDay(Number(input.value));
    });

    const speed = el<HTMLSelectElement>("speed");
    speed.addEventListener("change", () => {
      this.speed = Number(speed.value);
    });

    const exaggeration = el<HTMLInputElement>("exaggerate");
    exaggeration.addEventListener("input", () => {
      this.scene.setExaggeration(Number(exaggeration.value));
    });

    window.addEventListener("keydown", (event) => {
      if (event.target instanceof HTMLInputElement) return;
      if (event.code === "Space") {
        event.preventDefault();
        this.togglePlay();
      } else if (event.code === "ArrowRight") {
        this.setDay(this.index + 1);
      } else if (event.code === "ArrowLeft") {
        this.setDay(this.index - 1);
      }
    });

    window.addEventListener("resize", () => this.chart.resize());
  }

  /** Take rows/cols/days/crop layout from the preset the UI selected. */
  private adoptPreset(): void {
    const select = el<HTMLSelectElement>("preset");
    const config = (this.presets.find((item) => item.id === select.value)?.config ?? {}) as Record<
      string,
      unknown
    >;

    this.rows = Number(config.rows ?? this.rows) || this.rows;
    this.cols = Number(config.cols ?? this.cols) || this.cols;
    // A preset that names a horizon means it: the season is cut short on
    // purpose.  One that stays silent lets the crops decide instead.
    const givenDays = config.days;
    this.daysPinned = givenDays != null;

    const laid = config.crop_map;
    this.baseCrop = String(config.crop ?? this.baseCrop);
    this.canMix = this.crops.find((crop) => crop.key === this.baseCrop)?.mixable ?? false;
    if (Array.isArray(laid) && laid.length === this.rows * this.cols) {
      this.cropMap = laid.map(String);
    } else {
      // No layout, or one that belongs to another grid size: fall back to the
      // scenario's own crop rather than carrying a mismatched map around.
      const crop = String(config.crop ?? this.crops[0]?.key ?? "tomato");
      this.cropMap = Array.from({ length: this.rows * this.cols }, () => crop);
    }

    if (!this.cropMap.includes(this.brush)) {
      this.brush = this.cropMap[0] ?? this.crops[0]?.key ?? "tomato";
    }
    for (const id of ["fill-all", "fill-bands", "reset-crops"] as const) {
      el<HTMLButtonElement>(id).disabled = !this.canMix;
    }
    el<HTMLInputElement>("paint-mode").disabled = !this.canMix;
    if (this.daysPinned) this.days = Number(givenDays) || this.days;
    this.renderPalette();
    this.syncGrid();
  }

  private applyGridSize(): void {
    const rows = Number(el<HTMLInputElement>("rows").value);
    const cols = Number(el<HTMLInputElement>("cols").value);
    const days = Number(el<HTMLInputElement>("days").value);
    if (!Number.isFinite(rows) || !Number.isFinite(cols) || rows < 1 || cols < 1) return;
    // Only a horizon the user actually moved pins the run length; dragging rows
    // or columns leaves it following the crop.
    if (Number.isFinite(days) && days !== this.days) this.daysPinned = true;
    const from: Grid = { rows: this.rows, cols: this.cols };
    this.cropMap = remapCrops(this.cropMap, from, { rows, cols });
    this.rows = rows;
    this.cols = cols;
    this.days = Number.isFinite(days) ? days : this.days;
    this.syncGrid();
  }

  /** Keep sliders, outputs and the size summary in step with the state. */
  private syncGrid(): void {
    for (const key of ["rows", "cols", "days"] as const) {
      const input = el<HTMLInputElement>(key);
      const value = String(this[key]);
      if (input.value !== value) input.value = value;
      el<HTMLOutputElement>(`${key}-out`).textContent = value;
    }
    this.renderGridHint();
    this.pushLayout();
    this.renderDaysHint();
  }

  /** Size summary under the sliders: cell count, area, and a big-field warning. */
  private renderGridHint(): void {
    const cells = this.rows * this.cols;
    el("grid-hint").textContent =
      `${this.rows} × ${this.cols} = ${cells} ${t("unit.cells")} · ${(cells / 10_000).toFixed(3)} ha` +
      // Measured on the reference machine: a run costs roughly one second per
      // 25 000 cell-days, so the largest field the sliders allow is a minute.
      (cells > 2400 ? t("grid.hintLarge") : "");
  }

  /** Say who decided the horizon: the slowest crop, or the user's slider. */
  private renderDaysHint(): void {
    const season = this.season();
    const crop = season.label || t("crop.fallback");
    el("days-hint").textContent = this.daysPinned
      ? t("days.manual", [this.days, crop, season.days])
      : t("days.follow", [crop, season.days]);
  }

  private syncGridLabel(): void {
    for (const key of ["rows", "cols", "days"] as const) {
      el<HTMLOutputElement>(`${key}-out`).textContent = el<HTMLInputElement>(key).value;
    }
    const rows = Number(el<HTMLInputElement>("rows").value) || 0;
    const cols = Number(el<HTMLInputElement>("cols").value) || 0;
    el("grid-hint").textContent = `${rows} × ${cols} = ${rows * cols} ${t("unit.cells")}${t("grid.hintDrag")}`;
  }

  /** Days the slowest crop planted needs, and which crop that is. */
  private season(): { days: number; label: string } {
    let days = 0;
    let label = "";
    for (const key of this.layoutKeys()) {
      const crop = this.crops.find((item) => item.key === key);
      if (crop && crop.season_days > days) {
        days = crop.season_days;
        label = this.cropName(crop);
      }
    }
    return { days: days || this.days, label };
  }

  /** Keep the horizon on the slowest crop until the user overrides it.
   *
   * A bed of lettuce next to tomato runs as long as the tomato does — cutting
   * it at the lettuce's 60 days would harvest half a crop.
   */
  private followCropSeason(): void {
    if (this.daysPinned) return;
    const { days } = this.season();
    if (days === this.days) return;
    this.days = days;
    el<HTMLInputElement>("days").value = String(days);
    el<HTMLOutputElement>("days-out").textContent = String(days);
  }

  /** Push the local layout into the renderer so painting is visible at once. */
  private pushLayout(): void {
    this.followCropSeason();
    const keys = this.layoutKeys();
    this.scene.setCropPreview(this.cropMap.map((key) => keys.indexOf(key)));
    if (this.channel === CROP_CHANNEL) {
      this.scene.setFrame(this.index);
      this.renderLegend();
    }
    this.renderCropMix();
    // Swatch colours follow the layout, so they only move when the set of
    // planted crops does — not on every cell a drag paints.
    const signature = keys.join(",");
    if (signature !== this.paletteSignature) {
      this.paletteSignature = signature;
      this.renderPalette();
    }
    // Painting changes which crop is the slowest without going through
    // syncGrid, so the hint is refreshed here as well as there.
    this.renderDaysHint();
  }

  /** Crops planted right now, in first-appearance order.
   *
   * Mirrors `crop_keys` on the server (`grid.crop_index` is indexed into it), and
   * is also the order the palette and the legend take their colours from — so it
   * has to stay first-appearance, not alphabetical.
   */
  private layoutKeys(): string[] {
    const keys: string[] = [];
    for (const key of this.cropMap) if (!keys.includes(key)) keys.push(key);
    return keys;
  }

  private paint(row: number, col: number): void {
    if (!this.canMix) return;
    const index = row * this.cols + col;
    if (index < 0 || index >= this.cropMap.length) return;
    if (this.cropMap[index] === this.brush) return;
    this.cropMap[index] = this.brush;
    this.pushLayout();
  }

  /** Note that the field on screen belongs to an older configuration.

   * Layout edits still repaint at once — the point is that they no longer
   * start a simulation by themselves.
   */
  private markDirty(): void {
    el<HTMLButtonElement>("run").classList.add("btn--pending");
    this.setStatus("dirty");
  }

  private buildConfig(): Record<string, unknown> {
    const preset = this.presets.find((item) => item.id === el<HTMLSelectElement>("preset").value);
    const base = (preset?.config ?? {}) as Record<string, unknown>;
    // A field planted start to finish with the scenario's own crop is not a
    // mosaic: sending no map keeps cereal scenarios valid, because their
    // phenology is CropForge's and cannot be planted cell by cell.
    const keys = this.layoutKeys();
    const uniform = keys.length === 1 && keys[0] === String(base.crop ?? "");
    return {
      ...base,
      rows: this.rows,
      cols: this.cols,
      days: this.days,
      crop_map: uniform ? null : [...this.cropMap],
    };
  }

  private async runScenario(): Promise<void> {
    // The largest field takes minutes; the button is disabled for the duration,
    // so this only guards against a second entry point appearing later.
    if (this.running) return;
    this.running = true;

    const runButton = el<HTMLButtonElement>("run");
    const overlay = el("overlay");
    const overlayText = el("overlay-text");

    runButton.disabled = true;
    overlay.hidden = false;
    overlayText.textContent = t("run.overlay", [this.rows, this.cols, this.days]);
    this.setStatus("running");
    this.pause();

    const started = performance.now();
    try {
      const bundle = await createConfiguredRun(this.buildConfig());
      this.applyBundle(bundle);
      const seconds = ((performance.now() - started) / 1000).toFixed(1);
      runButton.classList.remove("btn--pending");
      this.setStatus("ready", [bundle.meta.days, seconds]);
      overlay.hidden = true;
    } catch (error) {
      this.setStatus("failed");
      overlayText.textContent = t("overlay.failed", [String(error)]);
      // Leaving the cover up hides a field that is still perfectly readable,
      // and the timeline would stay paused for no reason.
      overlay.hidden = true;
      if (this.bundle) this.play();
    } finally {
      this.running = false;
      runButton.disabled = false;
    }
  }

  private applyBundle(bundle: Bundle): void {
    this.bundle = bundle;
    this.channelBuffers.clear();
    this.index = 0;
    this.scene.load(bundle, this.channel);
    this.scene.setExaggeration(Number(el<HTMLInputElement>("exaggerate").value));
    this.chart.setData(bundle);
    // The bundle echoes back the layout it ran; if the user has since painted
    // something else their local map wins until their own run returns.
    this.scene.setCropPreview(this.cropMap.map((key) => this.layoutKeys().indexOf(key)));

    const slider = el<HTMLInputElement>("day");
    slider.max = String(bundle.frames.length - 1);
    slider.value = "0";

    this.renderChannels();
    this.renderFieldInfo();
    this.renderYield();
    this.setChannel(this.channel);
    this.setDay(0);
    this.play();
  }

  private renderChannels(): void {
    const bundle = this.bundle;
    const container = el("channels");
    container.innerHTML = "";
    if (!bundle) return;
    const names = CHANNEL_ORDER.filter((name) => name === CROP_CHANNEL || name in bundle.channels);
    for (const name of names) {
      const label =
        name === CROP_CHANNEL ? t("channel.cropLayout") : this.channelName(name);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chip";
      button.textContent = label;
      button.dataset.channel = name;
      button.setAttribute("aria-pressed", String(name === this.channel));
      button.addEventListener("click", () => this.setChannel(name));
      container.append(button);
    }
  }

  /** Brush palette: one chip per crop the engine can plant per cell. */
  private renderPalette(): void {
    const container = el("palette");
    container.innerHTML = "";
    const mixable = this.crops.filter((crop) => crop.mixable);
    const allowed = this.canMix;
    el("layout-hint").textContent = allowed
      ? t("layout.enabledHint")
      : t("layout.disabledHint");
    if (!mixable.length) return;
    // The field colours a cell by its index in *this* layout, not by its index
    // among all mixable crops — otherwise the chip for lettuce is yellow while
    // the bed it paints is brick red.  Crops not planted yet have no layout
    // index, so they take the first colour that is still free.
    const planted = this.layoutKeys();
    const used = new Set(planted.map((_, index) => index));
    mixable.forEach((crop, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chip chip--crop";
      button.dataset.crop = crop.key;
      button.disabled = !allowed;
      button.setAttribute("aria-pressed", String(crop.key === this.brush));
      let swatchIndex = planted.indexOf(crop.key);
      if (swatchIndex < 0) {
        swatchIndex = index;
        while (used.has(swatchIndex)) swatchIndex += 1;
      }
      used.add(swatchIndex);
      const swatch = document.createElement("i");
      swatch.className = "swatch";
      swatch.style.background = CROP_SWATCHES[swatchIndex % CROP_SWATCHES.length] as string;
      button.append(swatch, document.createTextNode(this.cropName(crop)));
      button.addEventListener("click", () => {
        this.brush = crop.key;
        this.renderPalette();
      });
      container.append(button);
    });
  }

  /** How much of the field is planted to what — before and after a run. */
  private renderCropMix(): void {
    const list = el("crop-mix");
    list.innerHTML = "";
    const total = Math.max(1, this.cropMap.length);
    const counts = new Map<string, number>();
    for (const key of this.cropMap) counts.set(key, (counts.get(key) ?? 0) + 1);
    for (const [key, count] of counts) {
      const meta = this.crops.find((crop) => crop.key === key);
      const label = meta ? this.cropName(meta) : key;
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = `${((count / total) * 100).toFixed(0)}% · ${count} 格`;
      list.append(dt, dd);
    }
  }

  private renderYield(): void {
    const bundle = this.bundle;
    const list = el("yield-info");
    list.innerHTML = "";
    if (!bundle) return;
    const rows: [string, string][] = [
      [t("yield.total"), `${bundle.yield.t_per_ha.toFixed(2)} t/ha`],
      [t("yield.gross"), `${bundle.yield.total_kg.toFixed(0)} kg`],
      [t("yield.biomass"), `${bundle.yield.biomass_t_per_ha.toFixed(2)} t/ha`],
      [t("yield.harvestIndex"), bundle.yield.harvest_index.toFixed(2)],
    ];
    for (const row of bundle.yield.crops ?? []) {
      rows.push([
        this.metaCropName(row),
        `${row.t_per_ha.toFixed(2)} t/ha · ${row.cells} ${t("unit.cells")}`,
      ]);
      rows.push([
        t("yield.perCrop"),
        `${row.total_kg.toFixed(0)} kg · ${(row.share * 100).toFixed(0)}%`,
      ]);
    }
    for (const [key, value] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      list.append(dt, dd);
    }
  }

  private setChannel(name: string): void {
    const bundle = this.bundle;
    if (!bundle) return;
    this.channel = name;
    this.scene.setChannel(name);
    this.scene.setFrame(this.index);
    for (const node of document.querySelectorAll<HTMLButtonElement>(".chip")) {
      node.setAttribute("aria-pressed", String(node.dataset.channel === name));
    }
    this.renderLegend();
    this.updateMetrics();
  }

  private renderLegend(): void {
    const bundle = this.bundle;
    if (!bundle) return;
    const meta = bundle.channels[this.channel];
    const bar = el("legend-bar");
    const swatches = el("legend-crops");
    const low = el("legend-low");
    const high = el("legend-high");
    const unit = el("legend-unit");

    if (this.channel === CROP_CHANNEL) {
      // Categorical, not continuous: a ramp would imply an ordering between
      // tomato and lettuce that does not exist.
      bar.hidden = true;
      swatches.hidden = false;
      swatches.innerHTML = "";
      const keys = this.layoutKeys();
      keys.forEach((key, index) => {
        const meta2 = this.crops.find((crop) => crop.key === key);
        const item = document.createElement("span");
        item.className = "legend__crop";
        const dot = document.createElement("i");
        dot.style.background = CROP_SWATCHES[index % CROP_SWATCHES.length] as string;
        item.append(dot, document.createTextNode(meta2 ? this.cropName(meta2) : key));
        swatches.append(item);
      });
      low.textContent = "";
      high.textContent = "";
      unit.textContent = t("legend.crops", [keys.length]);
      return;
    }

    bar.hidden = false;
    swatches.hidden = true;
    if (meta.kind === "stage") {
      bar.style.background = stageCss();
      low.textContent = bundle.stages[0] ? this.stageName(bundle.stages[0]) : "—";
      high.textContent = bundle.stages.at(-1) ? this.stageName(bundle.stages.at(-1)!) : "—";
      unit.textContent = t("legend.stages", [bundle.stages.length]);
      return;
    }
    bar.style.background = rampCss(rampFor(this.channel));
    low.textContent = format(meta.domain[0], 1);
    high.textContent = format(meta.domain[1], 1);
    unit.textContent = meta.unit;
  }

  private renderFieldInfo(): void {
    const bundle = this.bundle;
    const list = el("field-info");
    list.innerHTML = "";
    if (!bundle) return;
    const meta = bundle.meta;
    const planted = this.layoutKeys().length;
    const localCrop = this.crops.find((crop) => crop.key === this.cropMap[0]);
    const cropLabel =
      planted > 1
        ? t("field.mixed", [planted])
        : (localCrop
          ? this.cropName(localCrop)
          : meta.crop_label
            ? dn({
                label: meta.crop_label,
                label_en: meta.crop_label_en,
                label_es: meta.crop_label_es,
              })
            : meta.crop);
    const rows: [string, string][] = [
      [t("field.field"), `${meta.rows} × ${meta.cols} ${t("unit.cells")}`],
      [t("field.area"), `${meta.area_ha.toFixed(3)} ha`],
      [t("field.crop"), cropLabel],
      [t("field.variety"), meta.variety],
      [t("field.days"), `${meta.days} ${t("unit.days")}`],
      [
        t("field.site"),
        `${this.enumName("soil", meta.soil)} · ${this.enumName("terrain", meta.terrain)}`,
      ],
      [t("field.density"), `${meta.plants_per_m2.toFixed(0)} ${t("unit.density")}`],
      [
        t("field.irrigation"),
        meta.irrigation
          ? t("field.irrigDrip", [
              meta.irrigation_mm,
              Math.round(meta.irrigation_coverage * 100),
            ])
          : t("field.irrigRainfed"),
      ],
      [t("field.fert"), `${meta.fertiliser_kg_ha} kg/ha`],
    ];
    if (meta.protected) {
      const transmission = Math.round((meta.cover_transmission ?? 1) * 100);
      rows.splice(4, 0, [t("field.shelter"), t("field.shelterVal", [transmission])]);
    }
    if (meta.weather) {
      const weather = meta.weather;
      const tag =
        weather.source === "open-meteo"
          ? t("field.weatherObserved")
          : t("field.weatherSynthetic");
      rows.splice(4, 0, [t("field.weather"), `${dn(weather)}（${tag}）`]);
    }
    for (const [key, value] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      list.append(dt, dd);
    }
  }

  private setDay(index: number): void {
    const bundle = this.bundle;
    if (!bundle) return;
    const last = bundle.frames.length - 1;
    this.index = Math.max(0, Math.min(last, index));
    this.scene.setFrame(this.index);
    this.chart.setDay(this.index);

    const slider = el<HTMLInputElement>("day");
    slider.value = String(this.index);

    const frame = bundle.frames[this.index];
    const point = bundle.series[this.index];
    el("day-label").textContent = t("day.label", [frame.day]);
    el("date-label").textContent = dateForDoy(frame.doy);
    this.updateMetrics(point);
  }

  private updateMetrics(point = this.bundle?.series[this.index]): void {
    const bundle = this.bundle;
    const list = el("metrics");
    list.innerHTML = "";
    if (!bundle || !point) return;

    const values = this.cellValues(this.channel);
    const meta = bundle.channels[this.channel];
    const rows: [string, string][] = [];
    if (this.channel === CROP_CHANNEL) {
      const mix = new Map<string, number>();
      for (const key of this.cropMap) mix.set(key, (mix.get(key) ?? 0) + 1);
      for (const [key, count] of mix) {
        const crop = this.crops.find((crop) => crop.key === key);
        const label = crop ? this.cropName(crop) : key;
        rows.push([label, `${((count / Math.max(1, this.cropMap.length)) * 100).toFixed(0)}%`]);
      }
    } else {
      const mean = values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
      const min = values.length ? Math.min(...values) : 0;
      const max = values.length ? Math.max(...values) : 0;
      rows.push([this.channelName(this.channel), `${format(mean, 2)} ${meta.unit}`]);
      rows.push([t("metric.spatial"), `${format(min, 2)} – ${format(max, 2)}`]);
    }
    rows.push(
      [t("metric.biomass"), `${format(point.biomass, 1)} ${t("unit.perPlant")}`],
      [t("metric.lai"), format(point.lai, 2)],
      [t("metric.moisture"), `${format(point.moisture, 1)} %`],
      [t("metric.stress"), format(point.stress, 2)],
      [t("metric.temp"), `${format(point.tmean, 1)} °C`],
      [t("metric.rain"), `${format(point.rain, 1)} mm`],
      [t("metric.irrig"), point.irrigation > 0 ? `${format(point.irrigation, 1)} mm` : t("na")],
    );

    for (const [key, value] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      list.append(dt, dd);
    }
  }

  private cellValues(channel: string): Float32Array {
    const bundle = this.bundle;
    if (!bundle) return new Float32Array(0);
    const frame = bundle.frames[this.index];
    const meta = bundle.channels[channel];
    const payload = frame?.ch[channel];
    if (!meta || !payload) return new Float32Array(0);

    let buffer = this.channelBuffers.get(channel);
    if (!buffer || buffer.length !== bundle.grid.rows * bundle.grid.cols) {
      buffer = new Float32Array(bundle.grid.rows * bundle.grid.cols);
      this.channelBuffers.set(channel, buffer);
    }
    return decodeChannel(payload, meta.domain, buffer);
  }

  private showTooltip(info: HoverInfo | null): void {
    const tooltip = el("tooltip");
    const bundle = this.bundle;
    if (!info || !bundle) {
      tooltip.hidden = true;
      return;
    }
    const index = info.row * bundle.grid.cols + info.col;
    const lines: string[] = [
      t("tooltip.cell", [bundle.frames[this.index].day, info.row + 1, info.col + 1]),
    ];
    const planted = this.cropMap[info.row * this.cols + info.col];
    if (planted) {
      const crop = this.crops.find((crop) => crop.key === planted);
      const label = crop ? this.cropName(crop) : planted;
      lines.push(`${t("tooltip.crop")} ${label}`);
    }
    for (const name of ["lai", "biomass", "moisture", "nitrogen", "stress", "disease"]) {
      const values = this.cellValues(name);
      const meta = bundle.channels[name];
      if (!meta) continue;
      lines.push(`${this.channelName(name)} ${format(values[index], 2)} ${meta.unit}`);
    }
    const stage = this.cellValues("stage")[index];
    if (Number.isFinite(stage)) {
      const stageMeta = bundle.stages[Math.round(stage)];
      const label = stageMeta ? this.stageName(stageMeta) : undefined;
      if (label) lines.push(`${t("tooltip.stage")} ${label}`);
    }
    const dot = stageColor(this.cellValues("stage")[index] ?? 0);
    const tone = `rgb(${Math.round(dot[0] * 255)} ${Math.round(dot[1] * 255)} ${Math.round(dot[2] * 255)})`;
    tooltip.innerHTML = `<span style="color:${tone}">●</span> ${lines.join("\n")}`;
    tooltip.hidden = false;
    tooltip.style.left = `${info.clientX}px`;
    tooltip.style.top = `${info.clientY}px`;
  }

  private togglePlay(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  private play(): void {
    if (this.playing) return;
    this.playing = true;
    this.setPlayButton("❚❚", "btn.pause");
    this.lastTick = performance.now();
    requestAnimationFrame(this.tick);
  }

  private pause(): void {
    this.playing = false;
    this.setPlayButton("▶", "btn.play");
  }

  /** Icon button: the glyph is language-free, so the name lives in `aria-label`.
   *
   * The `data-i18n-aria` marker is rewritten too, so switching language while
   * paused keeps saying "Play" and while playing keeps saying "Pause".
   */
  private setPlayButton(glyph: string, key: string): void {
    const button = el<HTMLButtonElement>("play");
    button.textContent = glyph;
    button.dataset.i18nAria = key;
    button.setAttribute("aria-label", t(key));
  }

  private readonly tick = (now: number): void => {
    if (!this.playing) return;
    if (now - this.lastTick >= this.speed) {
      this.lastTick = now;
      const last = (this.bundle?.frames.length ?? 1) - 1;
      if (this.index >= last) this.setDay(0);
      else this.setDay(this.index + 1);
    }
    requestAnimationFrame(this.tick);
  };
}

const app = new App();
app.start();
