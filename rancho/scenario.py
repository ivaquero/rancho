"""Scenario description for the Rancho field simulator.

A :class:`ScenarioConfig` is the single source of truth for one simulation:
grid geometry, crop, soil, climate, terrain and the physics engines that
CropForge should switch on.  Everything is validated up front so that the
engine never fails halfway through a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rancho.crops import TEMPLATES

#: Crops whose phenology CropForge ships on its own, so they have coefficients
#: but no growth model of Rancho's own.
BUILTIN_CROPS: frozenset[str] = frozenset({"wheat", "maize"})

#: Every crop a scenario may name: the cereals plus every template on disk.  Not
#: a ``Literal`` on purpose — dropping a new ``<id>.toml`` into :mod:`rancho.crops`
#: must make it selectable without editing this file.
CROP_KEYS: frozenset[str] = BUILTIN_CROPS | frozenset(TEMPLATES)

#: Crops that may be planted *per cell*.  The restriction is not agronomic but
#: mechanical: CropForge's own plugins (the cereals) step the whole field in one
#: call and cannot be told which cells belong to them, while the templated crops
#: use Rancho's own phenology, which steps one plant at a time.  Adding a TOML
#: file therefore makes a new crop mixable as well.
MOSAIC_KEYS: frozenset[str] = frozenset(
    key for key, template in TEMPLATES.items() if template.has_phenology
)
SoilKind = Literal["loam", "clay", "sandy", "andisol"]
ClimateKind = Literal["temperate", "continental", "semi-arid", "tropical"]
WeatherSource = Literal["synthetic", "open-meteo"]
SoilPattern = Literal["uniform", "gradient", "patches"]
TerrainKind = Literal["flat", "slope", "undulating"]

#: Stage order lives in :mod:`rancho.crops` with the templates it validates;
#: :data:`STAGE_LABELS` stays here because it is a display concern.

#: Display names for the stage axis (Chinese UI labels).
STAGE_LABELS: dict[str, str] = {
    "germination": "萌发",
    "emergence": "出苗",
    "seedling": "苗期",
    "tillering": "分蘖",
    "vegetative": "营养生长",
    "stem_extension": "拔节",
    "tasseling": "抽雄",
    "silking": "吐丝",
    "anthesis": "开花",
    "flowering": "开花",
    "fruit_set": "坐果",
    "fruiting": "结果采收",
    "grain_fill": "灌浆",
    "maturity": "成熟",
}


@dataclass(frozen=True, slots=True)
class CropProfile:
    """What the harvested product *is* — used to turn dry matter into yield.

    Cereals report grain at harvest moisture, so their dry-matter fraction is 1
    and the harvest index alone does the conversion.  Vegetables are sold fresh:
    the fruit or leaf is only a few percent dry matter, and part of it never
    reaches market, so both factors have to be applied on top of the index.
    """

    label: str
    species: str
    dry_matter_fraction: float = 1.0
    marketable_fraction: float = 1.0
    #: Share of above-ground dry matter reaching the harvested organ, from the
    #: template.  Lives with the product description because "how much of the
    #: plant is sold" and "what fraction of it is dry matter" are one question.
    harvest_index: float = 0.38


#: Crop → product description.  Kept next to the scenario because yield is a
#: property of what is sold, not of the growth model — but every entry now comes
#: from the template's ``[product]`` table, cereals included.
CROP_PROFILES: dict[str, CropProfile] = {
    key: CropProfile(
        template.label,
        template.species,
        template.product.dry_matter_fraction,
        template.product.marketable_fraction,
        template.product.harvest_index,
    )
    for key, template in TEMPLATES.items()
}


class PhysicsConfig(BaseModel):
    """Opt-in CropForge physics engines."""

    model_config = ConfigDict(extra="forbid")

    et0: bool = True
    radiation: bool = True
    disease: bool = False
    weeds: bool = False


class WeatherConfig(BaseModel):
    """Where the daily weather series comes from.

    ``synthetic`` regenerates the seeded climate profile in ``climate`` and is
    fully offline.  ``open-meteo`` pulls the observed window for the scenario's
    coordinates (:mod:`rancho.weather`) and caches it on disk; ``climate`` then
    only serves as the fallback when the provider cannot be reached.
    """

    model_config = ConfigDict(extra="forbid")

    source: WeatherSource = "synthetic"
    #: Calendar year the sowing day refers to.  Synthetic weather ignores it —
    #: only real data needs a year to turn a day-of-year into a date.
    year: int = Field(default=2025, ge=1940, le=2100)
    timeout_s: float = Field(default=20.0, gt=1.0, le=120.0)
    #: Degrade to the synthetic climate instead of failing when offline.
    fallback: bool = True
    #: Read the on-disk cache only — reproducible, air-gapped runs.
    offline: bool = False


class ScenarioConfig(BaseModel):
    """Full configuration of one field simulation run."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="North Plot", min_length=1, max_length=64)
    rows: int = Field(default=26, ge=4, le=72)
    cols: int = Field(default=34, ge=4, le=96)
    days: int = Field(default=200, ge=10, le=240)

    crop: str = Field(default="wheat", min_length=1, max_length=32)
    variety: str = Field(default="HD-2967", min_length=1, max_length=48)
    sowing_doy: int = Field(default=300, ge=1, le=366)

    #: Which crop grows in each cell, row-major and exactly ``rows × cols`` long.
    #: ``None`` means the whole field carries :attr:`crop`.  Every entry must be
    #: one of :data:`MOSAIC_KEYS` — see :func:`crop_grid` for why the list rather
    #: than a compact encoding is the wire format here.
    crop_map: list[str] | None = Field(default=None)

    row_spacing_m: float = Field(default=0.25, gt=0.01, le=2.0)
    plant_spacing_m: float = Field(default=0.10, gt=0.01, le=2.0)

    soil: SoilKind = "loam"
    soil_pattern: SoilPattern = "patches"
    soil_variation: float = Field(default=0.55, ge=0.0, le=1.0)
    climate: ClimateKind = "continental"
    terrain: TerrainKind = "slope"
    slope_pct: float = Field(default=1.6, ge=0.0, le=20.0)
    latitude: float = Field(default=28.6, ge=-90.0, le=90.0)
    longitude: float = Field(default=77.2, ge=-180.0, le=180.0)
    seed: int = Field(default=42, ge=0, le=999_999)

    #: Protected cultivation: a plastic/insect-net cover that keeps rain out and
    #: a shade net that cuts the radiation reaching the canopy.  ``cover_transmission``
    #: is the combined PAR transmission of cover *and* shade net (0.55 ≈ clear
    #: film at 85 % behind a 65 % shade net), applied to every day's radiation.
    protected: bool = False
    cover_transmission: float = Field(default=1.0, gt=0.20, le=1.0)

    irrigation: bool = True
    irrigation_threshold_pct: float = Field(default=21.0, ge=2.0, le=40.0)
    irrigation_mm: float = Field(default=16.0, ge=1.0, le=60.0)
    irrigation_coverage: float = Field(default=0.58, ge=0.1, le=1.0)
    #: Share of above-ground dry matter that ends up in the harvested organ.
    #: Cereals sit near 0.4; a lettuce head is most of the plant, hence 0.9.
    #: Left ``None`` the value comes from the crop's ``[product]`` table, which
    #: is what every bundled preset does — the field exists to try a hypothesis
    #: ("what if this variety translocates 10 % more?") without editing crops.
    harvest_index: float | None = Field(default=None, ge=0.10, le=0.90)
    fertiliser_kg_ha: float = Field(default=80.0, ge=0.0, le=400.0)

    physics: PhysicsConfig = Field(default_factory=PhysicsConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)

    @model_validator(mode="after")
    def _check_crop(self) -> ScenarioConfig:
        """Reject crops the engine cannot run, and a map that does not fit."""
        if self.crop not in CROP_KEYS:
            raise ValueError(f"未知作物 {self.crop!r}，可选 {sorted(CROP_KEYS)}")
        if self.crop_map is None:
            return self
        expected = self.rows * self.cols
        if len(self.crop_map) != expected:
            raise ValueError(
                f"crop_map 有 {len(self.crop_map)} 项，地块是 {self.rows}×{self.cols}"
                f"（应 {expected} 项）"
            )
        foreign = sorted({key for key in self.crop_map if key not in MOSAIC_KEYS})
        if foreign:
            raise ValueError(
                f"逐格混作只支持 Rancho 自带物候的作物 {sorted(MOSAIC_KEYS)}，"
                f"不支持 {foreign}（它们的物候由 CropForge 插件整块推进）"
            )
        return self

    @property
    def plants_per_m2(self) -> float:
        """Sowing density implied by the plant geometry."""
        return 1.0 / (self.row_spacing_m * self.plant_spacing_m)

    @property
    def mosaic(self) -> bool:
        """Whether this scenario plants more than one crop."""
        return bool(self.crop_map) and any(key != self.crop_map[0] for key in self.crop_map)

    def with_grid(self, rows: int, cols: int) -> ScenarioConfig:
        """Copy for a resized field, remapping the crop map by nearest cell.

        Growing or shrinking the grid must not throw away a hand-painted
        layout, so every cell of the new geometry takes the crop of the cell
        that used to sit at the same *relative* position.
        """
        updates: dict[str, object] = {"rows": rows, "cols": cols}
        if self.crop_map:
            source = list(self.crop_map)
            remapped: list[str] = []
            for row in range(rows):
                source_row = min(self.rows - 1, round(row * self.rows / rows))
                for col in range(cols):
                    source_col = min(self.cols - 1, round(col * self.cols / cols))
                    remapped.append(source[source_row * self.cols + source_col])
            updates["crop_map"] = remapped
        return self.model_copy(update=updates)


