"""Deterministic fixture generation, not inferred building behaviour."""
import csv
from datetime import datetime, timedelta
from pathlib import Path

out = Path(__file__).with_name("synthetic-office.csv")
with out.open("w", newline="", encoding="utf-8") as stream:
    writer = csv.writer(stream)
    writer.writerow(["timestamp", "kwh"])
    for day in range(16):
        for hour in range(24):
            at = datetime(2026, 10, 1) + timedelta(days=day, hours=hour)
            value = 1.2 if hour < 6 or hour >= 20 else (5.4 if 8 <= hour < 18 else 2.8)
            value += ((day * 3 + hour) % 5 - 2) * .05
            if day >= 14 and hour < 6:
                value += 3.8
            writer.writerow([at.isoformat(), round(value, 2)])
