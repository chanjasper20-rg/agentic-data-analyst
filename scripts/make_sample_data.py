"""Generate a synthetic multi-site solar generation dataset for the demo.

Produces roughly two years of daily records across five sites, with a few
deliberate faults planted so that "find anomalies in this data" has something
real to find:

  * SITE_C  -- a nine-day total outage in the second summer
  * SITE_D  -- inverter degradation that worsens over the final ten months
  * SITE_A  -- a handful of missing generation readings (sensor dropout)
  * SITE_E  -- one impossible spike (double the plant's rated capacity)

Run:  python scripts/make_sample_data.py
"""

from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 20260721
START = date(2024, 1, 1)
END = date(2025, 12, 31)
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_solar_generation.csv"

# site_id, site_name, capacity_kwp, latitude-ish seasonality strength, base performance ratio
SITES = [
    ("SITE_A", "Tuas Rooftop", 1850.0, 0.10, 0.82),
    ("SITE_B", "Jurong Warehouse", 3200.0, 0.12, 0.79),
    ("SITE_C", "Senoko Industrial", 5400.0, 0.11, 0.81),
    ("SITE_D", "Changi Logistics Hub", 2750.0, 0.13, 0.84),
    ("SITE_E", "Pasir Panjang Terminal", 4100.0, 0.09, 0.77),
]

# Planted faults
OUTAGE_SITE = "SITE_C"
OUTAGE_START = date(2025, 7, 14)
OUTAGE_DAYS = 9

DEGRADE_SITE = "SITE_D"
DEGRADE_START = date(2025, 3, 1)
DEGRADE_FLOOR = 0.62  # ends the year at 62% of expected output

DROPOUT_SITE = "SITE_A"
DROPOUT_DATES = {date(2024, 9, 5), date(2024, 9, 6), date(2025, 2, 18), date(2025, 11, 3)}

SPIKE_SITE = "SITE_E"
SPIKE_DATE = date(2025, 5, 22)


def daily_irradiance(day: date, seasonality: float, rng: random.Random) -> float:
    """Peak-sun-hours for the day (kWh/m2), with seasonal and weather variation."""
    day_of_year = day.timetuple().tm_yday
    seasonal = 1.0 + seasonality * math.sin(2 * math.pi * (day_of_year - 80) / 365.25)
    clear_sky = 5.1 * seasonal

    # Monsoon months are cloudier and more variable.
    if day.month in (11, 12, 1):
        weather = rng.betavariate(2.0, 2.2)
    elif day.month in (6, 7, 8):
        weather = rng.betavariate(4.5, 1.6)
    else:
        weather = rng.betavariate(3.5, 1.8)

    return round(max(0.4, clear_sky * (0.45 + 0.75 * weather)), 3)


def build_rows() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    rows: list[dict[str, object]] = []
    outage_days = {OUTAGE_START + timedelta(days=i) for i in range(OUTAGE_DAYS)}
    total_days = (END - START).days

    day = START
    while day <= END:
        for site_id, site_name, capacity, seasonality, base_pr in SITES:
            irradiance = daily_irradiance(day, seasonality, rng)
            performance_ratio = base_pr + rng.gauss(0, 0.015)
            availability = 100.0

            if site_id == DEGRADE_SITE and day >= DEGRADE_START:
                progress = (day - DEGRADE_START).days / max(1, (END - DEGRADE_START).days)
                performance_ratio *= 1.0 - (1.0 - DEGRADE_FLOOR) * progress

            if site_id == OUTAGE_SITE and day in outage_days:
                performance_ratio = 0.0
                availability = 0.0
            elif rng.random() < 0.012:  # occasional partial-availability day anywhere
                availability = round(rng.uniform(45.0, 92.0), 1)
                performance_ratio *= availability / 100.0

            generation = capacity * irradiance * max(0.0, performance_ratio)

            if site_id == SPIKE_SITE and day == SPIKE_DATE:
                generation *= 2.4  # impossible reading: well above plant capacity

            generation_value: object = round(generation, 1)
            if site_id == DROPOUT_SITE and day in DROPOUT_DATES:
                generation_value = ""  # sensor dropout -> blank cell

            rows.append(
                {
                    "date": day.isoformat(),
                    "site_id": site_id,
                    "site_name": site_name,
                    "capacity_kwp": capacity,
                    "irradiance_kwh_m2": irradiance,
                    "generation_kwh": generation_value,
                    "availability_pct": round(availability, 1),
                }
            )
        day += timedelta(days=1)

    assert len(rows) == (total_days + 1) * len(SITES)
    return rows


def main() -> None:
    rows = build_rows()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"Wrote {len(rows):,} rows to {OUT_PATH} ({size_kb:.0f} KB)")
    print(f"  Date range : {rows[0]['date']} to {rows[-1]['date']}")
    print(f"  Sites      : {', '.join(site[0] for site in SITES)}")
    print("  Planted faults:")
    print(f"    {OUTAGE_SITE}  outage from {OUTAGE_START} for {OUTAGE_DAYS} days")
    print(f"    {DEGRADE_SITE}  gradual degradation from {DEGRADE_START}")
    print(f"    {DROPOUT_SITE}  {len(DROPOUT_DATES)} missing generation readings")
    print(f"    {SPIKE_SITE}  impossible spike on {SPIKE_DATE}")


if __name__ == "__main__":
    main()
