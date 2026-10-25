/** Baseline Chinese dictionary — the key set below *is* the contract.
 *
 * Adding a language is two steps: copy this file, translate the values, and
 * register the result in `../index.ts`.  Because every locale is typed as
 * `Strings`, a translation missing a key fails the build instead of silently
 * falling back to Chinese at runtime.
 *
 * Placeholders are `{0}`, `{1}`, … — see `t()` in `../index.ts`.
 */

export const zh = {
  // --- shell ---------------------------------------------------------------
  "app.title": "Rancho · 农田作物仿真台",
  "brand.sub": "农田作物仿真台",
  "lang.label": "语言",

  // --- top bar -------------------------------------------------------------
  "field.scenario": "场景",
  "btn.run": "运行仿真",
  "status.connecting": "正在连接服务…",
  "status.connected": "服务已连接 · v{0}",
  "status.noBackend": "未连接后端（请先运行 rancho serve）",
  "status.dirtyHasBundle": "配置已改 · 点「运行仿真」重跑",
  "status.dirtyNoBundle": "待运行 · 点「运行仿真」开始",
  "status.running": "仿真运行中…",
  "status.ready": "就绪 · {0} 天 · {1} 秒",
  "status.failed": "仿真失败",

  // --- right panel: sections ----------------------------------------------
  "title.channels": "着色通道",
  "title.legend": "色标",
  "title.metrics": "当日状态",
  "title.layout": "蔬菜布局",
  "title.size": "地块尺寸",
  "title.field": "地块",
  "title.yield": "产量",
  "title.trajectory": "全季轨迹",

  // --- layout / painting ---------------------------------------------------
  "layout.toggle": "拖拽画笔（关闭后拖动为旋转视角）",
  "layout.enabledHint": "点击或拖拽地块给格子换作物，改动后自动重跑。",
  "layout.disabledHint": "该场景的作物物候由 CropForge 插件整块推进，不支持逐格混作。",
  "btn.fillAll": "整块填充",
  "btn.fillBands": "按列分带",
  "btn.reset": "还原场景",

  // --- field size ----------------------------------------------------------
  "slider.rows": "行数",
  "slider.cols": "列数",
  "slider.days": "天数",
  "grid.hintLarge": " · 格数较多，跑一次要十几秒到一分钟",
  "grid.hintDrag": "（松开后重跑）",
  "days.manual": "已手动设为 {0} 天 · {1}需 {2} 天成熟",
  "days.follow": "跟随最长作物：{0} {1} 天成熟",
  "crop.fallback": "作物",

  // --- run / overlays ------------------------------------------------------
  "run.overlay": "正在运行仿真：{0} × {1} 格 · {2} 天…",
  "overlay.connecting": "正在连接仿真服务…",
  "overlay.noBackend": "无法连接仿真服务。请在项目根目录执行 rancho serve 后刷新。",
  "overlay.presetsFail": "读取场景失败：{0}",
  "overlay.failed": "仿真失败：{0}",

  // --- yield ---------------------------------------------------------------
  "yield.total": "合计",
  "yield.gross": "总产",
  "yield.biomass": "生物量",
  "yield.harvestIndex": "收获指数",
  "yield.perCrop": "└ 产量",

  // --- legend --------------------------------------------------------------
  "legend.crops": "{0} 种作物",
  "legend.stages": "{0} 个阶段",

  // --- field info ----------------------------------------------------------
  "field.field": "地块",
  "field.area": "面积",
  "field.crop": "作物",
  "field.variety": "品种",
  "field.days": "天数",
  "field.site": "立地",
  "field.density": "密度",
  "field.irrigation": "灌溉",
  "field.fert": "施氮",
  "field.mixed": "{0} 种混作",
  "field.shelter": "设施",
  "field.shelterVal": "避雨遮阳 · 透光 {0}%",
  "field.weather": "气象",
  "field.weatherObserved": "实测",
  "field.weatherSynthetic": "合成",
  "field.irrigDrip": "定值 {0} mm · 覆盖 {1}%",
  "field.irrigRainfed": "雨养",

  // --- soil / terrain enums (keyed by the server's raw value) --------------
  "soil.loam": "壤土",
  "soil.clay": "黏土",
  "soil.sandy": "沙壤",
  "soil.andisol": "安山土",
  "terrain.flat": "平地",
  "terrain.slope": "缓坡",
  "terrain.undulating": "起伏",

  // --- metrics -------------------------------------------------------------
  "metric.spatial": "空间范围",
  "metric.biomass": "生物量",
  "metric.lai": "叶面积",
  "metric.moisture": "含水率",
  "metric.stress": "胁迫指数",
  "metric.temp": "气温",
  "metric.rain": "降水",
  "metric.irrig": "灌溉",

  // --- units ---------------------------------------------------------------
  "unit.cells": "格",
  "unit.days": "天",
  "unit.density": "株/m²",
  "unit.perPlant": "g/株",

  // --- timeline / tooltip --------------------------------------------------
  "day.label": "第 {0} 天",
  "date.format": "{0} 月 {1} 日",
  "tooltip.cell": "第 {0} 天 · 行 {1} / 列 {2}",
  "tooltip.crop": "作物",
  "tooltip.stage": "阶段",
  "channel.cropLayout": "作物布局",
  "na": "—",

  // --- bottom bar ----------------------------------------------------------
  "chart.hint": "拖动时间轴或点击曲线查看当日",
  "speed.label": "速度",
  "speed.slow": "慢",
  "speed.normal": "常速",
  "speed.fast": "快",
  "field.exaggerate": "株高夸张",

  // --- chart.ts ------------------------------------------------------------
  "chart.lai": "LAI",
  "chart.moisture": "含水率",
  "chart.stress": "胁迫",
  "chart.rain": "降水",
  "chart.dayFirst": "第 1 天",
  "chart.dayLast": "第 {0} 天",
};

/** What every locale has to supply: the baseline keys, plus room to index
 *  dynamically (soil/terrain are addressed as `soil.${value}` at runtime). */
export type Strings = Record<keyof typeof zh, string> & Record<string, string>;
