import p5 from "p5";

import type { Bundle, SeriesPoint } from "./types";

interface Row {
  key: keyof SeriesPoint;
  label: string;
  unit: string;
  color: string;
  mode: "line" | "bar";
  /** Optional fixed domain, otherwise derived from the data. */
  domain?: [number, number];
}

const ROWS: Row[] = [
  { key: "lai", label: "LAI", unit: "", color: "#a4d96c", mode: "line", domain: [0, 3] },
  { key: "moisture", label: "含水率", unit: "%", color: "#6fbfd8", mode: "line" },
  { key: "stress", label: "胁迫", unit: "", color: "#e8b04b", mode: "line", domain: [0, 1] },
  { key: "rain", label: "降水", unit: "mm", color: "#7fb2d8", mode: "bar" },
];

const PADDING = { left: 48, right: 10, top: 8, bottom: 16 };
const ROW_HEIGHT = 36;
const ROW_GAP = 9;

/** Stacked sparkline panel: one row per metric, shared day axis. */
export class TimelineChart {
  private readonly p: p5;
  private bundle: Bundle | null = null;
  private day = 0;
  private hoverDay: number | null = null;

  onScrub: ((index: number) => void) | null = null;

  constructor(
    container: HTMLElement,
    private readonly onHoverDay?: (index: number | null) => void,
  ) {
    const sketch = (instance: p5): void => {
      instance.setup = () => {
        const width = container.clientWidth || 280;
        const height = ROWS.length * (ROW_HEIGHT + ROW_GAP) + PADDING.top + PADDING.bottom;
        const canvas = instance.createCanvas(width, height);
        canvas.parent(container);
        instance.pixelDensity(Math.min(window.devicePixelRatio, 2));
        instance.textFont("ui-monospace, monospace", 9);
        instance.noLoop();
      };

      instance.draw = () => {
        instance.clear();
        if (!this.bundle) return;
        this.render(instance);
      };

      instance.mouseMoved = () => {
        this.updateHover(instance);
      };

      instance.mouseDragged = () => {
        this.updateHover(instance, true);
      };

      instance.mousePressed = () => {
        this.updateHover(instance, true);
      };
    };

    this.p = new p5(sketch);
  }

  setData(bundle: Bundle): void {
    this.bundle = bundle;
    this.day = 0;
    this.p.redraw();
  }

  setDay(index: number): void {
    this.day = index;
    this.p.redraw();
  }

  resize(): void {
    const width = this.p.width || 280;
    const height = ROWS.length * (ROW_HEIGHT + ROW_GAP) + PADDING.top + PADDING.bottom;
    this.p.resizeCanvas(width, height);
    this.p.redraw();
  }

  private updateHover(instance: p5, scrub = false): void {
    if (!this.bundle) return;
    const inside =
      instance.mouseX > PADDING.left &&
      instance.mouseX < instance.width - PADDING.right &&
      instance.mouseY > PADDING.top;
    if (!inside) {
      if (this.hoverDay !== null) {
        this.hoverDay = null;
        this.onHoverDay?.(null);
        instance.redraw();
      }
      return;
    }
    const count = this.bundle.series.length;
    const plotWidth = instance.width - PADDING.left - PADDING.right;
    const ratio = (instance.mouseX - PADDING.left) / plotWidth;
    const index = Math.max(0, Math.min(count - 1, Math.round(ratio * (count - 1))));
    this.hoverDay = index;
    this.onHoverDay?.(index);
    if (scrub) this.onScrub?.(index);
    instance.redraw();
  }

  private render(instance: p5): void {
    const bundle = this.bundle;
    if (!bundle) return;
    const series = bundle.series;
    const count = series.length;
    const plotWidth = instance.width - PADDING.left - PADDING.right;
    const xAt = (index: number): number =>
      PADDING.left + (index / Math.max(1, count - 1)) * plotWidth;

    instance.push();
    instance.noStroke();

    ROWS.forEach((row, rowIndex) => {
      const top = PADDING.top + rowIndex * (ROW_HEIGHT + ROW_GAP);
      const values = series.map((point) => Number(point[row.key] ?? 0));
      const rawMax = row.domain ? row.domain[1] : Math.max(...values, 0.001);
      const rawMin = row.domain ? row.domain[0] : Math.min(...values, 0);
      const max = rawMax === rawMin ? rawMin + 1 : rawMax;
      const yAt = (value: number): number =>
        top + ROW_HEIGHT - ((value - rawMin) / (max - rawMin)) * ROW_HEIGHT;

      // panel background + baseline
      instance.fill(255, 255, 255, 6);
      instance.rect(PADDING.left, top, plotWidth, ROW_HEIGHT, 4);
      instance.stroke(255, 255, 255, 26);
      instance.strokeWeight(1);
      instance.line(PADDING.left, top + ROW_HEIGHT, PADDING.left + plotWidth, top + ROW_HEIGHT);
      instance.noStroke();

      if (row.mode === "bar") {
        const barWidth = Math.max(1.2, plotWidth / count - 0.6);
        values.forEach((value, index) => {
          if (value <= 0) return;
          const height = Math.max(1, (value / (max || 1)) * ROW_HEIGHT);
          instance.fill(row.color);
          instance.rect(xAt(index) - barWidth / 2, top + ROW_HEIGHT - height, barWidth, height, 1);
        });
      } else {
        instance.noFill();
        instance.stroke(row.color);
        instance.strokeWeight(1.5);
        instance.beginShape();
        values.forEach((value, index) => instance.vertex(xAt(index), yAt(value)));
        instance.endShape();
        // light area fill under the curve
        instance.noStroke();
        const area = instance.color(row.color);
        area.setAlpha(34);
        instance.fill(area);
        instance.beginShape();
        values.forEach((value, index) => instance.vertex(xAt(index), yAt(value)));
        instance.vertex(xAt(count - 1), top + ROW_HEIGHT);
        instance.vertex(xAt(0), top + ROW_HEIGHT);
        instance.endShape(instance.CLOSE);
      }

      instance.fill(140, 160, 148);
      instance.textAlign(instance.RIGHT, instance.CENTER);
      instance.text(row.label, PADDING.left - 8, top + ROW_HEIGHT / 2);
      instance.textAlign(instance.RIGHT, instance.TOP);
      instance.fill(110, 130, 118);
      instance.text(
        `${max >= 100 ? max.toFixed(0) : max.toFixed(max >= 10 ? 1 : 2)}${row.unit}`,
        instance.width - PADDING.right - 3,
        top + 1,
      );
    });

    // current day marker
    instance.stroke(232, 243, 200, 200);
    instance.strokeWeight(1);
    instance.line(xAt(this.day), PADDING.top, xAt(this.day), instance.height - PADDING.bottom);
    instance.noStroke();
    instance.fill(232, 243, 200);
    instance.circle(xAt(this.day), PADDING.top, 3.5);

    if (this.hoverDay !== null) {
      instance.stroke(255, 255, 255, 70);
      instance.strokeWeight(1);
      instance.line(
        xAt(this.hoverDay),
        PADDING.top,
        xAt(this.hoverDay),
        instance.height - PADDING.bottom,
      );
      instance.noStroke();
    }

    // x axis labels
    instance.fill(95, 115, 103);
    instance.textAlign(instance.LEFT, instance.BOTTOM);
    instance.text("第 1 天", PADDING.left, instance.height - 3);
    instance.textAlign(instance.RIGHT, instance.BOTTOM);
    instance.text(`第 ${count} 天`, instance.width - PADDING.right, instance.height - 3);
    instance.pop();
  }
}
