/** Wire format returned by `POST /api/runs` — see `rancho/frames.py`. */

export interface ChannelMeta {
  label: string;
  /** English label — the client picks this when the UI language is English. */
  label_en: string;
  /** Spanish label. */
  label_es: string;
  unit: string;
  kind: "continuous" | "stage" | string;
  domain: [number, number];
}

export interface Frame {
  day: number;
  doy: number;
  rain: number;
  irrigation: number;
  /** channel name → base64 of a row-major `rows × cols` uint8 grid. */
  ch: Record<string, string>;
}

export interface SeriesPoint {
  day: number;
  doy: number;
  tmean: number;
  rain: number;
  irrigation: number;
  et0: number;
  lai: number;
  biomass: number;
  stress: number;
  moisture: number;
  nitrogen: number;
  alive: number;
}

export interface WeatherMeta {
  source: "synthetic" | "open-meteo";
  label: string;
  /** English weather label. */
  label_en?: string;
  /** Spanish weather label. */
  label_es?: string;
  provider: string;
  latitude: number | null;
  longitude: number | null;
  grid: [number, number] | null;
  elevation_m: number | null;
  timezone: string | null;
  start: string;
  end: string;
  days: number;
  cached: boolean;
  notes: string[];
  mean_temp_c: number;
  min_temp_c: number;
  max_temp_c: number;
  mean_radiation_mj: number;
  rain_total_mm: number;
  rain_days: number;
  mean_humidity_pct: number;
  mean_wind_ms: number;
}

/** One selectable crop — what `/api/crops` returns. */
export interface CropOption {
  key: string;
  label: string;
  /** English display name. */
  label_en?: string | null;
  /** Spanish display name. */
  label_es?: string | null;
  species: string;
  mixable: boolean;
  season_gdd: number;
  /** Days from sowing to maturity — the horizon follows the slowest crop. */
  season_days: number;
  root_zone_mm: number;
  has_market: boolean;
}

/** Per-crop harvest ledger inside the bundle. */
export interface CropYield {
  key: string;
  label: string;
  /** English display name. */
  label_en?: string | null;
  /** Spanish display name. */
  label_es?: string | null;
  cells: number;
  share: number;
  harvest_index: number;
  biomass_t_per_ha: number;
  t_per_ha: number;
  total_kg: number;
}

export interface RunMeta {
  name: string;
  rows: number;
  cols: number;
  days: number;
  crop: string;
  crop_label?: string;
  /** English crop name. */
  crop_label_en?: string;
  /** Spanish crop name. */
  crop_label_es?: string;
  variety: string;
  sowing_doy: number;
  soil: string;
  climate: string;
  terrain: string;
  slope_pct?: number;
  protected?: boolean;
  cover_transmission?: number;
  irrigation: boolean;
  irrigation_mm: number;
  irrigation_coverage: number;
  fertiliser_kg_ha: number;
  plants_per_m2: number;
  area_ha: number;
  seed: number;
  elapsed_s: number;
  weather?: WeatherMeta;
  /** True when the run planted more than one crop. */
  mosaic?: boolean;
  /** Every crop planted, in the order `grid.crop_index` indexes into. */
  crops?: { key: string; label: string; label_en?: string; label_es?: string }[];
}

export interface YieldSummary {
  t_per_ha: number;
  total_kg: number;
  biomass_t_per_ha: number;
  harvest_index: number;
  engine_t_per_ha: number;
  crops: CropYield[];
}

export interface Bundle {
  id: string;
  created_at: string;
  meta: RunMeta;
  grid: {
    rows: number;
    cols: number;
    cell_size_m: number;
    area_ha: number;
    plant_spacing_m: number;
    row_spacing_m: number;
    elevation: number[];
    /** Per-cell index into `meta.crops`, row-major. */
    crop_index?: number[];
  };
  channels: Record<string, ChannelMeta>;
  stages: { key: string; label: string; label_en?: string; label_es?: string }[];
  series: SeriesPoint[];
  frames: Frame[];
  yield: YieldSummary;
}

export interface Preset {
  id: string;
  label: string;
  /** English scenario name. */
  label_en?: string;
  /** Spanish scenario name. */
  label_es?: string;
  description: string;
  /** English scenario description. */
  description_en?: string;
  /** Spanish scenario description. */
  description_es?: string;
  config: Record<string, unknown>;
  /** The scenario the server opens on. */
  default?: boolean;
}

/** Field-size bounds from `/api/limits` — what the sliders run between. */
export interface Limits {
  rows: { min: number; max: number; default: number };
  cols: { min: number; max: number; default: number };
  days: { min: number; max: number; default: number };
}

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const LOOKUP = new Uint8Array(256);
for (let i = 0; i < B64.length; i += 1) LOOKUP[B64.charCodeAt(i)] = i;

/** Decode a base64 string into bytes without touching `atob` (keeps it fast). */
export function decodeBytes(input: string): Uint8Array {
  let length = input.length;
  while (length > 0 && input.charCodeAt(length - 1) === 61) length -= 1;

  const out = new Uint8Array((length * 3) >> 2);
  let acc = 0;
  let bits = 0;
  let offset = 0;

  for (let i = 0; i < length; i += 1) {
    acc = (acc << 6) | LOOKUP[input.charCodeAt(i)];
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[offset] = (acc >> bits) & 0xff;
      offset += 1;
    }
  }
  return out;
}

/** Decode one channel of one day into float values, scattered into `target`. */
export function decodeChannel(
  payload: string,
  domain: [number, number],
  target: Float32Array,
): Float32Array {
  const bytes = decodeBytes(payload);
  const [low, high] = domain;
  const span = (high - low) / 255;
  const count = Math.min(bytes.length, target.length);
  for (let i = 0; i < count; i += 1) target[i] = low + bytes[i] * span;
  return target;
}

export const CROP_LABELS: Record<string, string> = {
  wheat: "冬小麦",
  maize: "玉米",
};

export const STAGE_COLORS: string[] = [
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
