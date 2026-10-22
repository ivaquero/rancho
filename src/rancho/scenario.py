"""Scenario description for the Rancho field simulator.

A :class:`ScenarioConfig` is the single source of truth for one simulation:
grid geometry, crop, soil, climate, terrain and the physics engines that
CropForge should switch on.  Everything is validated up front so that the
engine never fails halfway through a run.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CropKind = Literal["wheat", "maize"]
SoilKind = Literal["loam", "clay", "sandy"]
ClimateKind = Literal["temperate", "continental", "semi-arid"]
SoilPattern = Literal["uniform", "gradient", "patches"]
TerrainKind = Literal["flat", "slope", "undulating"]

#: Canonical phenological stage order shared by the bundled crop plugins.
STAGE_ORDER: tuple[str, ...] = (
    "germination",
    "emergence",
    "tillering",
    "vegetative",
    "stem_extension",
    "tasseling",
    "silking",
    "anthesis",
    "grain_fill",
    "maturity",
)

#: Display names for the stage axis (Chinese UI labels).
STAGE_LABELS: dict[str, str] = {
    "germination": "萌发",
    "emergence": "出苗",
    "tillering": "分蘖",
    "vegetative": "营养生长",
    "stem_extension": "拔节",
    "tasseling": "抽雄",
    "silking": "吐丝",
    "anthesis": "开花",
    "grain_fill": "灌浆",
    "maturity": "成熟",
}


class PhysicsConfig(BaseModel):
    """Opt-in CropForge physics engines."""

    model_config = ConfigDict(extra="forbid")

    et0: bool = True
    radiation: bool = True
    disease: bool = False
    weeds: bool = False


class ScenarioConfig(BaseModel):
    """Full configuration of one field simulation run."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="North Plot", min_length=1, max_length=64)
    rows: int = Field(default=26, ge=4, le=72)
    cols: int = Field(default=34, ge=4, le=96)
    days: int = Field(default=200, ge=10, le=240)

    crop: CropKind = "wheat"
    variety: str = Field(default="HD-2967", min_length=1, max_length=48)
    sowing_doy: int = Field(default=300, ge=1, le=366)

    row_spacing_m: float = Field(default=0.25, gt=0.01, le=2.0)
    plant_spacing_m: float = Field(default=0.10, gt=0.01, le=2.0)

    soil: SoilKind = "loam"
    soil_pattern: SoilPattern = "patches"
    soil_variation: float = Field(default=0.55, ge=0.0, le=1.0)
    climate: ClimateKind = "continental"
    terrain: TerrainKind = "slope"
    seed: int = Field(default=42, ge=0, le=999_999)

    irrigation: bool = True
    irrigation_threshold_pct: float = Field(default=21.0, ge=2.0, le=40.0)
    irrigation_mm: float = Field(default=16.0, ge=1.0, le=60.0)
    irrigation_coverage: float = Field(default=0.58, ge=0.1, le=1.0)
    harvest_index: float = Field(default=0.38, ge=0.10, le=0.60)
    fertiliser_kg_ha: float = Field(default=80.0, ge=0.0, le=400.0)

    physics: PhysicsConfig = Field(default_factory=PhysicsConfig)

    @property
    def plants_per_m2(self) -> float:
        """Sowing density implied by the plant geometry."""
        return 1.0 / (self.row_spacing_m * self.plant_spacing_m)


PRESETS: dict[str, dict[str, object]] = {
    "rabi-wheat": {
        "label": "冬小麦 · 常规灌溉",
        "description": "北印度 Rabi 季小麦，缓坡地块，土壤水分低于阈值时自动灌溉。",
        "config": ScenarioConfig(
            name="North Plot",
            rows=26,
            cols=34,
            days=200,
            crop="wheat",
            variety="HD-2967",
            sowing_doy=300,
            climate="continental",
            soil="loam",
            terrain="slope",
            irrigation=True,
            irrigation_coverage=0.62,
            fertiliser_kg_ha=85.0,
            soil_pattern="patches",
            soil_variation=0.6,
            physics=PhysicsConfig(et0=True, radiation=True),
        ),
    },
    "kharif-maize": {
        "label": "夏玉米 · 雨养",
        "description": "雨季玉米，平地黏壤土，无灌溉，检验降水驱动的生长节律。",
        "config": ScenarioConfig(
            name="South Plot",
            rows=24,
            cols=32,
            days=145,
            crop="maize",
            variety="Bio-9681",
            sowing_doy=170,
            climate="temperate",
            soil="clay",
            terrain="flat",
            irrigation=False,
            fertiliser_kg_ha=70.0,
            soil_pattern="gradient",
            soil_variation=0.5,
            physics=PhysicsConfig(et0=True, radiation=True),
        ),
    },
    "semi-arid": {
        "label": "旱区 · 水分胁迫",
        "description": "半干旱沙壤土，灌溉受限，观察胁迫指数与产量损失的空间分布。",
        "config": ScenarioConfig(
            name="Dry Plot",
            rows=22,
            cols=30,
            days=170,
            crop="wheat",
            variety="HD-2967",
            sowing_doy=300,
            climate="semi-arid",
            soil="sandy",
            terrain="undulating",
            irrigation=True,
            irrigation_threshold_pct=13.0,
            irrigation_mm=9.0,
            irrigation_coverage=0.42,
            fertiliser_kg_ha=45.0,
            soil_pattern="patches",
            soil_variation=0.9,
            physics=PhysicsConfig(et0=True, radiation=True),
        ),
    },
    "disease-front": {
        "label": "病害扩散 · 抗性试验",
        "description": "开启空间病害引擎，病害从地块中心向外扩散，用于评估损失。",
        "config": ScenarioConfig(
            name="Trial Plot",
            rows=24,
            cols=30,
            days=190,
            crop="wheat",
            variety="HD-2967",
            sowing_doy=300,
            climate="temperate",
            soil="loam",
            terrain="flat",
            irrigation=True,
            irrigation_coverage=0.72,
            fertiliser_kg_ha=85.0,
            soil_pattern="patches",
            soil_variation=0.4,
            physics=PhysicsConfig(et0=True, radiation=True, disease=True, weeds=True),
        ),
    },
}

DEFAULT_PRESET = "rabi-wheat"
