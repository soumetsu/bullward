import csv
from pathlib import Path

from fastapi import FastAPI

app = FastAPI(title="Project 4 Invest API")
SAMPLE_CANDLES = Path(__file__).parent / "data" / "sample-candles.csv"
CANDLE_FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/history")
def history() -> list[dict[str, str | float | int]]:
    with SAMPLE_CANDLES.open(newline="", encoding="utf-8") as candle_file:
        reader = csv.DictReader(candle_file)
        if reader.fieldnames != CANDLE_FIELDS:
            raise ValueError(f"Expected CSV columns: {', '.join(CANDLE_FIELDS)}")

        candles = [
            {
                "timestamp": row["timestamp"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            }
            for row in reader
        ]

    return sorted(candles, key=lambda candle: candle["timestamp"])
