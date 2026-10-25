/** English locale — must satisfy every key in the Chinese baseline.
 *
 * Copy this file to add another language; `Strings` makes the compiler point at
 * whatever has not been translated yet.
 */

import type { Strings } from "./zh";

export const en: Strings = {
  // --- shell ---------------------------------------------------------------
  "app.title": "Rancho · Farm Crop Simulator",
  "brand.sub": "Farm Crop Simulator",
  "lang.label": "Language",

  // --- top bar -------------------------------------------------------------
  "field.scenario": "Scenario",
  "btn.run": "Run simulation",
  "status.connecting": "Connecting to service…",
  "status.connected": "Service connected · v{0}",
  "status.noBackend": "Backend not connected (run rancho serve first)",
  "status.dirtyHasBundle": "Config changed · press Run to rerun",
  "status.dirtyNoBundle": "Pending · press Run to start",
  "status.running": "Running simulation…",
  "status.ready": "Ready · {0} days · {1}s",
  "status.failed": "Simulation failed",

  // --- right panel: sections ----------------------------------------------
  "title.channels": "Color channel",
  "title.legend": "Legend",
  "title.metrics": "Current state",
  "title.layout": "Crop layout",
  "title.size": "Field size",
  "title.field": "Field",
  "title.yield": "Yield",
  "title.trajectory": "Season trajectory",

  // --- layout / painting ---------------------------------------------------
  "layout.toggle": "Drag to paint (off = rotate the view)",
  "layout.enabledHint": "Click or drag a cell to change its crop; it reruns automatically.",
  "layout.disabledHint":
    "This scenario's phenology is advanced per-plot by the CropForge plugin; per-cell mixing is off.",
  "btn.fillAll": "Fill all",
  "btn.fillBands": "Column bands",
  "btn.reset": "Reset scene",

  // --- field size ----------------------------------------------------------
  "slider.rows": "Rows",
  "slider.cols": "Cols",
  "slider.days": "Days",
  "grid.hintLarge": " · large grid, a run takes 10s to a minute",
  "grid.hintDrag": " (rerun after release)",
  "days.manual": "Manually set to {0} days · {1} ripens in {2} days",
  "days.follow": "Follows the longest crop: {0} ripens in {1} days",
  "crop.fallback": "crop",

  // --- run / overlays ------------------------------------------------------
  "run.overlay": "Running simulation: {0} × {1} cells · {2} days…",
  "overlay.connecting": "Connecting to simulation service…",
  "overlay.noBackend":
    "Cannot reach the simulation service. Run rancho serve in the project root and refresh.",
  "overlay.presetsFail": "Failed to read scenarios: {0}",
  "overlay.failed": "Simulation failed: {0}",

  // --- yield ---------------------------------------------------------------
  "yield.total": "Total",
  "yield.gross": "Gross",
  "yield.biomass": "Biomass",
  "yield.harvestIndex": "Harvest index",
  "yield.perCrop": "└ yield",

  // --- legend --------------------------------------------------------------
  "legend.crops": "{0} crops",
  "legend.stages": "{0} stages",

  // --- field info ----------------------------------------------------------
  "field.field": "Field",
  "field.area": "Area",
  "field.crop": "Crop",
  "field.variety": "Variety",
  "field.days": "Days",
  "field.site": "Site",
  "field.density": "Density",
  "field.irrigation": "Irrigation",
  "field.fert": "N fertiliser",
  "field.mixed": "{0} mixed crops",
  "field.shelter": "Shelter",
  "field.shelterVal": "Sheltered · {0}% transmission",
  "field.weather": "Weather",
  "field.weatherObserved": "observed",
  "field.weatherSynthetic": "synthetic",
  "field.irrigDrip": "drip {0} mm · {1}% cover",
  "field.irrigRainfed": "rainfed",

  // --- soil / terrain enums (keyed by the server's raw value) --------------
  "soil.loam": "Loam",
  "soil.clay": "Clay",
  "soil.sandy": "Sandy loam",
  "soil.andisol": "Andisol",
  "terrain.flat": "Flat",
  "terrain.slope": "Slope",
  "terrain.undulating": "Undulating",

  // --- metrics -------------------------------------------------------------
  "metric.spatial": "Spatial range",
  "metric.biomass": "Biomass",
  "metric.lai": "Leaf area",
  "metric.moisture": "Moisture",
  "metric.stress": "Stress index",
  "metric.temp": "Temp",
  "metric.rain": "Rain",
  "metric.irrig": "Irrigation",

  // --- units ---------------------------------------------------------------
  "unit.cells": "cells",
  "unit.days": "days",
  "unit.density": "plants/m²",
  "unit.perPlant": "g/plant",

  // --- timeline / tooltip --------------------------------------------------
  "day.label": "Day {0}",
  "date.format": "{0}/{1}",
  "tooltip.cell": "Day {0} · row {1} / col {2}",
  "tooltip.crop": "Crop",
  "tooltip.stage": "Stage",
  "channel.cropLayout": "Crop layout",
  "na": "—",

  // --- bottom bar ----------------------------------------------------------
  "chart.hint": "Drag the timeline or click a curve to inspect a day",
  "speed.label": "Speed",
  "speed.slow": "Slow",
  "speed.normal": "Normal",
  "speed.fast": "Fast",
  "field.exaggerate": "Height exaggeration",

  // --- chart.ts ------------------------------------------------------------
  "chart.lai": "LAI",
  "chart.moisture": "Moisture",
  "chart.stress": "Stress",
  "chart.rain": "Rain",
  "chart.dayFirst": "Day 1",
  "chart.dayLast": "Day {0}",
};
