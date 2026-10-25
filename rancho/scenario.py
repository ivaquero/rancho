"""Scenario description for the Rancho field simulator.

A :class:`ScenarioConfig` is the single source of truth for one simulation:
grid geometry, crop, soil, climate, terrain and the physics engines that
CropForge should switch on.  Everything is validated up front so that the
engine never fails halfway through a run.
"""

from __future__ import annotations

from collections.abc import Iterable
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

#: English stage names, parallel to :data:`STAGE_LABELS`.
STAGE_LABELS_EN: dict[str, str] = {
    "germination": "Germination",
    "emergence": "Emergence",
    "seedling": "Seedling",
    "tillering": "Tillering",
    "vegetative": "Vegetative",
    "stem_extension": "Stem extension",
    "tasseling": "Tasseling",
    "silking": "Silking",
    "anthesis": "Anthesis",
    "flowering": "Flowering",
    "fruit_set": "Fruit set",
    "fruiting": "Fruiting",
    "grain_fill": "Grain fill",
    "maturity": "Maturity",
}

#: Spanish stage names, parallel to :data:`STAGE_LABELS`.
STAGE_LABELS_ES: dict[str, str] = {
    "germination": "Germinación",
    "emergence": "Emergencia",
    "seedling": "Plántula",
    "tillering": "Ahijamiento",
    "vegetative": "Vegetativo",
    "stem_extension": "Encañado",
    "tasseling": "Espigado",
    "silking": "Floración femenina",
    "anthesis": "Antesis",
    "flowering": "Floración",
    "fruit_set": "Cuajado",
    "fruiting": "Fructificación",
    "grain_fill": "Llenado de grano",
    "maturity": "Madurez",
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
    label_en: str
    label_es: str
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
        template.label_en or template.label,
        template.label_es or template.label_en or template.label,
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
    #: How long the run lasts.  ``None`` — the default — means "until the
    #: slowest crop in the field has matured", see :func:`season_days`.  A
    #: scenario that names a horizon instead is cutting the season short on
    #: purpose, and that number wins.
    days: int | None = Field(default=None, ge=10, le=240)

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
        # The nominal crop has to be one of them too: it is what the field
        # reports, what a single-crop run grows and what the engine picks its
        # plugin from, so a wheat field cannot quietly claim a tomato layout.
        if self.crop not in MOSAIC_KEYS:
            raise ValueError(
                f"混作场景的 crop 必须取自 {sorted(MOSAIC_KEYS)}，当前是 {self.crop!r}"
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


def unique_in_order(keys: Iterable[str]) -> tuple[str, ...]:
    """De-duplicate *keys*, keeping first appearance.

    One order, one implementation: the water balance, the growth dispatcher and
    the client's colour lookup all index into this tuple, so they have to agree
    on it.  "First appearance" rather than alphabetical is deliberate — it is
    the order the beds were planted in.
    """
    seen: dict[str, None] = {}
    for key in keys:
        seen.setdefault(key, None)
    return tuple(seen)


def crop_keys(cfg: ScenarioConfig) -> tuple[str, ...]:
    """Every crop planted in this scenario, in the order the UI should show them."""
    return unique_in_order(crop_grid(cfg))


def crop_indices(cfg: ScenarioConfig) -> list[int]:
    """Per-cell index into :func:`crop_keys` — what the bundle ships to the client."""
    lookup = {key: index for index, key in enumerate(crop_keys(cfg))}
    return [lookup[key] for key in crop_grid(cfg)]


def season_days(cfg: ScenarioConfig) -> int:
    """Days the field needs before its slowest crop has matured.

    A mixed bed is finished when the *last* crop in it is, so this is the
    maximum rather than an average: lettuce is ready in 60 days but stands next
    to tomato for another 70.  Clamped to the range :attr:`ScenarioConfig.days`
    accepts, so a slow cereal cannot ask for more than the engine will run.
    """
    longest = max(TEMPLATES[key].season.days_to_maturity for key in crop_keys(cfg))
    return max(10, min(240, longest))


def horizon_days(cfg: ScenarioConfig) -> int:
    """The number of days this scenario will actually simulate."""
    return cfg.days if cfg.days is not None else season_days(cfg)


def with_horizon(cfg: ScenarioConfig) -> ScenarioConfig:
    """Copy with :attr:`days` filled in — what the engine and the cache key use.

    Resolving once, at the edge, keeps the rest of the code reading a plain
    integer: ``days=None`` and ``days=130`` for the same tomato field *are* the
    same run and must share a cache entry.
    """
    return cfg if cfg.days is not None else cfg.model_copy(update={"days": season_days(cfg)})


PRESETS: dict[str, dict[str, object]] = {
    "rabi-wheat": {
        "label": "冬小麦 · 常规灌溉",
        "label_en": "Winter wheat · conventional irrigation",
        "label_es": "Trigo de invierno · riego convencional",
        "description": "北印度 Rabi 季小麦，缓坡地块，土壤水分低于阈值时自动灌溉。",
        "description_en": "Rabi-season wheat in North India on a gentle slope; auto-irrigates when soil moisture drops below threshold.",
        "description_es": (
            "Trigo de la temporada Rabi en el norte de la India, en ladera suave; "
            "riega automáticamente cuando la humedad del suelo baja del umbral."
        ),
        "config": ScenarioConfig(
            name="North Plot",
            rows=26,
            cols=34,
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
        "label_en": "Summer maize · rainfed",
        "label_es": "Maíz de verano · secano",
        "description": "雨季玉米，平地黏壤土，无灌溉，检验降水驱动的生长节律。",
        "description_en": "Kharif-season maize on flat clay loam, no irrigation — growth driven by the rainfall rhythm.",
        "description_es": (
            "Maíz de la temporada Kharif en franco arcilloso llano, sin riego: "
            "el crecimiento lo marca el ritmo de las lluvias."
        ),
        "config": ScenarioConfig(
            name="South Plot",
            rows=24,
            cols=32,
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
        "label_en": "Arid zone · water stress",
        "label_es": "Zona árida · estrés hídrico",
        "description": "半干旱沙壤土，灌溉受限，观察胁迫指数与产量损失的空间分布。",
        "description_en": "Semi-arid sandy loam with limited irrigation; watch the spatial spread of stress and yield loss.",
        "description_es": (
            "Franco arenoso semiárido con riego limitado; observe la propagación "
            "espacial del estrés y la pérdida de rendimiento."
        ),
        "config": ScenarioConfig(
            name="Dry Plot",
            rows=22,
            cols=30,
            # 170 天而不是小麦自己的 200：旱区要的是胁迫过程，不是满季产量。
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
        "label_en": "Disease spread · resistance trial",
        "label_es": "Propagación de enfermedad · ensayo de resistencia",
        "description": "开启空间病害引擎，病害从地块中心向外扩散，用于评估损失。",
        "description_en": "Spatial disease engine enabled; infection advances outward from the field centre to assess losses.",
        "description_es": (
            "Motor espacial de enfermedades activado; la infección avanza desde el "
            "centro de la parcela para evaluar las pérdidas."
        ),
        "config": ScenarioConfig(
            name="Trial Plot",
            rows=24,
            cols=30,
            # 同上：190 天收，给病害扩散留出对照窗口。
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
        "label_en": "El Salvador · greenhouse tomato",
        "label_es": "El Salvador · tomate de invernadero",
        "description": (
            "萨尔瓦多山地缓坡上的竹架大棚：遮阳网 + 防虫网 + 避雨，滴灌番茄，旱季定植、雨季前收完。"
        ),
        "description_en": (
            "Bamboo-trellis greenhouse on a mountain slope in El Salvador: shade net + insect net + rain shelter, drip-irrigated tomato, planted in the dry season and harvested before the rains."
        ),
        "description_es": (
            "Invernadero de bambú en una ladera de montaña de El Salvador: malla sombra + malla "
            "antiinsectos + cubierta, tomate con riego por goteo, plantado en estación seca y "
            "cosechado antes de las lluvias."
        ),
        "config": ScenarioConfig(
            name="Invernadero El Salvador",
            # Small on purpose: this is the scenario the app opens on, and 32
            # cells render and re-run instantly, so the first thing a visitor
            # sees is the field rather than a progress cover.  Every number in
            # the model is per cell or per hectare, so nothing here depends on
            # the field being this size — the sliders go up to 72 × 96.
            rows=5,
            cols=12,
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
        "label_en": "El Salvador · greenhouse leafy greens",
        "label_es": "El Salvador · hortalizas de hoja en invernadero",
        "description": (
            "同一棚架下换茬生菜：60 天短周期，全畦滴灌，看高温季节遮阳对冠层温度与蒸腾的调节。"
        ),
        "description_en": (
            "Same structure replanted with lettuce: a short 60-day cycle, full-bed drip, watching how shade moderates canopy temperature and transpiration in the hot season."
        ),
        "description_es": (
            "La misma estructura replantada con lechuga: un ciclo corto de 60 días, goteo en toda "
            "la cama, observando cómo la sombra modera la temperatura del dosel y la transpiración "
            "en la estación cálida."
        ),
        "config": ScenarioConfig(
            name="Invernadero Hoja",
            rows=18,
            cols=40,
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
        "label_en": "El Salvador · greenhouse mixed cropping",
        "label_es": "El Salvador · cultivo mixto en invernadero",
        "description": (
            "同一座棚里按畦分作物：番茄 | 黄瓜 | 生菜三带并列，看跨畦争水争氮时各自的产量与胁迫。"
        ),
        "description_en": (
            "One house split by bed into tomato | cucumber | lettuce bands; watch each crop's yield and stress as they compete for water and nitrogen across beds."
        ),
        "description_es": (
            "Una sola casa dividida por camas en bandas de tomate | pepino | lechuga; observe el "
            "rendimiento y el estrés de cada cultivo al competir por agua y nitrógeno entre camas."
        ),
        "config": ScenarioConfig(
            name="Invernadero Mixto",
            rows=18,
            cols=39,
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
        "label_en": "El Salvador · greenhouse tomato (observed weather)",
        "label_es": "El Salvador · tomate de invernadero (clima observado)",
        "description": (
            "同一棚架与农艺参数，气象改用 Open-Meteo 的 2025/26 实测序列："
            "看真实旱季辐射与昼夜温差下的表现，断网时自动退回合成气候并标注。"
        ),
        "description_en": (
            "Same structure and agronomy, but weather swapped for the 2025/26 Open-Meteo observed series: performance under real dry-season radiation and diurnal range, auto-falling back to synthetic and flagging when offline."
        ),
        "description_es": (
            "Misma estructura y agronomía, pero con el clima cambiado por la serie observada "
            "2025/26 de Open-Meteo: comportamiento bajo la radiación real de la estación seca y la "
            "amplitud térmica diaria; sin conexión vuelve al clima sintético y lo indica."
        ),
        "config": ScenarioConfig(
            name="Invernadero Observado",
            rows=18,
            cols=40,
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
