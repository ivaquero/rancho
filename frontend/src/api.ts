import type { Bundle, CropOption, Limits, Preset } from "./types";

const BASE = import.meta.env.DEV ? "" : "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return (await response.json()) as T;
}

export function listPresets(): Promise<Preset[]> {
  return request<Preset[]>("/api/presets");
}

export function listCrops(): Promise<CropOption[]> {
  return request<CropOption[]>("/api/crops");
}

export function listLimits(): Promise<Limits> {
  return request<Limits>("/api/limits");
}

export function createRun(presetId: string): Promise<Bundle> {
  return request<Bundle>("/api/runs", {
    method: "POST",
    body: JSON.stringify({ preset: presetId }),
  });
}

/** Run a scenario the UI assembled — a preset reshaped by the sliders and the brush. */
export function createConfiguredRun(config: Record<string, unknown>): Promise<Bundle> {
  return request<Bundle>("/api/runs", {
    method: "POST",
    body: JSON.stringify({ config }),
  });
}

export function health(): Promise<{ status: string; version: string }> {
  return request<{ status: string; version: string }>("/api/health");
}