def crop_grid(cfg: ScenarioConfig) -> list[str]:
    """The crop of every cell, row-major — :attr:`crop` repeated when no map.

    Everything downstream reads the grid rather than ``cfg.crop`` so that the
    single-crop case has no branch of its own: one uniform map behaves exactly
    like a scenario that never set one.
    """
    if cfg.crop_map is None:
        return [cfg.crop] * (cfg.rows * cfg.cols)
    return list(cfg.crop_map)


def crop_keys(cfg: ScenarioConfig) -> tuple[str, ...]:
    """Every crop planted in this scenario, in the order the UI should show them."""
    keys: dict[str, None] = {}
    for key in crop_grid(cfg):
        keys.setdefault(key, None)
    return tuple(keys)


def crop_indices(cfg: ScenarioConfig) -> list[int]:
    """Per-cell index into :func:`crop_keys` — what the bundle ships to the client."""
    lookup = {key: index for index, key in enumerate(crop_keys(cfg))}
    return [lookup[key] for key in crop_grid(cfg)]


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
            soil_pattern="patches",
            soil_variation=0.85,
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
    "salvador-greenhouse": {
        "label": "萨尔瓦多 · 大棚番茄",
        "description": (
            "萨尔瓦多山地缓坡上的竹架大棚：遮阳网 + 防虫网 + 避雨，滴灌番茄，旱季定植、雨季前收完。"
        ),
        "config": ScenarioConfig(
            name="Invernadero El Salvador",
            rows=18,
            cols=40,
            days=130,
            crop="tomato",
            variety="Caiman F1",
            sowing_doy=310,
            row_spacing_m=0.90,
            plant_spacing_m=0.45,
            soil="andisol",
            soil_pattern="patches",
            soil_variation=0.45,
            climate="tropical",
            terrain="slope",
            slope_pct=6.0,
            latitude=13.69,
            longitude=-89.22,
            irrigation=True,
            irrigation_threshold_pct=26.0,
            irrigation_mm=10.0,
            irrigation_coverage=0.90,
            fertiliser_kg_ha=280.0,
            protected=True,
            cover_transmission=0.55,
            physics=PhysicsConfig(et0=True, radiation=True),
        ),
    },
    "salvador-leafy": {
        "label": "萨尔瓦多 · 大棚叶菜",
        "description": (
            "同一棚架下换茬生菜：60 天短周期，全畦滴灌，看高温季节遮阳对冠层温度与蒸腾的调节。"
        ),
        "config": ScenarioConfig(
            name="Invernadero Hoja",
            rows=18,
            cols=40,
            days=60,
            crop="lettuce",
            variety="Salanova",
            sowing_doy=20,
            row_spacing_m=0.40,
            plant_spacing_m=0.30,
            soil="andisol",
            soil_pattern="patches",
            soil_variation=0.40,
            climate="tropical",
            terrain="slope",
            slope_pct=6.0,
            latitude=13.69,
            longitude=-89.22,
            irrigation=True,
            irrigation_threshold_pct=26.0,
            irrigation_mm=6.0,
            irrigation_coverage=0.94,
            fertiliser_kg_ha=95.0,
            protected=True,
            cover_transmission=0.60,
            physics=PhysicsConfig(et0=True, radiation=True),
        ),
    },
    "salvador-mosaic": {
        "label": "萨尔瓦多 · 大棚混作",
        "description": (
            "同一座棚里按畦分作物：番茄 | 黄瓜 | 生菜三带并列，看跨畦争水争氮时各自的产量与胁迫。"
        ),
        "config": ScenarioConfig(
            name="Invernadero Mixto",
            rows=18,
            cols=39,
            days=120,
            crop="tomato",
            variety="Caiman F1",
            sowing_doy=310,
            # Three bands along the length of the house — a hand-written layout,
            # exactly what painting cells in the UI produces.
            crop_map=[
                "tomato" if col < 13 else "cucumber" if col < 26 else "lettuce"
                for _row in range(18)
                for col in range(39)
            ],
            row_spacing_m=0.90,
            plant_spacing_m=0.45,
            soil="andisol",
            soil_pattern="patches",
            soil_variation=0.45,
            climate="tropical",
            terrain="slope",
            slope_pct=6.0,
            latitude=13.69,
            longitude=-89.22,
            irrigation=True,
            irrigation_threshold_pct=26.0,
            irrigation_mm=10.0,
            irrigation_coverage=0.90,
            fertiliser_kg_ha=220.0,
            protected=True,
            cover_transmission=0.55,
            physics=PhysicsConfig(et0=True, radiation=True),
        ),
    },
    "salvador-observed": {
        "label": "萨尔瓦多 · 大棚番茄（实测气象）",
        "description": (
            "同一棚架与农艺参数，气象改用 Open-Meteo 的 2025/26 实测序列："
            "看真实旱季辐射与昼夜温差下的表现，断网时自动退回合成气候并标注。"
        ),
        "config": ScenarioConfig(
            name="Invernadero Observado",
            rows=18,
            cols=40,
            days=130,
            crop="tomato",
            variety="Caiman F1",
            sowing_doy=310,
            row_spacing_m=0.90,
            plant_spacing_m=0.45,
            soil="andisol",
            soil_pattern="patches",
            soil_variation=0.45,
            climate="tropical",
            terrain="slope",
            slope_pct=6.0,
            latitude=13.69,
            longitude=-89.22,
            irrigation=True,
            irrigation_threshold_pct=26.0,
            irrigation_mm=10.0,
            irrigation_coverage=0.90,
            fertiliser_kg_ha=280.0,
            protected=True,
            cover_transmission=0.55,
            physics=PhysicsConfig(et0=True, radiation=True),
            weather=WeatherConfig(source="open-meteo", year=2025),
        ),
    },
}

#: Greenhouse vegetables are the default for a reason: the protected preset is
#: the one that exercises every layer Rancho adds on top of CropForge (cover
#: micro-climate, trellised canopy, fresh-weight yield), so it is also the one
#: worth landing on.
DEFAULT_PRESET = "salvador-greenhouse"
