/** Colour ramps for the field surface and the plant instances. */

export type Rgb = [number, number, number];

export interface Ramp {
  /** Stops from low to high, each `[r, g, b]` in 0–1. */
  stops: Rgb[];
}

// Bare soil → tillering → closed canopy: the low end has to look like ground,
// otherwise an early-season field renders as a black slab.
const CANOPY: Ramp = {
  stops: [
    [0.29, 0.21, 0.14],
    [0.42, 0.36, 0.19],
    [0.54, 0.55, 0.24],
    [0.68, 0.76, 0.31],
    [0.85, 0.92, 0.52],
  ],
};

const MOISTURE: Ramp = {
  stops: [
    [0.25, 0.16, 0.11],
    [0.48, 0.33, 0.17],
    [0.35, 0.48, 0.42],
    [0.2, 0.56, 0.68],
    [0.62, 0.88, 0.95],
  ],
};

const NITROGEN: Ramp = {
  stops: [
    [0.16, 0.14, 0.24],
    [0.31, 0.24, 0.44],
    [0.51, 0.38, 0.66],
    [0.72, 0.56, 0.85],
    [0.93, 0.84, 1.0],
  ],
};

const STRESS: Ramp = {
  stops: [
    [0.14, 0.28, 0.2],
    [0.42, 0.6, 0.28],
    [0.85, 0.72, 0.28],
    [0.89, 0.52, 0.22],
    [0.82, 0.24, 0.16],
  ],
};

const DISEASE: Ramp = {
  stops: [
    [0.13, 0.16, 0.21],
    [0.28, 0.27, 0.38],
    [0.47, 0.28, 0.5],
    [0.68, 0.27, 0.52],
    [0.94, 0.42, 0.62],
  ],
};

const RAMPS: Record<string, Ramp> = {
  lai: CANOPY,
  biomass: CANOPY,
  height: CANOPY,
  moisture: MOISTURE,
  nitrogen: NITROGEN,
  stress: STRESS,
  disease: DISEASE,
};

export function rampFor(channel: string): Ramp {
  return RAMPS[channel] ?? CANOPY;
}

/** Sample a ramp at `t` in 0–1 with linear interpolation between stops. */
export function sampleRamp(ramp: Ramp, t: number): Rgb {
  const clamped = t <= 0 ? 0 : t >= 1 ? 1 : t;
  const scaled = clamped * (ramp.stops.length - 1);
  const index = Math.min(ramp.stops.length - 2, Math.floor(scaled));
  const frac = scaled - index;
  const a = ramp.stops[index];
  const b = ramp.stops[index + 1];
  return [a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac, a[2] + (b[2] - a[2]) * frac];
}

export function rampCss(ramp: Ramp, steps = 12): string {
  const parts: string[] = [];
  for (let i = 0; i < steps; i += 1) {
    const [r, g, b] = sampleRamp(ramp, i / (steps - 1));
    parts.push(`rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`);
  }
  return `linear-gradient(to right, ${parts.join(", ")})`;
}

const STAGE_HEX = [
  "#4d6b3a",
  "#6f9c46",
  "#8fc35a",
  "#b4d96b",
  "#d8e88a",
  "#e8c95f",
  "#e0a04a",
  "#d0803f",
  "#b05f3a",
  "#8a4a34",
];

/** One colour per crop, for the layout channel and the brush palette. */
export const CROP_SWATCHES: string[] = [
  "#c85a3c",
  "#5aa36a",
  "#d0a63c",
  "#5b86bd",
  "#a462ac",
  "#4fa39a",
  "#b3714a",
  "#7f8f4e",
];

export function cropColor(index: number): Rgb {
  const hex = CROP_SWATCHES[Math.max(0, index) % CROP_SWATCHES.length] ?? "#888888";
  return [
    parseInt(hex.slice(1, 3), 16) / 255,
    parseInt(hex.slice(3, 5), 16) / 255,
    parseInt(hex.slice(5, 7), 16) / 255,
  ];
}

export function stageColor(index: number): Rgb {
  const hex = STAGE_HEX[Math.min(STAGE_HEX.length - 1, Math.max(0, Math.round(index)))];
  return [
    parseInt(hex.slice(1, 3), 16) / 255,
    parseInt(hex.slice(3, 5), 16) / 255,
    parseInt(hex.slice(5, 7), 16) / 255,
  ];
}

export function stageCss(): string {
  const parts = STAGE_HEX.map((hex) => hex);
  return `linear-gradient(to right, ${parts.join(", ")})`;
}
