import "./style.css";

import { createConfiguredRun, health, listCrops, listLimits, listPresets } from "./api";
import { TimelineChart } from "./chart";
import { CROP_SWATCHES, stageColor, stageCss, rampCss, rampFor } from "./palette";
import { CROP_CHANNEL, FieldScene, type HoverInfo } from "./scene";
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

const CHANNEL_LABELS: Record<string, string> = { [CROP_CHANNEL]: "作物布局" };

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
  return `${date.getUTCMonth() + 1} 月 ${date.getUTCDate()} 日`;
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
  private paletteSignature = "";
  private rerunTimer: number | null = null;
  private running = false;
  private rerunQueued = false;
  private readonly channelBuffers = new Map<string, Float32Array>();

  constructor() {
    this.scene = new FieldScene(el<HTMLCanvasElement>("stage"));
    this.chart = new TimelineChart(el("chart"));
    this.chart.onScrub = (index) => this.setDay(index);
    this.scene.onHover = (info) => this.showTooltip(info);

    this.bindControls();
  }

  start(): void {
    void this.bootstrap();
  }

  private async bootstrap(): Promise<void> {
    const status = el("status");
    const runButton = el<HTMLButtonElement>("run");
    const overlay = el("overlay");
    const overlayText = el("overlay-text");

    try {
      const info = await health();
      status.textContent = `服务已连接 · v${info.version}`;
    } catch {
      status.textContent = "未连接后端（请先运行 rancho serve）";
      overlayText.textContent = "无法连接仿真服务。请在项目根目录执行 rancho serve 后刷新。";
      return;
    }

    try {
      this.presets = await listPresets();
    } catch (error) {
      overlayText.textContent = `读取场景失败：${String(error)}`;
      return;
    }

    const select = el<HTMLSelectElement>("preset");
    select.innerHTML = "";
    for (const preset of this.presets) {
      const option = document.createElement("option");
      option.value = preset.id;
      option.textContent = preset.label;
      option.title = preset.description;
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

    overlay.hidden = false;
    runButton.disabled = true;
    overlayText.textContent = "正在运行首次仿真（约十秒）…";
    try {
      await this.runScenario();
      overlay.hidden = true;
    } catch (error) {
      overlayText.textContent = `仿真失败：${String(error)}`;
    } finally {
      runButton.disabled = false;
    }
  }

  private bindControls(): void {
    el<HTMLButtonElement>("run").addEventListener("click", () => void this.runScenario());

    el<HTMLSelectElement>("preset").addEventListener("change", () => {
      this.adoptPreset();
      void this.runScenario();
    });

    el<HTMLInputElement>("paint-mode").addEventListener("change", (event) => {
      const checked = (event.target as HTMLInputElement).checked;
      this.scene.setPaintEnabled(checked);
    });
    this.scene.setPaintEnabled(el<HTMLInputElement>("paint-mode").checked);

    this.scene.onPick = (row, col) => {
      this.paint(row, col);
      this.queueRerun();
    };

    el<HTMLButtonElement>("fill-all").addEventListener("click", () => {
      this.cropMap = this.cropMap.map(() => this.brush);
      this.pushLayout();
      this.queueRerun();
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
      this.queueRerun();
    });
    el<HTMLButtonElement>("reset-crops").addEventListener("click", () => {
      this.adoptPreset();
      this.pushLayout();
      this.queueRerun();
    });

    for (const key of ["rows", "cols", "days"] as const) {
      const input = el<HTMLInputElement>(key);
      // `input` keeps the label live; `change` is what triggers a run, so a
      // drag across the slider does not fire one simulation per pixel.
      input.addEventListener("input", () => this.syncGridLabel());
      input.addEventListener("change", () => {
        this.applyGridSize();
        this.queueRerun();
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
    this.days = Number(config.days ?? this.days) || this.days;

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
    this.renderPalette();
    this.syncGrid();
  }

  private applyGridSize(): void {
    const rows = Number(el<HTMLInputElement>("rows").value);
    const cols = Number(el<HTMLInputElement>("cols").value);
    const days = Number(el<HTMLInputElement>("days").value);
    if (!Number.isFinite(rows) || !Number.isFinite(cols) || rows < 1 || cols < 1) return;
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
    const cells = this.rows * this.cols;
    el("grid-hint").textContent =
      `${this.rows} × ${this.cols} = ${cells} 格 · ${(cells / 10_000).toFixed(3)} ha` +
      // Measured on the reference machine: a run costs roughly one second per
      // 25 000 cell-days, so the largest field the sliders allow is a minute.
      (cells > 2400 ? " · 格数较多，跑一次要十几秒到一分钟" : "");
    this.pushLayout();
  }

  private syncGridLabel(): void {
    for (const key of ["rows", "cols", "days"] as const) {
      el<HTMLOutputElement>(`${key}-out`).textContent = el<HTMLInputElement>(key).value;
    }
    const rows = Number(el<HTMLInputElement>("rows").value) || 0;
    const cols = Number(el<HTMLInputElement>("cols").value) || 0;
    el("grid-hint").textContent = `${rows} × ${cols} = ${rows * cols} 格（松开后重跑）`;
  }

  /** Push the local layout into the renderer so painting is visible at once. */
  private pushLayout(): void {
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

  private queueRerun(): void {
    if (this.rerunTimer !== null) window.clearTimeout(this.rerunTimer);
    // Painting fires per cell; collapsing them into one run keeps a drag from
    // queueing a dozen sims.
    this.rerunTimer = window.setTimeout(() => {
      this.rerunTimer = null;
      void this.runScenario();
    }, 450);
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
    // The largest field takes minutes and the engine serialises runs anyway, so
    // a second request would only queue behind the first and make both late.
    // Remember that the configuration moved on and re-run once this one lands.
    if (this.running) {
      this.rerunQueued = true;
      return;
    }
    this.running = true;

    const runButton = el<HTMLButtonElement>("run");
    const overlay = el("overlay");
    const overlayText = el("overlay-text");
    const status = el("status");

    runButton.disabled = true;
    overlay.hidden = false;
    overlayText.textContent = `正在运行仿真：${this.rows} × ${this.cols} 格 · ${this.days} 天…`;
    status.textContent = "仿真运行中…";
    this.pause();

    const started = performance.now();
    try {
      const bundle = await createConfiguredRun(this.buildConfig());
      this.applyBundle(bundle);
      const seconds = ((performance.now() - started) / 1000).toFixed(1);
      status.textContent = `就绪 · ${bundle.meta.days} 天 · ${seconds} 秒`;
      overlay.hidden = true;
    } catch (error) {
      status.textContent = "仿真失败";
      overlayText.textContent = `仿真失败：${String(error)}`;
      // Leaving the cover up hides a field that is still perfectly readable,
      // and the timeline would stay paused for no reason.
      overlay.hidden = true;
      if (this.bundle) this.play();
    } finally {
      this.running = false;
      runButton.disabled = false;
      if (this.rerunQueued) {
        this.rerunQueued = false;
        void this.runScenario();
      }
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
      const label = CHANNEL_LABELS[name] ?? bundle.channels[name]?.label ?? name;
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
      ? "点击或拖拽地块给格子换作物，改动后自动重跑。"
      : "该场景的作物物候由 CropForge 插件整块推进，不支持逐格混作。";
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
      button.append(swatch, document.createTextNode(crop.label));
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
      const label = meta?.label ?? key;
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
      ["合计", `${bundle.yield.t_per_ha.toFixed(2)} t/ha`],
      ["总产", `${bundle.yield.total_kg.toFixed(0)} kg`],
      ["生物量", `${bundle.yield.biomass_t_per_ha.toFixed(2)} t/ha`],
      ["收获指数", bundle.yield.harvest_index.toFixed(2)],
    ];
    for (const row of bundle.yield.crops ?? []) {
      rows.push([row.label, `${row.t_per_ha.toFixed(2)} t/ha · ${row.cells} 格`]);
      rows.push(["└ 产量", `${row.total_kg.toFixed(0)} kg · ${(row.share * 100).toFixed(0)}%`]);
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
        item.append(dot, document.createTextNode(meta2?.label ?? key));
        swatches.append(item);
      });
      low.textContent = "";
      high.textContent = "";
      unit.textContent = `${keys.length} 种作物`;
      return;
    }

    bar.hidden = false;
    swatches.hidden = true;
    if (meta.kind === "stage") {
      bar.style.background = stageCss();
      low.textContent = bundle.stages[0]?.label ?? "—";
      high.textContent = bundle.stages.at(-1)?.label ?? "—";
      unit.textContent = `${bundle.stages.length} 个阶段`;
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
    const terrain: Record<string, string> = {
      flat: "平地",
      slope: "缓坡",
      undulating: "起伏",
    };
    const soil: Record<string, string> = {
      loam: "壤土",
      clay: "黏土",
      sandy: "沙壤",
      andisol: "安山土",
    };
    const planted = this.layoutKeys().length;
    const cropLabel =
      planted > 1
        ? `${planted} 种混作`
        : (this.crops.find((crop) => crop.key === this.cropMap[0])?.label ??
          meta.crop_label ??
          meta.crop);
    const rows: [string, string][] = [
      ["地块", `${meta.rows} × ${meta.cols} 格`],
      ["面积", `${meta.area_ha.toFixed(3)} ha`],
      ["作物", cropLabel],
      ["品种", meta.variety],
      ["天数", `${meta.days} 天`],
      ["立地", `${soil[meta.soil] ?? meta.soil} · ${terrain[meta.terrain] ?? meta.terrain}`],
      ["密度", `${meta.plants_per_m2.toFixed(0)} 株/m²`],
      [
        "灌溉",
        meta.irrigation
          ? `定值 ${meta.irrigation_mm} mm · 覆盖 ${Math.round(meta.irrigation_coverage * 100)}%`
          : "雨养",
      ],
      ["施氮", `${meta.fertiliser_kg_ha} kg/ha`],
    ];
    if (meta.protected) {
      const transmission = Math.round((meta.cover_transmission ?? 1) * 100);
      rows.splice(4, 0, ["设施", `避雨遮阳 · 透光 ${transmission}%`]);
    }
    if (meta.weather) {
      const weather = meta.weather;
      const tag = weather.source === "open-meteo" ? "实测" : "合成";
      rows.splice(4, 0, ["气象", `${weather.label}（${tag}）`]);
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
    el("day-label").textContent = `第 ${frame.day} 天`;
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
        const label = this.crops.find((crop) => crop.key === key)?.label ?? key;
        rows.push([label, `${((count / Math.max(1, this.cropMap.length)) * 100).toFixed(0)}%`]);
      }
    } else {
      const mean = values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
      const min = values.length ? Math.min(...values) : 0;
      const max = values.length ? Math.max(...values) : 0;
      rows.push([meta.label, `${format(mean, 2)} ${meta.unit}`]);
      rows.push(["空间范围", `${format(min, 2)} – ${format(max, 2)}`]);
    }
    rows.push(
      ["生物量", `${format(point.biomass, 1)} g/株`],
      ["叶面积", format(point.lai, 2)],
      ["含水率", `${format(point.moisture, 1)} %`],
      ["胁迫指数", format(point.stress, 2)],
      ["气温", `${format(point.tmean, 1)} °C`],
      ["降水", `${format(point.rain, 1)} mm`],
      ["灌溉", point.irrigation > 0 ? `${format(point.irrigation, 1)} mm` : "—"],
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
      `第 ${bundle.frames[this.index].day} 天 · 行 ${info.row + 1} / 列 ${info.col + 1}`,
    ];
    const planted = this.cropMap[info.row * this.cols + info.col];
    if (planted) {
      const label = this.crops.find((crop) => crop.key === planted)?.label ?? planted;
      lines.push(`作物 ${label}`);
    }
    for (const name of ["lai", "biomass", "moisture", "nitrogen", "stress", "disease"]) {
      const values = this.cellValues(name);
      const meta = bundle.channels[name];
      if (!meta) continue;
      lines.push(`${meta.label} ${format(values[index], 2)} ${meta.unit}`);
    }
    const stage = this.cellValues("stage")[index];
    if (Number.isFinite(stage)) {
      const label = bundle.stages[Math.round(stage)]?.label;
      if (label) lines.push(`阶段 ${label}`);
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
    el("play").textContent = "❚❚";
    this.lastTick = performance.now();
    requestAnimationFrame(this.tick);
  }

  private pause(): void {
    this.playing = false;
    el("play").textContent = "▶";
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
