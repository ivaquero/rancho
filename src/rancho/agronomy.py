"""Cell-resolved water and nitrogen balance for Rancho.

CropForge ships a layered tipping-bucket that refills every column towards field
capacity, which flattens the grid: every cell ends up at the same moisture and
therefore grows identically.  Rancho replaces *only* that piece with a FAO-56
single-layer root-zone balance that keeps one state per cell, so the field
carries real spatial structure — dry shoulders, wet hollows, nitrogen patches —
without touching CropForge's crop growth, radiation or logging.

Daily sequence (phase 0, before the crop plugin at phase 2):

1. Infiltration — rainfall minus slope runoff, which is routed downhill into
   depression cells rather than discarded.
2. Allocation — a water-limited irrigation allotment goes to the driest blocks
   first, so part of the field is deliberately left to dry down.
3. Stress coefficient ``Ks`` from root-zone depletion (FAO-56, p = 0.5).
4. Extraction — ``ETc = Kc · ET0 · Ks`` out of the root bucket.
5. Nitrogen — mineralisation + split fertiliser in, crop uptake + leaching out.
6. Write-back — voxel moisture, ``water_stress_ks``, ``n_stress_kn``, stress.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from rancho.climate import SoilField
from rancho.scenario import ScenarioConfig

#: Effective root-zone depth (mm) used by the single-layer bucket.
ROOT_ZONE_MM = 300.0
#: Crop coefficient for a winter cereal canopy (FAO-56 mid-season).
CROP_COEFFICIENT = 1.05
#: Depletion fraction at which stress starts (FAO-56, p = 0.5 for cereals).
DEPLETION_FRACTION = 0.5
#: Nitrogen concentration of dry matter (fraction of kg DM).
N_CONCENTRATION = 0.028
#: Half-saturation constant of the Mitscherlich nitrogen response (kg/ha).
N_HALF_SATURATION = 28.0
#: Daily net mineralisation from soil organic matter (kg N/ha/day).
MINERALISATION_KG_HA_DAY = 0.35
#: Share of rain deeper than 15 mm that carries nitrate below the root zone.
LEACHING_PER_MM = 0.05
#: Beer-Lambert canopy extinction coefficient (C3 cereals).
K_EXTINCTION = 0.45
#: Share of global radiation intercepted (RUE is calibrated on global MJ).
PAR_FRACTION = 1.0

#: Eight-neighbourhood offsets used for downhill routing.
_OFFSETS: tuple[tuple[int, int], ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


class AgronomyModel:
    """Per-cell root-zone water + nitrogen state, advanced once per day."""

    def __init__(self, cfg: ScenarioConfig, soil: SoilField) -> None:
        self._cfg = cfg
        self._soil = soil
        self._moisture = soil.moisture.astype(np.float64).copy()
        self._nitrogen = soil.nitrogen.astype(np.float64).copy()
        self._biomass = np.zeros((soil.rows, soil.cols), dtype=np.float64)
        self._day = 0
        self._runoff, self._receiver = self._route_runoff(soil)
        self._fertiliser_days = self._split_days(cfg)
        # Irrigation is zonal: a system covers part of the field and the rest is
        # rainfed, which is what keeps the grid from being levelled to one
        # moisture value (see :meth:`_allocate_irrigation`).
        self._zone = self._irrigation_zone(cfg, soil)
        # Canopy bookkeeping: the bundled plugin grows LAI from thermal time
        # alone, so Rancho tracks its potential curve and applies a supply
        # factor of its own (see :meth:`adjust_canopy`).
        self._supply = np.ones((soil.rows, soil.cols), dtype=np.float64)
        self._lai_potential = np.zeros((soil.rows, soil.cols), dtype=np.float64)
        self._lai_written = np.zeros((soil.rows, soil.cols), dtype=np.float64)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    @staticmethod
    def _route_runoff(soil: SoilField) -> tuple[np.ndarray, np.ndarray]:
        """Runoff share per cell and the downhill neighbour that receives it.

        Water leaving a cell is not simply lost: it is handed to the steepest
        downslope neighbour, which is what makes hollows and toe-slopes stay wet
        while shoulders shed water.  Cells with no downslope neighbour keep
        their rain and act as sinks.
        """
        elev = np.asarray(soil.elevation, dtype=np.float64)
        rows, cols = elev.shape
        flat_index = np.arange(rows * cols, dtype=np.int64).reshape(rows, cols)

        best_drop = np.zeros((rows, cols), dtype=np.float64)
        best_dr = np.zeros((rows, cols), dtype=np.int64)
        best_dc = np.zeros((rows, cols), dtype=np.int64)

        for dr, dc in _OFFSETS:
            # Cells whose neighbour (i+dr, j+dc) is still inside the grid.
            r_from, r_to = max(0, -dr), rows - max(0, dr)
            c_from, c_to = max(0, -dc), cols - max(0, dc)
            here = (slice(r_from, r_to), slice(c_from, c_to))
            drop = elev[here] - elev[r_from + dr : r_to + dr, c_from + dc : c_to + dc]
            steeper = drop > best_drop[here]
            best_drop[here] = np.where(steeper, drop, best_drop[here])
            best_dr[here] = np.where(steeper, dr, best_dr[here])
            best_dc[here] = np.where(steeper, dc, best_dc[here])

        receiver = flat_index + best_dr * cols + best_dc
        receiver[best_drop <= 0.0] = flat_index[best_drop <= 0.0]

        slope = np.hypot(*np.gradient(elev))
        runoff = np.clip(slope * 0.62, 0.0, 0.34)
        runoff[best_drop <= 0.0] = 0.0
        return runoff, receiver.reshape(-1)

    @staticmethod
    def _split_days(cfg: ScenarioConfig) -> frozenset[int]:
        """Three fertiliser splits: basal, tillering, stem extension."""
        if cfg.fertiliser_kg_ha <= 0.0:
            return frozenset()
        return frozenset(max(1, round(cfg.days * share)) for share in (0.02, 0.22, 0.40))

    @staticmethod
    def _irrigation_zone(cfg: ScenarioConfig, soil: SoilField) -> np.ndarray:
        """Boolean mask of the cells the irrigation system actually reaches.

        Coverage is drawn from the soil-depth field rather than a rectangle, so
        the boundary follows the land instead of a ruler: the deep, water-holding
        blocks get the dripline, the shallow shoulders stay rainfed.
        """
        if not cfg.irrigation:
            return np.zeros((soil.rows, soil.cols), dtype=bool)
        if cfg.irrigation_coverage >= 0.999:
            return np.ones((soil.rows, soil.cols), dtype=bool)
        cutoff = float(np.quantile(soil.field_capacity, 1.0 - cfg.irrigation_coverage))
        return np.asarray(soil.field_capacity >= cutoff, dtype=bool)

    # ------------------------------------------------------------------
    # Stepper
    # ------------------------------------------------------------------

    def step(self, state: Any, env: Any) -> Any:
        """Apply one day of water and nitrogen balance, then stamp plant state."""
        soil = self._soil
        self._day += 1

        rain = max(0.0, float(env.rainfall_mm))
        shed = rain * self._runoff
        inflow = rain * (1.0 - self._runoff)
        pooled = np.zeros_like(inflow)
        np.add.at(pooled.reshape(-1), self._receiver, shed.reshape(-1))

        irrigation = self._allocate_irrigation()
        self._moisture += (inflow + pooled + irrigation) / ROOT_ZONE_MM * 100.0
        np.minimum(self._moisture, soil.field_capacity + 1.5, out=self._moisture)

        span = np.maximum(1e-6, DEPLETION_FRACTION * (soil.field_capacity - soil.wilting_point))
        stress = np.clip((self._moisture - soil.wilting_point) / span, 0.0, 1.0)

        demand = max(0.0, float(env.et0_mm)) * CROP_COEFFICIENT * stress
        self._moisture -= demand / ROOT_ZONE_MM * 100.0
        np.maximum(self._moisture, soil.wilting_point * 0.55, out=self._moisture)

        self._update_nitrogen(state, rain)
        nitrogen_stress = np.clip(1.0 - np.exp(-self._nitrogen / N_HALF_SATURATION), 0.18, 1.0)

        self._supply = 0.88 * self._supply + 0.12 * (stress * nitrogen_stress)
        self._write_back(state, stress, nitrogen_stress)
        self._allocate_light(state, env)

        env.custom["irrigation_mm"] = round(float(np.max(irrigation)), 3)
        env.custom["irrigation_frac"] = round(
            float(np.count_nonzero(irrigation)) / irrigation.size, 4
        )
        env.custom["soil_moisture_pct"] = round(float(np.mean(self._moisture)), 2)
        env.custom["water_stress_ks"] = round(float(np.mean(stress)), 4)

        return state

    def _allocate_irrigation(self) -> np.ndarray:
        """Water-limited allocation: only cells inside the serviced zone get water.

        A system that topped up every dry cell would level the field to one
        moisture value and erase the very variability the grid is there to show.
        Coverage is therefore spatial, not a daily ranking: the zone is wet, the
        rest of the field lives on rainfall and dries down on its own schedule.
        """
        cfg = self._cfg
        applied = np.zeros_like(self._moisture)
        if not cfg.irrigation:
            return applied

        served = self._zone & (self._moisture < cfg.irrigation_threshold_pct)
        applied[served] = cfg.irrigation_mm
        return applied

    def _allocate_light(self, state: Any, env: Any) -> None:
        """Give each plant its share of the *canopy* interception, not its own.

        CropForge's radiation hook evaluates Beer-Lambert per plant, so every
        plant intercepts the light of a full canopy and the field's assimilate
        ends up multiplied by the planting density.  Here interception is
        computed once per cell from the canopy LAI and divided across the plants
        that share that cell, which is both density-independent and correct.
        """
        density = max(1e-6, self._cfg.plants_per_m2)
        canopy = 1.0 - np.exp(-K_EXTINCTION * np.maximum(0.0, self._lai_written))

        radiation = float(env.radiation_mj_m2)
        grid = env.custom.get("solar_rad_corrected_mj")
        if isinstance(grid, list | tuple) and len(grid) == self._soil.rows:
            cell_rad = np.asarray(grid, dtype=np.float64)
            if cell_rad.shape == canopy.shape:
                radiation_cell = cell_rad
            else:
                radiation_cell = np.full(canopy.shape, radiation)
        else:
            radiation_cell = np.full(canopy.shape, radiation)

        share = radiation_cell * PAR_FRACTION * canopy / density
        for plant in state.plants:
            plant.custom["intercepted_par_mj"] = float(share[int(plant.row), int(plant.col)])

    def _update_nitrogen(self, state: Any, rain: float) -> None:
        biomass = np.zeros_like(self._moisture)
        for plant in state.plants:
            biomass[int(plant.row), int(plant.col)] = float(plant.biomass_g)

        growth_g = np.maximum(0.0, biomass - self._biomass)
        self._biomass = biomass

        supply = MINERALISATION_KG_HA_DAY
        if self._day in self._fertiliser_days:
            supply += self._cfg.fertiliser_kg_ha / len(self._fertiliser_days)

        # g/plant → kg DM/ha, times tissue N concentration.
        uptake = growth_g * self._cfg.plants_per_m2 * 10.0 * N_CONCENTRATION
        leached = max(0.0, rain - 15.0) * LEACHING_PER_MM
        self._nitrogen = np.maximum(1.5, self._nitrogen + supply - uptake - leached)

    def _write_back(self, state: Any, stress: np.ndarray, nitrogen_stress: np.ndarray) -> None:
        """Push cell state into the CropForge state objects the plugin reads."""
        moisture = self._moisture
        for plant in state.plants:
            row, col = int(plant.row), int(plant.col)
            layers = state.soil[row][col]
            surface = float(moisture[row, col])
            for index, voxel in enumerate(layers):
                voxel.moisture_pct = round(min(46.0, surface + 1.4 * index), 2)
            voxel_nitrogen = float(self._nitrogen[row, col])
            for voxel in layers:
                voxel.nitrogen_kg_ha = round(voxel_nitrogen, 2)

            plant.custom["water_stress_ks"] = float(stress[row, col])
            plant.custom["n_stress_kn"] = float(nitrogen_stress[row, col])
            plant.stress_index = round(float(1.0 - stress[row, col] * nitrogen_stress[row, col]), 4)

    def adjust_canopy(self, state: Any, env: Any) -> Any:
        """Limit leaf area to what the cell can actually supply.

        The bundled plugins derive LAI from thermal time alone, so a
        water-limited cell would otherwise carry the same canopy as a
        well-watered one and the grid would render flat.  Rancho keeps the
        plugin's potential curve by differencing what it wrote last, then scales
        that curve by a supply factor built from the running water × nitrogen
        stress.  This runs at phase 3 — after the crop plugin — so the value it
        writes is the one that reaches the log and the interception model.
        """
        for plant in state.plants:
            row, col = int(plant.row), int(plant.col)
            increment = float(plant.lai) - self._lai_written[row, col]
            potential = max(0.0, self._lai_potential[row, col] + increment)
            self._lai_potential[row, col] = potential

            if potential <= 1e-3:
                limited = 0.0
            else:
                limited = potential * (0.30 + 0.70 * math.sqrt(self._supply[row, col]))
            self._lai_written[row, col] = limited
            plant.lai = limited
        return state
