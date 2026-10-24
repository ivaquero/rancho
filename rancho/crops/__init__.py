"""Hand-editable crop templates — one TOML file per greenhouse vegetable.

A vegetable used to be defined in three unrelated places: its growth profile in
:mod:`rancho.vegetables`, its root-zone coefficients in
:data:`rancho.agronomy.CROP_WATER` and its product description in
:data:`rancho.scenario.CROP_PROFILES`.  Editing one and forgetting the others
produced a crop that grew like a tomato and was sold like a lettuce.  The files
in this directory are now the single source for all three, validated on load.

Layout of one file — ``tomato.toml`` carries the annotated original::

    id = "tomato"  # must equal the file stem and the crop key
    label = "番茄"  # what the UI, reports and ledgers show
    species = "Solanum lycopersicum"
    base_temp_c = 10.0  # thermal-time base, °C

    [[stages]]  # array of tables, ordered by ``gdd``
    name = "germination"
    gdd = 0.0  # cumulative °C·d needed to enter the stage

    [growth]  # rue, lai_slope, lai_max, senescence_*
    [structure]  # k_height, max_height_cm
    [water]  # root_zone_mm, kc_mid, …, splits
    [product]  # dry_matter_fraction, marketable_fraction

TOML reaches the same IEEE-754 doubles as the Python literals it replaced, so
extracting these files moved no simulated yield; on top of that every value is
range-checked, stage names are checked against :data:`STAGE_ORDER`, and a typo
fails at import with the file name and the offending field path.
"""

from __future__ import annotations

import itertools
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

#: Canonical phenological stage order shared by all crops.  Only the stages a
#: crop actually passes through appear in a run; the list is a superset so that
#: cereals and greenhouse vegetables can share one axis in the UI.
STAGE_ORDER: tuple[str, ...] = (
    "germination",
    "emergence",
    "seedling",
    "tillering",
    "vegetative",
    "stem_extension",
    "tasseling",
    "silking",
    "anthesis",
    "flowering",
    "fruit_set",
    "fruiting",
    "grain_fill",
    "maturity",
)

#: Where the bundled templates live.  ``Path`` rather than ``importlib.resources``
#: keeps the CLI able to print a real path for the user to open in an editor.
TEMPLATE_DIR = Path(__file__).parent


class CropTemplateError(ValueError):
    """A template file is missing, malformed or physically implausible."""


class _Strict(BaseModel):
    """Reject unknown keys: a typo must not silently fall back to a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Stage(_Strict):
    """One phenological threshold — mirrors a ``[[stages]]`` table."""

    name: str = Field(min_length=1)
    gdd: float = Field(ge=0.0)


class Growth(_Strict):
    """Canopy and biomass — mirrors the ``[growth]`` table."""

    #: Radiation use efficiency, g dry matter per MJ intercepted.
    rue: float = Field(gt=0.0)
    #: LAI gained per °C·day while the canopy is still expanding.
    lai_slope: float = Field(gt=0.0)
    lai_max: float = Field(gt=0.0)
    #: Daily LAI loss fraction once the crop reaches a senescing stage.
    senescence_rate: float = Field(ge=0.0, le=1.0)
    #: Stages during which the canopy senesces instead of expanding.
    senescence_stages: tuple[str, ...] = ()


class Structure(_Strict):
    """Height allometry and the trellis that caps it — the ``[structure]`` table."""

    #: cm per √g of dry matter.
    k_height: float = Field(gt=0.0)
    max_height_cm: float = Field(gt=0.0)


class Water(_Strict):
    """Root zone and nitrogen — mirrors the ``[water]`` table."""

    root_zone_mm: float = Field(gt=0.0)
    kc_mid: float = Field(gt=0.0)
    depletion_fraction: float = Field(gt=0.0, lt=1.0)
    k_extinction: float = Field(gt=0.0)
    n_concentration: float = Field(gt=0.0)
    #: Cumulative shares of the season at which fertiliser is split.
    splits: tuple[float, ...] = ()


class Product(_Strict):
    """What is actually sold — mirrors the ``[product]`` table."""

    dry_matter_fraction: float = Field(gt=0.0, le=1.0)
    marketable_fraction: float = Field(gt=0.0, le=1.0)


class CropTemplate(_Strict):
    """One crop, exactly as it is written on disk."""

    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=32)
    species: str = Field(min_length=1, max_length=64)
    base_temp_c: float
    stages: tuple[Stage, ...] = Field(min_length=1)
    growth: Growth
    structure: Structure
    water: Water
    product: Product

    @model_validator(mode="after")
    def _check(self) -> CropTemplate:
        """Cross-field rules that pydantic cannot express per field."""
        names = [stage.name for stage in self.stages]
        unknown = [name for name in names if name not in STAGE_ORDER]
        if unknown:
            raise ValueError(f"未知物候阶段 {unknown}，可选 {list(STAGE_ORDER)}")
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise ValueError(f"物候阶段重复 {duplicated}")
        if self.stages[0].gdd != 0.0:
            raise ValueError(f"首个阶段 {self.stages[0].name!r} 的 gdd 必须为 0.0")
        for previous, current in itertools.pairwise(self.stages):
            if current.gdd <= previous.gdd:
                raise ValueError(
                    f"gdd 必须严格递增：{previous.name} {previous.gdd} → "
                    f"{current.name} {current.gdd}"
                )
        missing = [name for name in self.growth.senescence_stages if name not in names]
        if missing:
            raise ValueError(f"senescence_stages 引用了未定义的阶段 {missing}")
        for previous, current in itertools.pairwise(self.water.splits):
            if current <= previous:
                raise ValueError(f"splits 必须严格递增：{previous} → {current}")
        stray = [share for share in self.water.splits if not 0.0 < share <= 1.0]
        if stray:
            raise ValueError(f"splits 必须落在 (0, 1]：{stray}")
        return self

    @property
    def season_gdd(self) -> float:
        """Thermal time from sowing to the last stage — a rough cycle length."""
        return self.stages[-1].gdd


def _describe(error: ValidationError) -> str:
    """First validation failure as ``section.field: message``."""
    first = error.errors()[0]
    path = ".".join(str(part) for part in first["loc"]) or "<root>"
    return f"{path}: {first['msg']}"


def load_template(path: Path) -> CropTemplate:
    """Parse and validate one template file."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CropTemplateError(f"{path.name}: TOML 语法错误 — {exc}") from exc
    try:
        template = CropTemplate.model_validate(raw)
    except ValidationError as exc:
        raise CropTemplateError(f"{path.name}: {_describe(exc)}") from exc
    if template.id != path.stem:
        raise CropTemplateError(f"{path.name}: id {template.id!r} 必须与文件名一致")
    return template


def load_templates(directory: Path = TEMPLATE_DIR) -> dict[str, CropTemplate]:
    """Every ``*.toml`` in *directory*, keyed by crop id.

    No duplicate check is needed: :func:`load_template` ties the id to the file
    stem, and one directory cannot hold two files with the same stem.
    """
    paths = sorted(Path(directory).glob("*.toml"))
    if not paths:
        raise CropTemplateError(f"{directory} 下没有任何作物模板")
    return {path.stem: load_template(path) for path in paths}


TEMPLATES: dict[str, CropTemplate] = load_templates()
"""The bundled crops.  Loaded once at import: an edit takes effect on restart."""
