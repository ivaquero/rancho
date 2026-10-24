# 作物模板

一个作物的参数以前分散在四处：生长物候在 `vegetables.py`、根区水氮在
`agronomy.CROP_WATER`、产品折率在 `scenario.CROP_PROFILES`、售价与温度响应在
`econ/spec.py`。改一处忘三处，就会得到「按番茄长、按生菜卖、按玉米定价」的作物。
现在这四处都从 `rancho/crops/*.toml` 读——五份文件，五个作物，加一个作物就是加一个文件。

## 文件位置

```
rancho/crops/
  __init__.py    模板的 pydantic 模型、物候词汇 STAGE_ORDER、加载与校验
  tomato.toml    番茄（带完整注释，可当范本复制）
  cucumber.toml  黄瓜
  lettuce.toml   生菜
  wheat.toml     小麦（物候归 CropForge，模板只有系数与产品）
  maize.toml     玉米（同上）
```

`id` 必须等于文件名，也等于 `ScenarioConfig.crop` 的取值。模板在 **import 时**加载，
改完重启进程即可，没有编译步骤。TOML 与 Python 字面量解析到同一批 IEEE-754 双精度值，
所以抽取过程没有移动任何仿真结果——`tests/test_crops.py` 用精确相等（不是近似）钉住
原值。

## 字段

根级（必须写在任何 `[[stages]]` 之前）：

| 字段 | 单位 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | — | `^[a-z][a-z0-9_-]*$`，≤32 | 作物键，等于文件名 |
| `label` | — | 非空，≤32 | 界面 / 报告 / 账本上的显示名 |
| `species` | — | 非空，≤64 | 拉丁学名 |
| `base_temp_c` | ℃ | 浮点 | 热时间基温，低于此温度不计积温。**物候段之一**，见下 |

`[[stages]]` 数组表，一条一个物候阈值（`base_temp_c` / `[[stages]]` / `[growth]` /
`[structure]` 是**一组**：要么四个都写，要么一个都不写——不写表示物候归 CropForge
自带插件，模板只管 Rancho 这一侧的系数）：

| 字段 | 单位 | 约束 | 说明 |
| --- | --- | --- | --- |
| `name` | — | 取自 `STAGE_ORDER` | 物候阶段名，如 `flowering` |
| `gdd` | ℃·d | 首个必须为 `0.0`，其后严格递增 | 进入该阶段所需的累计有效积温 |

`[growth]`：

| 字段 | 单位 | 约束 | 说明 |
| --- | --- | --- | --- |
| `rue` | g/MJ | > 0 | 辐射利用效率：每 MJ 截获辐射合成的干物质克数 |
| `lai_slope` | (㎡/㎡)/℃·d | > 0 | 冠层张开期每 ℃·d 增加的 LAI |
| `lai_max` | ㎡/㎡ | > 0 | 封行后的叶面积上限 |
| `senescence_rate` | 1/日 | 0–1 | 进入衰老阶段后每日 LAI 损失比例 |
| `senescence_stages` | — | 必须在该作物的 `stages` 中 | 哪些阶段衰老而非继续张开；生菜设 `[]` |
| `transplant_lai` | ㎡/㎡ | > 0，默认 `0.45` | 定植苗的起始 LAI；经济层的农场季从它起步 |

`[structure]`：`k_height`（cm per √g 干物质）、`max_height_cm`（吊蔓 / 框体限高，cm），
均 > 0。

`[water]`：

| 字段 | 单位 | 约束 | 说明 |
| --- | --- | --- | --- |
| `root_zone_mm` | mm | > 0 | 有效根区贮水量 |
| `kc_mid` | — | > 0 | FAO-56 生育中期作物系数 |
| `depletion_fraction` | — | 0–1（开区间） | FAO-56 允许消耗比例 p |
| `k_extinction` | — | > 0 | Beer-Lambert 消光系数，用于冠层配光 |
| `n_concentration` | kg/kg | > 0 | 植株组织含氮率，用于带走量 |
| `splits` | 季内累积比 | 严格递增且落在 (0, 1] | 追肥时点，如 `0.18` = 第 18 % 生育期 |

`[product]`——「卖出去的是什么」：

| 字段                   | 单位 | 约束     | 说明                                                                     |
| ---------------------- | ---- | -------- | ------------------------------------------------------------------------ |
| `dry_matter_fraction`  | —    | (0, 1]   | 干物质率，干重折鲜重用；谷物按收获水分计，取 1.0                         |
| `marketable_fraction`  | —    | (0, 1]   | 商品率，扣除裂果、次品后的可售比例                                       |
| `harvest_index`        | —    | (0, 0.95]| 地上干物质进入收获器官的比例；番茄 0.32、黄瓜 0.55、生菜 0.78、谷物 0.38–0.46 |

