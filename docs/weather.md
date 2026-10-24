# 按经纬度取气象：模块设计与取舍

仿真台原本只有一套合成气候：给定气候档位与随机种子，生成逐日气温、辐射、降水、湿度与风速。
这保证了可复现，但萨尔瓦多的真实旱季辐射、昼夜温差和雨量分配，跟任何一套参数化曲线都不完全一样。
本模块补上第二条路——按经纬度取开源实测数据，两条路写同一份 CSV、走同一个 FAO-56 链路。

代码：`rancho/weather.py`（取数与序列化）、`rancho/climate.py`（合成器）。

## 数据源

| 项目 | 取值 |
| --- | --- |
| 提供方 | [Open-Meteo](https://open-meteo.com/)（无需注册、无 API key） |
| 历史数据 | `archive-api.open-meteo.com/v1/archive`，ERA5 / ERA5T 再分析 |
| 近期数据 | `api.open-meteo.com/v1/forecast`，日期落在再分析延迟（约 5 天）以内时启用 |
| 空间分辨率 | 0.25°（约 25 km），响应里返回实际取到的网格点 |
| 时间基准 | `timezone=auto`，按坐标所在时区切日 |
| 许可 | CC-BY 4.0，署名 Open-Meteo 与 ECMWF/Copernicus（ERA5） |

选它而不是 NASA POWER 或气象站数据，是因为日尺度所需的六个量一次请求就能拿全，
辐射已经是日累计 MJ/m²，且不需要密钥——对「输入一个坐标就能跑」的场景最省事。

## 字段映射

CropForge 的 `Weather.from_csv` 只认七列，映射与单位处理如下：

| CSV 列 | Open-Meteo 日变量 | 处理 |
| --- | --- | --- |
| `date` | `time` | ISO 日期 |
| `tmax_c` | `temperature_2m_max` | 直接取 |
| `tmin_c` | `temperature_2m_min` | 直接取 |
| `radiation_mj` | `shortwave_radiation_sum` | 已是 MJ/m²/日，不做换算 |
| `rainfall_mm` | `precipitation_sum` | 直接取 |
| `humidity_pct` | `relative_humidity_2m_mean` | 日平均相对湿度，直接取 |
| `wind_ms` | `wind_speed_10m_mean` | km/h ÷ 3.6；用日均而非日最大，ET₀ 用的是平均风速 |

风速特意取 `wind_speed_10m_mean` 而不是 `_max`：Penman-Monteith 里的 u₂ 是日平均风速，
用日最大值会把 ET₀ 抬高 30–50%。

## 数据流

```
坐标 + 日期窗口
   │
   ├─ weather.source == "synthetic" ──► climate.generate_weather() ──┐
   │                                                                  │
   └─ weather.source == "open-meteo" ─► 磁盘缓存命中？ ─是─┐          │
                                          │否              │          │
                                          ▼                │          │
                                    Open-Meteo ─► 校验/补缺 ─┘          │
                                                                       ▼
                                          WeatherSeries（统一数据模型）
                                                        │
                                     protected？─是─► apply_cover()
                                                        │
                                                        ▼
                                          write_weather_csv() ─► CropForge
                                                        │
                                                        ▼
                                              bundle.meta["weather"]
```

合成与实测共用 `WeatherDay` / `WeatherSeries` 和同一个 `write_weather_csv`，
所以两种来源的产量口径、ET₀ 公式、棚内换算完全一致，差异只在数列本身。

## 配置

```python
class WeatherConfig(BaseModel):
    source: Literal["synthetic", "open-meteo"] = "synthetic"
    year: int = 2025          # 播期日序对应的公历年；合成气候忽略
    timeout_s: float = 20.0
    fallback: bool = True     # 取数失败时退回合成气候（并在 meta 标注）
    offline: bool = False     # 只读缓存，不联网
```

场景里只写日序（`sowing_doy`），实测数据需要一年才能落到具体日期：
`start = date(year, 1, 1) + (sowing_doy - 1) 天`。合成气候不需要年份，因此固定钉在 2026 年这个
epoch 上——季节曲线只读日序，这样老预设重跑仍能逐字节复现原 CSV。

## 命令行与接口

```bash
# 查看某个坐标某段时间的气象
uv run rancho weather --lat 13.69 --lon -89.22 --start 2025-11-06 --days 130

# 借场景的坐标、播期与棚体参数，导出棚内气象
uv run rancho weather --preset salvador-observed --protected --csv weather.csv

# 机器可读（含逐日序列）
uv run rancho weather --preset salvador-observed --json

# 让任意场景改用实测气象
uv run rancho simulate --preset salvador-greenhouse --weather open-meteo --year 2025
```

HTTP 侧：

```
GET /api/weather?lat=13.69&lon=-89.22&start=2025-11-06&days=30[&protected=true&transmission=0.55]
→ {"meta": {...}, "days": [{"date": "...", "tmax_c": ..., ...}]}
```

取数失败返回 502 与原因；命令行则直接以非零码退出。

## 缓存与可复现

| 情况 | 行为 |
| --- | --- |
| 窗口结束日早于「今天 − 6 天」 | 再分析已定稿，缓存永久有效 |
| 窗口落在最近几天 / 未来 | 6 小时后过期重取 |
| `offline=True` | 只读缓存；无缓存则按 `fallback` 决定报错还是退回合成 |

缓存文件按 `(坐标, 起止日)` 命名，落在 `<work-dir>/weather/open-meteo/` 下，
内容包含请求参数、抓取时间与原始响应，便于审计——某个 bundle 的产量到底来自哪份数据，可以逐日对回去。

坐标按三位小数入缓存键，Open-Meteo 本身会吸附到 0.25° 网格，因此同村的不同地块多半共用一份缓存。

## 质量处理

上游数据并非总是干净：ERA5T 偶有缺测，个别日子最高温低于最低温。写入前统一做四件事：

1. **缺测插值**：单点缺测按前后邻日线性插值，序列首尾用最近有效值；整段全空则报错而不是补零。
2. **温度倒挂**：`tmax < tmin` 时交换，并记入 `notes`。
3. **物理范围**：辐射夹在 0–45 MJ/m²/d，湿度 1–100 %，风速 0–40 m/s，降水不为负。
4. **口径记录**：走了预报接口、命中了缓存、插值了几处，全部写进 `meta.weather.notes`。

## 棚内换算

实测值同样是「露天」气象。`protected=True` 时在写 CSV 之前换算：

| 量 | 变换 | 理由 |
| --- | --- | --- |
| 辐射 | × `cover_transmission` | 膜 0.85 × 遮阳网 0.65 ≈ 0.55 |
| 降水 | 置 0 | 避雨棚，雨水不进棚 |
| 风速 | 0.2 m/s | 四周防虫网 + 棚体挡风，只剩微弱对流 |
| 湿度 | +5 %，上限 95 % | 蒸腾水汽滞留 |

这一步必须在 CropForge 之前完成，否则 FAO-56 会按露天风速算 ET₀，把棚内需水量高估一倍。

## 已知局限

1. **网格粗于地块**：0.25°≈25 km，一个网格覆盖整个山谷。棚址与小气候的差异（海拔、坡向、棚体热惯性）不在数据里。
2. **不做高程订正**：响应返回的是网格点海拔（萨尔瓦多该点为 719 m），与农场实际海拔可能差几十米；Open-Meteo 支持传 `elevation=` 做递减率订正，本模块暂未启用，因为场景里还没有可靠的高程字段。
3. **棚温被低估**：模型没有棚体热平衡，实测气温直接进模型；正午闭棚时真实棚温会更高。
4. **再分析不是观测**：ERA5 是模式再分析，日辐射与降水的局地误差在山区可达 20–30 %。要更准应换用地面站或卫星产品。
5. **不给未来做长期规划**：预报接口只覆盖未来 16 天，超出范围会明确报错，而不是外推。

## 实测 vs 合成（萨尔瓦多大棚番茄，2025-11-06 → 2026-03-15）

| 指标 | 合成气候 | Open-Meteo ERA5 |
| --- | --- | --- |
| 棚内平均气温 | 24.8 ℃（11.1–34.9） | 24.7 ℃（13.1–34.6） |
| 棚内平均辐射 | 9.5 MJ/m²/d | 11.6 MJ/m²/d |
| 棚内平均湿度 | 84 % | 66 % |
| 平均 ET₀ | 1.98 mm/d | 2.31 mm/d |
| 全季灌溉 | 288 mm | 333 mm |
| 产量（鲜重） | 62.7 t/ha | 66.0 t/ha |

两条线的年均温几乎一致，差距集中在能量与水汽：合成曲线把热带雨季的阴天抹平均了，
实测旱季晴天更多（透光后仍高约 22 %），湿度也低了 18 个百分点，于是 ET₀ 高 17 %、灌溉多 45 mm。
这说明「按气候档位拍参数」能给出量级正确的答案，但季节内的波动必须靠实测。
