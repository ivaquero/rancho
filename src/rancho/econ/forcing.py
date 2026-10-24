"""Hourly forcing for the economics probe: weather, tariff and input prices.

Three series drive every decision in the model:

* **outdoor temperature**, which sets the passive greenhouse temperature and
  therefore the heating or cooling load;
* **solar radiation**, which sets the natural light integral the crops top up
  with LEDs;
* **prices** — the time-of-use electricity tariff, the irrigation water tariff
  and the fertiliser market price.

All of them are seeded and generated in one pass, so two arms of an experiment
can be compared on *identical* forcing.  Nothing here reads the network: the
probe has to be reproducible before it can be convincing.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Final

from rancho.econ.spec import (
    HOURS_PER_DAY,
    MOL_PAR_PER_MJ,
    SiteProfile,
    TariffSpec,
)

#: Hours between sunrise and sunset used to shape the daily radiation curve.
SUNRISE_HOUR: Final = 6.0
SUNSET_HOUR: Final = 18.0

#: Amplitude of the day-to-day temperature and cloud noise (K, fraction).
_NOISE_TEMP_K: Final = 0.45
_NOISE_CLOUD: Final = 0.06

#: Mean reversion and innovation scale of the price random walks.  The water
#: innovation is sized so that the *stationary* standard deviation of the
#: multiplier is 8 % — an irrigation tariff moves by a few percent a month, not
#: by tens of percent.  Sizing it on the raw unit-variance innovation instead
#: would give a 23 % band and, on a bad draw, a negative price.
_WATER_DRIFT: Final = 0.90
_WATER_SIGMA: Final = 0.35
_WATER_BAND: Final = 0.10
_FERT_DRIFT: Final = 0.94
_FERT_SIGMA: Final = 0.028
_ELEG_SPOT_SIGMA: Final = 0.07


@dataclass(frozen=True, slots=True)
class Forcing:
    """Hourly exogenous series, flattened to tuples for cheap random access."""

    site: SiteProfile
    tariff: TariffSpec
    days: int
    temp_out: tuple[float, ...]
    radiation_in: tuple[float, ...]
    tariff_price: tuple[float, ...]
    tariff_block: tuple[str, ...]
    water_price: tuple[float, ...]
    fert_price: tuple[float, ...]

    @property
    def hours(self) -> int:
        return self.days * HOURS_PER_DAY

    def day_hours(self, day: int) -> range:
        """Hour indices belonging to a zero-based day."""
        start = day * HOURS_PER_DAY
        return range(start, start + HOURS_PER_DAY)

    def dli(self, day: int) -> float:
        """Inside-canopy natural light integral for a day, in mol/m²."""
        return sum(self.radiation_in[hour] * MOL_PAR_PER_MJ for hour in self.day_hours(day))

    def mean_temp(self, day: int) -> float:
        hours = self.day_hours(day)
        return sum(self.temp_out[hour] for hour in hours) / HOURS_PER_DAY

    def day_radiation(self, day: int) -> float:
        """Inside-canopy global radiation for a day, in MJ/m²."""
        return sum(self.radiation_in[hour] for hour in self.day_hours(day))

    def summary(self) -> dict[str, float]:
        """Season-level aggregates worth printing in the report header."""
        count = len(self.temp_out)
        return {
            "mean_temp_c": round(sum(self.temp_out) / count, 2),
            "min_temp_c": round(min(self.temp_out), 2),
            "max_temp_c": round(max(self.temp_out), 2),
            "mean_dli": round(sum(self.dli(day) for day in range(self.days)) / self.days, 2),
            "mean_tariff": round(sum(self.tariff_price) / count, 3),
            "min_tariff": round(min(self.tariff_price), 3),
            "max_tariff": round(max(self.tariff_price), 3),
            "mean_water_price": round(sum(self.water_price) / count, 2),
            "mean_fert_price": round(sum(self.fert_price) / count, 2),
        }


def build_forcing(
    site: SiteProfile,
    tariff: TariffSpec,
    *,
    days: int,
    seed: int,
    water_price_base: float,
    fert_price_base: float,
) -> Forcing:
    """Generate one reproducible hourly forcing block.

    Radiation is a half-sine between 06:00 and 18:00 scaled to a daily total of
    ``clear_sky_mj × cloud``; cloud has a slow AR(1) component, so overcast
    spells persist instead of flickering hour to hour.  Prices get their own
    slow components: a daily spot multiplier on electricity, a scarcity drift
    on water and a random walk on fertiliser.
    """
    if days < 1:
        raise ValueError("days must be positive")

    rng = random.Random(seed)
    temp_out: list[float] = []
    radiation_in: list[float] = []
    tariff_price: list[float] = []
    tariff_block: list[str] = []
    water_price: list[float] = []
    fert_price: list[float] = []

    shape = [
        max(0.0, math.sin(math.pi * (hour - SUNRISE_HOUR) / (SUNSET_HOUR - SUNRISE_HOUR)))
        for hour in range(HOURS_PER_DAY)
    ]
    shape_total = sum(shape)

    temp_state = cloud_state = water_state = fert_state = 0.0
    for day in range(days):
        temp_state = 0.82 * temp_state + rng.gauss(0.0, 1.0)
        cloud_state = 0.78 * cloud_state + rng.gauss(0.0, 1.0)
        water_state = _WATER_DRIFT * water_state + rng.gauss(0.0, _WATER_SIGMA)
        fert_state = _FERT_DRIFT * fert_state + rng.gauss(0.0, _FERT_SIGMA)

        seasonal = math.sin(2.0 * math.pi * (day - 40.0) / 365.0)
        cloud = site.cloud_base + site.cloud_spread * cloud_state + 0.05 * seasonal
        cloud = min(1.05, max(0.22, cloud + rng.gauss(0.0, _NOISE_CLOUD)))

        day_mean = site.mean_temp_c + site.seasonal_amplitude_k * seasonal
        day_mean += _NOISE_TEMP_K * temp_state
        daily_inside = site.clear_sky_mj * cloud * site.cover_transmission

        spot = min(1.22, max(0.82, math.exp(rng.gauss(0.0, _ELEG_SPOT_SIGMA))))
        day_water = water_price_base * min(
            1.0 + _WATER_BAND, max(1.0 - _WATER_BAND, 1.0 + _WATER_BAND * water_state)
        )
        day_fert = fert_price_base * math.exp(fert_state)

        for hour in range(HOURS_PER_DAY):
            diurnal = site.temp_amplitude_k / 2.0 * math.cos(2.0 * math.pi * (hour - 15.0) / 24.0)
            temp_out.append(day_mean + diurnal + rng.gauss(0.0, 0.28))
            radiation_in.append(daily_inside * shape[hour] / shape_total)
            tariff_price.append(tariff.rate(hour) * spot)
            tariff_block.append(tariff.block(hour))
            water_price.append(day_water)
            fert_price.append(day_fert)

    return Forcing(
        site=site,
        tariff=tariff,
        days=days,
        temp_out=tuple(temp_out),
        radiation_in=tuple(radiation_in),
        tariff_price=tuple(tariff_price),
        tariff_block=tuple(tariff_block),
        water_price=tuple(water_price),
        fert_price=tuple(fert_price),
    )