收获指数归 `[product]` 而不是情景，是因为「整株里有多少能卖」和「卖的那部分是几成干物质」
本来就是一个问题；**逐格混作**下每种作物要各折各的，情景里再留一个全局值就没有意义了。
`ScenarioConfig.harvest_index` 保留为实验用覆盖（默认 `None` = 取模板），预设都不再写它。

`[market]`——只有经济层读，谷物不写：

| 字段 | 单位 | 约束 | 说明 |
| --- | --- | --- | --- |
| `price_yuan_kg` | 元/kg | > 0 | 售价 |
| `dli_target` | mol/m²/日 | > 0 | 目标日累积光积分 |
| `light_w_m2` | W/m² | > 0 | 补光灯安装密度下的照度 |
| `setpoint_day_c` / `setpoint_night_c` | ℃ | 夜 ≤ 昼 | 温控设定点 |
| `t_opt_c` / `t_min_c` / `t_max_c` / `t_critical_c` | ℃ | `t_min < t_opt < t_max ≤ t_critical` | 基数温度与损伤起点 |
| `damage_per_hour_k` | 1/(K·h) | ≥ 0 | 每超临界 1 K 每小时的品质损伤 |
| `growth_max_g_m2_day` | g/m²/日 | > 0 | 潜在可售鲜重（饱和光响应上限） |
| `dli_half` | mol/m²/日 | > 0 | 半饱和 DLI |
| `fert_g_m2_day` | g/m²/日 | > 0 | 每日施肥需求 |

## 加载时校验

错误一律在 import 时抛 `CropTemplateError`，消息形如
`basil.toml: water.depletion_fraction: Input should be less than 1`。完整规则：

1. 未知键一律拒绝，不静默回落到默认值（`extra="forbid"`）。
2. 阶段名必须在 `STAGE_ORDER` 内；同名阶段重复也算错。
3. 第一个阶段的 `gdd` 必须是 `0.0`，其后严格递增。
4. `senescence_stages` 只能引用该文件里定义过的阶段。
5. `splits` 严格递增，且落在 (0, 1]。
6. `id` 必须等于文件名。
7. 物候四段必须整组出现，缺一段就报错。
8. `[market]` 的温度基数必须有序，夜间设定点不得高于白天。
9. TOML 语法错误会带上文件名。

手工填数写 `rue = 2` 也能通过：整数字面量会被放宽成浮点。

## 改一个作物

改数值 → 保存 → 重启进程。四处派生表自动跟着走：

- `vegetables.VegetableProfile`（插件读的扁平运行时视图；有物候段的作物才有）
- `agronomy.CROP_WATER`（根区水量平衡；经济层也取这一份）
- `scenario.CROP_PROFILES`（干物质率、商品率、收获指数，用于干重折鲜重）
- `econ.CropSpec`（售价、温度响应、光响应、`transplant_lai`）

有物候段的作物还额外获得一张通行证：`scenario.MOSAIC_KEYS` 由 `has_phenology` 算出，
进了这张表的作物才能写进 `crop_map` 逐格混作（见 README「逐格混作」）。

`uv run rancho crops` 打印当前生效的五份模板、来源（自建物候 / CropForge 插件）与路径，
改完可先核对一眼。

## 加一个作物

复制 `tomato.toml` → 改名 → 改数值 → 重启。**不需要改 Python**：

- `scenario.CROP_KEYS` 由「内置作物 + 模板目录」算出，`ScenarioConfig(crop=...)` 不再靠
  硬编码 `Literal` 兜底，新模板立刻可选；
- `vegetables.PLUGINS` 由模板生成插件类并 `@register_crop`，新模板立刻有插件；
- 三张派生表同理自动扩充。

只有一处仍需手写：想让新作物进经济层竞价，要在 `econ/spec.py` 的 `CROPS` 里给它一行
**床参数**（`area_m2`、起始含氮量、田间持水量、凋萎点）——面积与起手土壤是一畦地的属性，
不是作物的属性，所以它们不进模板。缺 `[market]` 段的作物会被 `econ.spec._crop` 直接指出
缺哪一段，不会静默变成 0 元。

## 边界与已知取舍

- **谷物的物候不在这里**：小麦 / 玉米的物候由 CropForge 自带插件模型，模板只承担
  Rancho 的系数与产品折算，`has_phenology` 为假时 `vegetables` 不生成插件。
- **售价进了模板，但它是时价**：`price_yuan_kg` 描述「这种作物值多少」，地区与季节价差
  仍要改模板或将来按情景覆盖；经济层没有价格情景机制。
- **数组表偏长**：8 段物候占 24 行，是刻意换来的可读性——阶段名与阈值在同一屏内可见。
