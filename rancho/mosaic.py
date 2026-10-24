"""Several crops in one field: which plant each crop plugin gets.

CropForge attaches one :class:`~cropforge.plugins.CropPlugin` per field, so a
field is normally one crop.  A realistic greenhouse is not — beds are planted
to different vegetables and share one irrigation system, one soil and one
cover.  This module supplies the missing piece: a plugin that owns one crop
sub-plugin per planted species and hands every plant to the one growing in
*its* cell.

Nothing in this file forks CropForge.  The dispatcher only relies on the public
``step`` contract, plus the per-plant form Rancho's own plugins expose; crops
whose phenology CropForge owns are rejected by :data:`scenario.MOSAIC_KEYS`
before a run starts, because their ``step`` advances the whole field in one
call and cannot be told which cells belong to it.

Everything that has to agree on the layout — validation, the water balance, the
growth dispatch and the bundle — reads the same :func:`scenario.crop_grid`, so
"one crop repeated to fill the field" and "a hand-painted mosaic" are the same
case as far as the rest of the code is concerned.
"""

from __future__ import annotations

from typing import Any

from cropforge._plugins_base import CropPlugin

from rancho.vegetables import PLUGINS as VEGETABLE_PLUGINS
from rancho.vegetables import GreenhouseVegetable

#: Every crop this dispatcher can grow, mapped to the plugin Rancho owns for it.
#: Templated crops only — see :data:`rancho.scenario.MOSAIC_KEYS` for why, and
#: note that the two stay in sync because one derives from the templates.
CELL_PLUGINS: dict[str, type[GreenhouseVegetable]] = dict(VEGETABLE_PLUGINS)


class CropMosaic(CropPlugin):
    """One sub-plugin per planted crop, keyed by the crop map of the field.

    Registered like any other plugin::

        field.use_plugin(CropMosaic, phase=2, crop_map=grid, rows=r, cols=c)

    where *grid* is row-major and exactly ``rows * cols`` long.  A uniform map
    costs one extra dictionary lookup per plant and nothing else: the sub-plugin
    runs the same sequence it would have run directly.
    """

    species = "mixed"

    def __init__(
        self,
        crop_map: list[str] | None = None,
        rows: int = 0,
        cols: int = 0,
    ) -> None:
        map_list = list(crop_map or [])
        if rows > 0 and cols > 0 and len(map_list) != rows * cols:
            raise ValueError(
                f"crop_map 有 {len(map_list)} 项，地块是 {rows}×{cols}（应 {rows * cols} 项）"
            )

        keys: dict[str, None] = {}
        for key in map_list:
            keys.setdefault(key, None)
        self.keys: tuple[str, ...] = tuple(keys)
        lookup = {key: index for index, key in enumerate(self.keys)}

        unknown = [key for key in self.keys if key not in CELL_PLUGINS]
        if unknown:
            raise ValueError(f"逐格混作缺少作物插件：{unknown}")

        self._index = [lookup[key] for key in map_list]
        self._cols = max(1, cols)
        self._plugins: list[GreenhouseVegetable] = [CELL_PLUGINS[key]() for key in self.keys]

    def step(self, state: Any, env: Any) -> Any:
        """Advance every plant one day through the plugin planted in its cell."""
        index = self._index
        plugins = self._plugins
        cols = self._cols
        for plant in state.plants:
            if not plant.alive:
                continue
            cell = plant.row * cols + plant.col
            plugins[index[cell] if 0 <= cell < len(index) else 0].step_plant(plant, env)
        return state
