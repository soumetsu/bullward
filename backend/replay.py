"""Read a recorded receipt range in the order the backend saw it."""

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from .candles import load_observed_minutes
from .observations import SOURCE
from .timeframes import aggregate_candles


def recorded_symbols(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return [
            {"symbol": ticker, "first_receipt": first, "last_receipt": last}
            for ticker, first, last in connection.execute(
                """SELECT ticker, MIN(received_at_utc), MAX(received_at_utc)
                   FROM observations WHERE source = ? GROUP BY ticker ORDER BY ticker""",
                (SOURCE,),
            )
        ]


def replay_step(db_path: Path, ticker: str, start: datetime, end: datetime,
                interval: str, after_id: int | None, through_id: int | None) -> dict:
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError("Provide a timezone and an end time after the start.")
    start = start.astimezone(UTC)
    bounds = (start.isoformat(timespec="microseconds"), end.astimezone(UTC).isoformat(timespec="microseconds"))
    if not db_path.exists():
        return {"observation": None, "candles": [], "done": True, "through_id": 0}

    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        if through_id is None:
            through_id, = connection.execute(
                """SELECT COALESCE(MAX(id), 0) FROM observations
                   WHERE source = ? AND ticker = ? AND received_at_utc >= ? AND received_at_utc < ?""",
                (SOURCE, ticker, *bounds),
            ).fetchone()
        cursor = ("", 0)
        if after_id is not None:
            cursor = connection.execute(
                """SELECT received_at_utc, id FROM observations WHERE id = ? AND id <= ?
                   AND source = ? AND ticker = ? AND received_at_utc >= ? AND received_at_utc < ?""",
                (after_id, through_id, SOURCE, ticker, *bounds),
            ).fetchone()
            if cursor is None:
                raise ValueError("The replay cursor does not belong to this selection.")
        frames = connection.execute(
            """SELECT received_at_utc, id, source_time_ms FROM observations
               WHERE source = ? AND ticker = ? AND received_at_utc >= ? AND received_at_utc < ?
                 AND id <= ? AND (received_at_utc, id) > (?, ?)
               ORDER BY received_at_utc, id LIMIT 2""",
            (SOURCE, ticker, *bounds, through_id, *cursor),
        ).fetchall()

    if not frames:
        return {"observation": None, "candles": [], "done": True, "through_id": through_id}

    receipt, row_id, source_time_ms = frames[0]
    as_of = datetime.fromisoformat(receipt)
    minutes, _ = load_observed_minutes(
        db_path, ticker, as_of=as_of, received_from=start, through_id=through_id, at_id=row_id,
    )
    return {
        "observation": {"id": row_id, "received_at": receipt, "source_time_ms": source_time_ms},
        "candles": aggregate_candles(minutes, interval, as_of),
        "done": len(frames) == 1,
        "through_id": through_id,
    }
