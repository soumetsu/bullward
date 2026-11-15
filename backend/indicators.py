"""Completed-candle indicator registry and saved configurations."""

from collections import deque
from contextlib import closing
from fractions import Fraction
import sqlite3
from typing import Annotated

from pydantic import BaseModel, Field, FiniteFloat, StringConstraints

Window = Annotated[int, Field(strict=True, gt=0)]


class IndicatorSettings(BaseModel):
    window: Window


class IndicatorConfig(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    type: str
    settings: IndicatorSettings


class IndicatorCandle(BaseModel):
    timestamp: str
    close: FiniteFloat
    high: FiniteFloat
    low: FiniteFloat
    ended: bool


class IndicatorRequest(BaseModel):
    short: Window
    long: Window
    candles: list[IndicatorCandle]


def sma(window):
    return sum(Fraction(str(item.close)) for item in window) / len(window)


def price_range(window):
    return Fraction(str(max(item.high for item in window))) - Fraction(str(min(item.low for item in window)))


# Each function receives a full trailing window of completed candles in UTC order.
TYPES = {
    "sma": ("Simple moving average", "price", sma),
    "range": ("Rolling price range", "price", price_range),
}


def available_types():
    return [
        {"type": key, "label": label, "units": units,
         "settings": {"window": {"type": "integer", "minimum": 1}}}
        for key, (label, units, _) in TYPES.items()
    ]


def values(candles, kind, length):
    calculate = TYPES[kind][2]
    window = deque(maxlen=min(length, len(candles)))
    for item in candles:
        if item.ended:
            window.append(item)
        yield calculate(window) if item.ended and len(window) == length else None


def calculate_indicators(request, configured=()):
    if request.short >= request.long:
        raise ValueError("The short window must be smaller than the long window.")
    candles = sorted(request.candles, key=lambda item: item.timestamp)
    if len({item.timestamp for item in candles}) != len(candles):
        raise ValueError("Candle times must be unique.")
    short = list(values(candles, "sma", request.short))
    long = list(values(candles, "sma", request.long))
    series = [
        {"id": "short", "type": "sma", "name": f"Short SMA ({request.short})", "values": short},
        {"id": "long", "type": "sma", "name": f"Long SMA ({request.long})", "values": long},
        *[
            {"id": item["id"], "type": item["type"], "name": item["name"],
             "values": list(values(candles, item["type"], item["settings"]["window"]))}
            for item in configured
        ],
    ]
    side = 0
    crossovers = []
    for candle, a, b in zip(candles, short, long, strict=True):
        if a is not None and b is not None and a != b:
            current = 1 if a > b else -1
            if side and current != side:
                crossovers.append({"timestamp": candle.timestamp, "direction": "up" if current > 0 else "down"})
            side = current
    for item in series:
        item["points"] = [
            {"timestamp": candle.timestamp, "value": float(value) if value is not None else None}
            for candle, value in zip(candles, item.pop("values"), strict=True)
        ]
    return {"series": series, "crossovers": crossovers}


def configurations(db_path):
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chart_indicators'"
        ).fetchone()
        if not exists:
            return []
        return [
            {"id": row_id, "name": name, "type": kind, "settings": {"window": window}}
            for row_id, name, kind, window in connection.execute(
                "SELECT id, name, type, window FROM chart_indicators ORDER BY id"
            )
        ]


def save_configuration(db_path, config):
    if config.type not in TYPES:
        raise ValueError("Choose an available indicator type.")
    if config.settings.window > 2**63 - 1:
        raise ValueError("The window is too large to save.")
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS chart_indicators (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                type TEXT NOT NULL, window INTEGER NOT NULL CHECK (window > 0)
            )
        """)
        try:
            connection.execute(
                "INSERT INTO chart_indicators (name, type, window) VALUES (?, ?, ?)",
                (config.name, config.type, config.settings.window),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("An indicator already uses that name.") from error
    return configurations(db_path)
