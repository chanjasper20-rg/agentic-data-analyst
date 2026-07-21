"""Generate a synthetic e-commerce orders dataset for testing.

Deliberately messier than the solar sample -- this one exercises data cleaning,
not just anomaly hunting. Roughly 18 months of orders with these problems
planted in it:

  * duplicate order_ids (the same order exported twice)
  * inconsistent category casing and stray whitespace
  * a handful of negative quantities (returns booked as orders)
  * blank region values
  * two mixed date formats in the ship_date column
  * a few unit_price outliers (decimal point in the wrong place)
  * discount_pct occasionally recorded as 0.15 instead of 15

Run:  python scripts/make_test_orders.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 20260721
START = date(2025, 1, 1)
END = date(2026, 6, 30)
ORDERS = 4200
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "test_orders.csv"

# category, product, base_price, seasonal_month_boost
CATALOG = [
    ("Electronics", "Wireless Earbuds", 129.00, 12),
    ("Electronics", "4K Monitor", 449.00, 11),
    ("Electronics", "Mechanical Keyboard", 189.00, None),
    ("Home", "Espresso Machine", 699.00, 12),
    ("Home", "Air Purifier", 329.00, 6),
    ("Home", "Cookware Set", 249.00, 11),
    ("Apparel", "Running Shoes", 159.00, 3),
    ("Apparel", "Rain Jacket", 219.00, 11),
    ("Apparel", "Merino Socks", 29.00, 12),
    ("Outdoors", "Camping Tent", 389.00, 5),
    ("Outdoors", "Trail Backpack", 179.00, 4),
    ("Outdoors", "Insulated Bottle", 45.00, None),
]

REGIONS = ["APAC", "EMEA", "NA", "LATAM"]
REGION_WEIGHTS = [0.38, 0.27, 0.28, 0.07]
CHANNELS = ["web", "mobile_app", "marketplace", "retail_partner"]
CHANNEL_WEIGHTS = [0.44, 0.33, 0.16, 0.07]

# Planted mess
DUPLICATE_COUNT = 18
NEGATIVE_QTY_COUNT = 12
BLANK_REGION_RATE = 0.008
MESSY_CATEGORY_RATE = 0.05
ALT_DATE_FORMAT_RATE = 0.03
PRICE_OUTLIER_COUNT = 6
FRACTIONAL_DISCOUNT_RATE = 0.02


def order_date(rng: random.Random) -> date:
    """Orders ramp up over the period, with a Q4 bulge."""
    span = (END - START).days
    day = START + timedelta(days=int(rng.betavariate(1.6, 1.2) * span))
    if day.month in (11, 12) and rng.random() < 0.25:
        return day
    return day


def build_rows() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    rows: list[dict[str, object]] = []

    for index in range(ORDERS):
        category, product, base_price, boost_month = CATALOG[rng.randrange(len(CATALOG))]
        placed = order_date(rng)

        quantity = rng.choices([1, 2, 3, 4, 6], weights=[0.58, 0.24, 0.10, 0.05, 0.03])[0]
        if boost_month and placed.month == boost_month:
            quantity += rng.choices([0, 1, 2], weights=[0.6, 0.3, 0.1])[0]

        unit_price = round(base_price * rng.uniform(0.94, 1.06), 2)
        discount = rng.choices([0, 5, 10, 15, 25], weights=[0.55, 0.16, 0.15, 0.09, 0.05])[0]
        region = rng.choices(REGIONS, weights=REGION_WEIGHTS)[0]
        channel = rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]

        ship_lag = rng.choices([1, 2, 3, 5, 9], weights=[0.30, 0.34, 0.20, 0.11, 0.05])[0]
        shipped = placed + timedelta(days=ship_lag)

        rows.append(
            {
                "order_id": f"ORD-{100000 + index}",
                "order_date": placed.isoformat(),
                "ship_date": shipped.isoformat(),
                "customer_id": f"CUST-{rng.randrange(1, 1400):05d}",
                "region": region,
                "channel": channel,
                "category": category,
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount_pct": discount,
            }
        )

    dirty(rows, rng)
    return rows


def dirty(rows: list[dict[str, object]], rng: random.Random) -> None:
    """Apply the planted data-quality problems in place."""
    for row in rows:
        if rng.random() < MESSY_CATEGORY_RATE:
            row["category"] = rng.choice(
                [str(row["category"]).upper(), str(row["category"]).lower(), f" {row['category']} "]
            )
        if rng.random() < BLANK_REGION_RATE:
            row["region"] = ""
        if rng.random() < ALT_DATE_FORMAT_RATE:
            year, month, day = str(row["ship_date"]).split("-")
            row["ship_date"] = f"{day}/{month}/{year}"
        if row["discount_pct"] and rng.random() < FRACTIONAL_DISCOUNT_RATE:
            row["discount_pct"] = round(float(row["discount_pct"]) / 100, 2)

    for row in rng.sample(rows, NEGATIVE_QTY_COUNT):
        row["quantity"] = -abs(int(row["quantity"]))

    for row in rng.sample(rows, PRICE_OUTLIER_COUNT):
        row["unit_price"] = round(float(row["unit_price"]) * 100, 2)

    # Same order exported twice -- identical except for a re-issued row position.
    rows.extend([dict(row) for row in rng.sample(rows, DUPLICATE_COUNT)])
    rows.sort(key=lambda row: (str(row["order_date"]), str(row["order_id"])))


def main() -> None:
    rows = build_rows()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"Wrote {len(rows):,} rows to {OUT_PATH} ({size_kb:.0f} KB)")
    print(f"  Date range : {START} to {END}")
    print("  Planted problems:")
    print(f"    {DUPLICATE_COUNT} duplicated order_ids")
    print(f"    {NEGATIVE_QTY_COUNT} negative quantities")
    print(f"    {PRICE_OUTLIER_COUNT} unit_price outliers (100x)")
    print("    inconsistent category casing, blank regions, mixed ship_date formats")
    print("    some discount_pct recorded as a fraction instead of a percentage")


if __name__ == "__main__":
    main()
