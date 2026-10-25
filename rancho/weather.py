"""Daily weather for a coordinate — the open-data bridge into CropForge.

CropForge reads a seven-column daily CSV.  Rancho can fill that file from two
places: its own seeded climate generator (:mod:`rancho.climate`) or a real
open archive queried by latitude/longitude.  This module owns the shared data
model, the CSV serialisation and the network client, so both paths emit the
same file for the same series and every downstream number stays comparable.

The provider is `Open-Meteo <https://open-meteo.com/>`_: no key, no quota
dance, and daily aggregates that already match what an agronomic model needs —
radiation as a daily sum in MJ/m², temperature extremes, precipitation,
humidity and mean wind.  Historical dates come from the ERA5 reanalysis
(0.25° ≈ 25 km grid); dates newer than the reanalysis lag come from the
forecast endpoint, which is flagged in the run metadata rather than silently
mixed in.

Only the standard library is used for transport: the payload is a small JSON
document fetched once per window and cached on disk, so adding an HTTP
dependency would buy nothing.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from rancho import __version__
from rancho.scenario import ScenarioConfig, WeatherSource, horizon_days

#: Column order CropForge expects; the unit of each column is in the name.
WEATHER_HEADER: Final = "date,tmax_c,tmin_c,radiation_mj,rainfall_mm,humidity_pct,wind_ms"

ARCHIVE_URL: Final = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL: Final = "https://api.open-meteo.com/v1/forecast"

#: Daily variables requested from Open-Meteo.  Daily means are preferred over
#: hourly aggregation: the provider already sums radiation over the day, which
#: is the quantity FAO-56 and every crop model actually consume.
DAILY_FIELDS: Final = (
    "temperature_2m_max",
    "temperature_2m_min",
    "shortwave_radiation_sum",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "wind_speed_10m_mean",
)

#: Wind is reported in km/h; the CSV family uses m/s.
KMH_PER_MS: Final = 3.6
#: ERA5T lands with a few days of lag; newer dates come from the forecast API.
ARCHIVE_LAG_DAYS: Final = 5
#: Forecast API horizon because of which a start date can be rejected up front.
FORECAST_HORIZON_DAYS: Final = 16
#: Provisional data is re-fetched after this long; settled reanalysis never is.
CACHE_TTL_HOURS: Final = 6
HTTP_ATTEMPTS: Final = 2
UA: Final = f"rancho/{__version__} (+https://github.com/ivaquero/rancho)"


class WeatherError(RuntimeError):
    """Base class for weather-source failures."""


class WeatherFetchError(WeatherError):
    """The provider was unreachable, or answered with unusable data."""


@dataclass(frozen=True, slots=True)
class WeatherDay:
    """One day of the CropForge weather contract, in model units."""

    day: date
    tmax_c: float
    tmin_c: float
    radiation_mj: float
    rainfall_mm: float
    humidity_pct: float
    wind_ms: float


@dataclass(frozen=True, slots=True)
class WeatherSeries:
    """A contiguous daily series plus everything needed to attribute it."""

    days: tuple[WeatherDay, ...]
    source: WeatherSource
    label: str
    provider: str
    label_en: str = ""
    label_es: str = ""
    #: Site the series describes.  Synthetic weather has no observation site,
    #: but carrying the farm's own coordinates keeps every bundle comparable.
    latitude: float | None = None
    longitude: float | None = None
    grid_latitude: float | None = None
    grid_longitude: float | None = None
    elevation_m: float | None = None
    timezone: str | None = None
    cached: bool = False
    notes: tuple[str, ...] = ()

    @property
    def start(self) -> date:
        return self.days[0].day

    @property
    def end(self) -> date:
        return self.days[-1].day

    def summary(self) -> dict[str, float | int]:
        """Season-level aggregates — the numbers worth printing or charting."""
        if not self.days:
            return {}
        count = len(self.days)
        mean_temp = sum((day.tmax_c + day.tmin_c) / 2.0 for day in self.days) / count
        return {
            "mean_temp_c": round(mean_temp, 1),
            "min_temp_c": round(min(day.tmin_c for day in self.days), 1),
            "max_temp_c": round(max(day.tmax_c for day in self.days), 1),
            "mean_radiation_mj": round(sum(day.radiation_mj for day in self.days) / count, 1),
            "rain_total_mm": round(sum(day.rainfall_mm for day in self.days), 1),
            "rain_days": sum(1 for day in self.days if day.rainfall_mm >= 1.0),
            "mean_humidity_pct": round(sum(day.humidity_pct for day in self.days) / count),
            "mean_wind_ms": round(sum(day.wind_ms for day in self.days) / count, 1),
        }

    def meta(self) -> dict[str, Any]:
        """Bundle/JSON metadata: provenance first, statistics second."""
        base: dict[str, Any] = {
            "source": self.source,
            "label": self.label,
            "label_en": self.label_en,
            "label_es": self.label_es,
            "provider": self.provider,
            "latitude": None if self.latitude is None else round(self.latitude, 4),
            "longitude": None if self.longitude is None else round(self.longitude, 4),
            "grid": (
                [round(self.grid_latitude, 4), round(self.grid_longitude, 4)]
                if self.grid_latitude is not None and self.grid_longitude is not None
                else None
            ),
            "elevation_m": None if self.elevation_m is None else round(self.elevation_m),
            "timezone": self.timezone,
            "start": self.start.isoformat() if self.days else None,
            "end": self.end.isoformat() if self.days else None,
            "days": len(self.days),
            "cached": self.cached,
            "notes": list(self.notes),
        }
        base.update(self.summary())
        return base


def write_weather_csv(path: Path, series: WeatherSeries) -> Path:
    """Serialise *series* in the format CropForge's ``Weather.from_csv`` reads.

    Formatting lives here and nowhere else: the synthetic generator keeps raw
    floats, so regenerating a preset reproduces its CSV byte for byte.
    """
    lines: list[str] = [WEATHER_HEADER]
    for day in series.days:
        lines.append(
            f"{day.day.isoformat()},{day.tmax_c:.1f},{day.tmin_c:.1f},{day.radiation_mj:.1f},"
            f"{day.rainfall_mm:.1f},{day.humidity_pct:.0f},{day.wind_ms:.1f}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def apply_cover(series: WeatherSeries, transmission: float) -> WeatherSeries:
    """Re-express an open-field series as the climate *inside* a structure.

    Rain never reaches the canopy, the cover and shade net pass only
    ``transmission`` of the incoming radiation, the air is stiller and more
    humid.  Doing this before the CSV is written (rather than after the
    simulation) keeps the FAO-56 ET₀ honest: greenhouse reference
    evapotranspiration really is 60–70 % of the open-field value.
    """
    covered = tuple(
        replace(
            day,
            radiation_mj=day.radiation_mj * transmission,
            rainfall_mm=0.0,
            wind_ms=0.2,
            humidity_pct=min(95.0, day.humidity_pct + 5.0),
        )
        for day in series.days
    )
    note = f"棚内小气候：透光 {transmission:.2f}、遮雨、风速 0.2 m/s、湿度 +5%"
    return replace(series, days=covered, notes=(*series.notes, note))


def _interpolate(values: list[float | None]) -> tuple[list[float], int]:
    """Fill gaps linearly; a wholly empty variable is an error, not a zero."""
    known = [(index, value) for index, value in enumerate(values) if value is not None]
    if not known:
        raise WeatherFetchError("该时段没有任何有效值，换一个时间范围或坐标")
    if len(known) == len(values):
        return [float(value) for value in values if value is not None], 0

    xs = [index for index, _ in known]
    ys = [float(value) for _, value in known]
    filled: list[float] = []
    for index, value in enumerate(values):
        if value is not None:
            filled.append(float(value))
        elif index <= xs[0]:
            filled.append(ys[0])
        elif index >= xs[-1]:
            filled.append(ys[-1])
        else:
            filled.append(_lerp(xs, ys, index))
    return filled, len(values) - len(known)


def _lerp(xs: list[int], ys: list[float], x: int) -> float:
    for position in range(1, len(xs)):
        if x <= xs[position]:
            left, right = xs[position - 1], xs[position]
            weight = (x - left) / (right - left)
            return ys[position - 1] + weight * (ys[position] - ys[position - 1])
    return ys[-1]


def _column(daily: dict[str, Any], name: str, expected: int) -> list[float | None]:
    raw = daily.get(name)
    if not isinstance(raw, list) or len(raw) != expected:
        raise WeatherFetchError(f"变量 {name} 缺失或长度不符（期望 {expected} 天）")
    return [None if value is None else float(value) for value in raw]


def _cache_is_fresh(record: dict[str, Any]) -> bool:
    """Settled reanalysis is immutable; anything recent expires with the TTL."""
    try:
        fetched = datetime.fromisoformat(str(record["fetched_at"]))
    except KeyError, TypeError, ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    settled_before = date.today() - timedelta(days=ARCHIVE_LAG_DAYS + 1)
    try:
        if date.fromisoformat(str(record.get("end"))) < settled_before:
            return True
    except TypeError, ValueError:
        pass
    return datetime.now(UTC) - fetched < timedelta(hours=CACHE_TTL_HOURS)


def _get_json(url: str, timeout_s: float) -> dict[str, Any]:
    """GET one JSON document, translating transport failures into one error type."""
    last: Exception | None = None
    for attempt in range(HTTP_ATTEMPTS):
        request = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise WeatherFetchError(f"气象服务返回 HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt + 1 < HTTP_ATTEMPTS:
                time.sleep(0.6)
            continue
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WeatherFetchError("气象服务返回了非 JSON 响应") from exc
        if not isinstance(document, dict):
            raise WeatherFetchError("气象服务返回结构异常")
        if document.get("error"):
            raise WeatherFetchError(f"气象服务报错：{document.get('reason', '未知原因')}")
        return document
    raise WeatherFetchError(f"无法连接气象服务（{last}）")


@dataclass(slots=True)
class OpenMeteoClient:
    """Daily series for a coordinate from Open-Meteo's archive or forecast API.

    ``cache_dir=None`` disables the disk cache; ``offline=True`` reads the
    cache only, which is what reproducible/air-gapped runs want.
    """

    timeout_s: float = 20.0
    cache_dir: Path | None = None
    offline: bool = False

    def fetch(self, latitude: float, longitude: float, start: date, days: int) -> WeatherSeries:
        if days < 1:
            raise WeatherError("days 必须 ≥ 1")
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            raise WeatherError(f"坐标超出范围：({latitude}, {longitude})")

        end = start + timedelta(days=days - 1)
        today = date.today()
        if start > today + timedelta(days=FORECAST_HORIZON_DAYS):
            raise WeatherError(
                f"起点 {start} 超出可用范围（最多预报到 {today + timedelta(days=FORECAST_HORIZON_DAYS)}）"
            )

        endpoint = FORECAST_URL if end >= today - timedelta(days=ARCHIVE_LAG_DAYS) else ARCHIVE_URL
        url = self._build_url(endpoint, latitude, longitude, start, end)
        payload, cached = self._load(endpoint, url, latitude, longitude, start, end)
        return self._parse(payload, latitude, longitude, start, days, cached)

    # ------------------------------------------------------------------ http

    @staticmethod
    def _build_url(endpoint: str, latitude: float, longitude: float, start: date, end: date) -> str:
        params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(DAILY_FIELDS),
            "timezone": "auto",
        }
        return f"{endpoint}?{urllib.parse.urlencode(params)}"

    def _cache_path(
        self, endpoint: str, latitude: float, longitude: float, start: date, end: date
    ) -> Path | None:
        if self.cache_dir is None:
            return None
        kind = "forecast" if endpoint == FORECAST_URL else "archive"
        name = f"{kind}_{latitude:.3f}_{longitude:.3f}_{start}_{end}.json"
        return self.cache_dir / "open-meteo" / name

    def _load(
        self,
        endpoint: str,
        url: str,
        latitude: float,
        longitude: float,
        start: date,
        end: date,
    ) -> tuple[dict[str, Any], bool]:
        cache_path = self._cache_path(endpoint, latitude, longitude, start, end)
        if cache_path is not None and cache_path.is_file():
            try:
                record = json.loads(cache_path.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                record = {}
            if _cache_is_fresh(record) and isinstance(record.get("response"), dict):
                return record["response"], True

        if self.offline:
            where = cache_path if cache_path is not None else "（未配置缓存目录）"
            raise WeatherFetchError(f"离线模式下没有可用缓存：{where}")

        response = _get_json(url, self.timeout_s)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "endpoint": endpoint,
                "request": {
                    "latitude": latitude,
                    "longitude": longitude,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "end": end.isoformat(),
                "response": response,
            }
            cache_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return response, False

    # ----------------------------------------------------------------- parse

    def _parse(
        self,
        payload: dict[str, Any],
        latitude: float,
        longitude: float,
        start: date,
        days: int,
        cached: bool,
    ) -> WeatherSeries:
        daily = payload.get("daily")
        if not isinstance(daily, dict):
            raise WeatherFetchError("响应缺少 daily 段")
        times = daily.get("time")
        if not isinstance(times, list) or len(times) != days:
            raise WeatherFetchError(f"响应天数不符：期望 {days}，实际 {len(times or [])}")

        try:
            dates = [date.fromisoformat(str(stamp)) for stamp in times]
        except ValueError as exc:
            raise WeatherFetchError("响应中的日期无法解析") from exc

        notes: list[str] = []
        raw_tmax = _column(daily, "temperature_2m_max", days)
        raw_tmin = _column(daily, "temperature_2m_min", days)
        raw_radiation = _column(daily, "shortwave_radiation_sum", days)
        raw_rain = _column(daily, "precipitation_sum", days)
        raw_humidity = _column(daily, "relative_humidity_2m_mean", days)
        raw_wind = _column(daily, "wind_speed_10m_mean", days)

        tmax, filled_hi = _interpolate(raw_tmax)
        tmin, filled_lo = _interpolate(raw_tmin)
        radiation, filled_rad = _interpolate(raw_radiation)
        rain, filled_rain = _interpolate(raw_rain)
        humidity, filled_rh = _interpolate(raw_humidity)
        wind_kmh, filled_wind = _interpolate(raw_wind)
        gaps = filled_hi + filled_lo + filled_rad + filled_rain + filled_rh + filled_wind
        if gaps:
            notes.append(f"补齐 {gaps} 个缺测值（线性插值）")

        wind = [max(0.0, min(40.0, value / KMH_PER_MS)) for value in wind_kmh]
        swaps = 0
        series: list[WeatherDay] = []
        for index, day in enumerate(dates):
            high, low = tmax[index], tmin[index]
            if high < low:
                high, low = low, high
                swaps += 1
            series.append(
                WeatherDay(
                    day=day,
                    tmax_c=high,
                    tmin_c=low,
                    radiation_mj=max(0.0, min(45.0, radiation[index])),
                    rainfall_mm=max(0.0, rain[index]),
                    humidity_pct=max(1.0, min(100.0, humidity[index])),
                    wind_ms=wind[index],
                )
            )
        if swaps:
            notes.append(f"修正 {swaps} 天最高/最低气温倒挂")

        endpoint_used = (
            "预报接口"
            if dates[-1] >= date.today() - timedelta(days=ARCHIVE_LAG_DAYS)
            else "ERA5 再分析"
        )
        if endpoint_used == "预报接口":
            notes.append("该时段超出再分析延迟，取预报接口的近期结果")
        if cached:
            notes.append("来自本地缓存")

        elevation = payload.get("elevation")
        return WeatherSeries(
            days=tuple(series),
            source="open-meteo",
            label="Open-Meteo · ERA5",
            label_en="Open-Meteo · ERA5",
            label_es="Open-Meteo · ERA5",
            provider="Open-Meteo",
            latitude=latitude,
            longitude=longitude,
            grid_latitude=_as_float(payload.get("latitude")),
            grid_longitude=_as_float(payload.get("longitude")),
            elevation_m=_as_float(elevation),
            timezone=str(payload.get("timezone")) if payload.get("timezone") else None,
            cached=cached,
            notes=tuple(notes),
        )


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def fetch_weather(
    latitude: float,
    longitude: float,
    start: date,
    days: int,
    *,
    cache_dir: Path | None = None,
    timeout_s: float = 20.0,
    offline: bool = False,
    protected: bool = False,
    cover_transmission: float = 1.0,
) -> WeatherSeries:
    """Open-field (or in-cover) daily series for one coordinate and window."""
    series = OpenMeteoClient(timeout_s=timeout_s, cache_dir=cache_dir, offline=offline).fetch(
        latitude, longitude, start, days
    )
    return apply_cover(series, cover_transmission) if protected else series


def resolve_weather(cfg: ScenarioConfig, path: Path, work_root: Path) -> WeatherSeries:
    """Produce the CSV CropForge reads, and return the series for the bundle.

    ``synthetic`` never touches the network.  ``open-meteo`` fetches the real
    window and, when the provider is unreachable, degrades to the synthetic
    climate *only if* the scenario allows it — and says so in the metadata, so a
    fallback run can never be mistaken for an observed one.
    """
    days = horizon_days(cfg) + 8

    if cfg.weather.source == "open-meteo":
        # Real data is anchored to a calendar year; the scenario itself only
        # knows a sowing *day of year*, so the year is what turns one into the
        # other.  A fallback keeps those dates, so the window still reads right.
        start = date(cfg.weather.year, 1, 1) + timedelta(days=cfg.sowing_doy - 1)
        try:
            series = fetch_weather(
                cfg.latitude,
                cfg.longitude,
                start,
                days,
                cache_dir=work_root / "weather",
                timeout_s=cfg.weather.timeout_s,
                offline=cfg.weather.offline,
            )
        except WeatherError as exc:
            if not cfg.weather.fallback:
                raise
            series = _synthetic(
                cfg, start=start, days=days, note=f"实测气象不可用（{exc}），已退回合成气候"
            )
    else:
        series = _synthetic(cfg, start=_synthetic_start(cfg), days=days)

    if cfg.protected:
        series = apply_cover(series, cfg.cover_transmission)
    write_weather_csv(path, series)
    # CropForge gets a short buffer beyond the horizon, but the bundle reports
    # the window the run actually covers — otherwise "130 days" and the weather
    # range disagree by the buffer and every statistic is diluted.
    return replace(series, days=series.days[: cfg.days])


def _synthetic_start(cfg: ScenarioConfig) -> date:
    """Synthetic weather is calendar-free, so it stays pinned to one epoch.

    Keeping the year fixed is what makes an old preset regenerate its CSV byte
    for byte: the seasonal sine only ever sees a day-of-year.
    """
    from rancho.climate import SYNTHETIC_EPOCH

    return SYNTHETIC_EPOCH + timedelta(days=cfg.sowing_doy - 1)


def _synthetic(
    cfg: ScenarioConfig, *, start: date, days: int, note: str | None = None
) -> WeatherSeries:
    """Adapter around :func:`rancho.climate.generate_weather` (imported late).

    The lazy import keeps the dependency one-way: :mod:`rancho.climate` builds
    the data model in this module, never the other way round.
    """
    from rancho.climate import generate_weather

    series = generate_weather(days=days, climate=cfg.climate, seed=cfg.seed, start=start)
    series = replace(series, latitude=cfg.latitude, longitude=cfg.longitude)
    return replace(series, notes=(*series.notes, note)) if note else series
