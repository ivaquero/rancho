import "./style.css";

import { createRun, health, listPresets } from "./api";
import { TimelineChart } from "./chart";
import { stageColor, stageCss, rampCss, rampFor } from "./palette";
import { FieldScene, type HoverInfo } from "./scene";
import { decodeChannel, type Bundle, type Preset } from "./types";

const CHANNEL_ORDER = [
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
  return `${date.getUTCMonth() + 1} 月 ${date.getUTCDate()} 日`;
}

class App {
  private readonly scene: FieldScene;
  private readonly chart: TimelineChart;
  private bundle: Bundle | null = null;
  private presets: Preset[] = [];
  private channel = "lai";
  private index = 0;
  private playing = false;
  private speed = 90;
  private lastTick = 0;
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

    overlay.hidden = false;
    runButton.disabled = true;
    overlayText.textContent = "正在运行首次仿真（约十秒）…";
    try {
      const bundle = await createRun(select.value);
      this.applyBundle(bundle);
      overlay.hidden = true;
    } catch (error) {
      overlayText.textContent = `仿真失败：${String(error)}`;
    } finally {
      runButton.disabled = false;
    }
  }

  private bindControls(): void {
    el<HTMLButtonElement>("run").addEventListener("click", () => void this.runPreset());

    el<HTMLSelectElement>("preset").addEventListener("change", () => {
      void this.runPreset();
    });

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

  private async runPreset(): Promise<void> {
    const select = el<HTMLSelectElement>("preset");
    const runButton = el<HTMLButtonElement>("run");
    const overlay = el("overlay");
    const overlayText = el("overlay-text");
    const status = el("status");

    runButton.disabled = true;
    overlay.hidden = false;
    overlayText.textContent = "正在运行仿真…";
    status.textContent = "仿真运行中…";
    this.pause();

    const started = performance.now();
    try {
      const bundle = await createRun(select.value);
      this.applyBundle(bundle);
      const seconds = ((performance.now() - started) / 1000).toFixed(1);
      status.textContent = `就绪 · ${bundle.meta.days} 天 · ${seconds} 秒`;
      overlay.hidden = true;
    } catch (error) {
      overlayText.textContent = `仿真失败：${String(error)}`;
      status.textContent = "仿真失败";
    } finally {
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

    const slider = el<HTMLInputElement>("day");
    slider.max = String(bundle.frames.length - 1);
    slider.value = "0";

    this.renderChannels();
    this.renderFieldInfo();
    this.setChannel(this.channel);
    this.setDay(0);
    this.play();
  }

  private renderChannels(): void {
    const bundle = this.bundle;
    const container = el("channels");
    container.innerHTML = "";
    if (!bundle) return;
    const names = CHANNEL_ORDER.filter((name) => name in bundle.channels);
    for (const name of names) {
      const meta = bundle.channels[name];
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chip";
      button.textContent = meta.label;
      button.dataset.channel = name;
      button.setAttribute("aria-pressed", String(name === this.channel));
      button.addEventListener("click", () => this.setChannel(name));
      container.append(button);
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
    const low = el("legend-low");
    const high = el("legend-high");
    const unit = el("legend-unit");

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
    const soil: Record<string, string> = { loam: "壤土", clay: "黏土", sandy: "沙壤" };
    const rows: [string, string][] = [
      ["地块", `${meta.rows} × ${meta.cols} 格`],
      ["面积", `${meta.area_ha.toFixed(3)} ha`],
      ["品种", meta.variety],
      ["立地", `${soil[meta.soil] ?? meta.soil} · ${terrain[meta.terrain] ?? meta.terrain}`],
      ["密度", `${meta.plants_per_m2.toFixed(0)} 株/m²`],
      [
        "灌溉",
        meta.irrigation
          ? `定值 ${meta.irrigation_mm} mm · 覆盖 ${Math.round(meta.irrigation_coverage * 100)}%`
          : "雨养",
      ],
      ["施氮", `${meta.fertiliser_kg_ha} kg/ha`],
      ["产量", `${bundle.yield.t_per_ha.toFixed(2)} t/ha`],
    ];
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
    const mean = values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
    const min = values.length ? Math.min(...values) : 0;
    const max = values.length ? Math.max(...values) : 0;
    const meta = bundle.channels[this.channel];

    const rows: [string, string][] = [
      [meta.label, `${format(mean, 2)} ${meta.unit}`],
      ["空间范围", `${format(min, 2)} – ${format(max, 2)}`],
      ["生物量", `${format(point.biomass, 1)} g/株`],
      ["含水率", `${format(point.moisture, 1)} %`],
      ["胁迫指数", format(point.stress, 2)],
      ["气温", `${format(point.tmean, 1)} °C`],
      ["降水", `${format(point.rain, 1)} mm`],
      ["灌溉", point.irrigation > 0 ? `${format(point.irrigation, 1)} mm` : "—"],
    ];

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
