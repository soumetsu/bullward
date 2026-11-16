"""Verify completed-candle indicators and paper-account events with constructed data."""

from argparse import ArgumentParser
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from backend.indicators import IndicatorConfig, IndicatorRequest, calculate_indicators, save_configuration
from backend.observations import initialize_database, record_observation
from backend.paper import advance_replay_account, execute_action, paper_state, process_live_account
from backend.replay import replay_step


def at(time):
    return datetime.fromisoformat(f"2026-11-09T{time}Z")


def save(db, symbol, time, price, volume=None):
    message = {
        "id": symbol, "time": int(at(time).timestamp() * 1000), "price": price, "test_data": True,
    }
    if volume is not None:
        message["day_volume"] = volume
    return record_observation(db, message, at(time))


def seed(db):
    initialize_database(db)
    for index, price in enumerate([13, 10, 7, 13, 19, 7, 1, 30]):
        save(db, "SMA", f"14:{30 + index}:05", price, 1000 + (index + 1) * (index + 2) * 10)
    for index, (high, low) in enumerate([(12, 9), (15, 10), (13, 11)]):
        save(db, "RANGE", f"14:{30 + index}:05", low)
        save(db, "RANGE", f"14:{30 + index}:35", high)
    save(db, "RANGE", "14:33:05", 12)
    for time, price in [
        ("14:30:05", 99), ("14:31:05", 100), ("14:31:40", 110),
        ("14:32:05", 110), ("14:32:10", 110), ("14:32:50", 95),
        ("14:33:01", 95), ("14:33:10", 105), ("14:33:20", 106),
    ]:
        save(db, "AAPL", time, price)


def step(db, symbol, frame=None):
    return replay_step(db, symbol, at("14:30:00"), at("14:38:00"), "1m",
                       frame["observation"]["id"] if frame else None,
                       frame["through_id"] if frame else None)


def indicators(frame, config=()):
    request = IndicatorRequest(short=2, long=3, candles=[
        {**row["candle"], "ended": row["ended"]} for row in frame["candles"]
    ])
    return calculate_indicators(request, config)


def check_indicators(db):
    frame = None
    history = []
    while not frame or not frame["done"]:
        frame = step(db, "SMA", frame)
        history.append(indicators(frame))
    full = history[-1]
    assert [[point["value"] for point in series["points"]] for series in full["series"]] == [
        [None, 11.5, 8.5, 10, 16, 13, 4, None],
        [None, None, 10, 10, 13, 13, 9, None],
    ]
    assert [(m["timestamp"][11:16], m["direction"]) for m in full["crossovers"]] == [
        ("14:34", "up"), ("14:36", "down"),
    ]
    for item in history:
        completed = len(item["series"][0]["points"]) - 1
        for series, final in zip(item["series"], full["series"], strict=True):
            assert series["points"][:-1] == final["points"][:completed]
            assert series["points"][-1]["value"] is None
        assert item["crossovers"] == [m for m in full["crossovers"] if
                                      m["timestamp"] < item["series"][0]["points"][-1]["timestamp"]]
    print("PASS: all SMA values, crossover ties, replay prefixes, and forming candles")

    for length in (2, 3):
        config = save_configuration(db, IndicatorConfig(name=f"Range {length}", type="range", settings={"window": length}))
    frame = None
    while not frame or not frame["done"]:
        frame = step(db, "RANGE", frame)
    assert [series["points"][2]["value"] for series in indicators(frame, config)["series"][2:]] == [5, 6]
    print("PASS: persisted Range 2 and Range 3 values are 5 and 6")


EXPECTED = [
    ("600", 4, "0", "0", "0"), ("600", 4, "0", "40", "40"),
    ("710", 3, "10", "30", "40"), ("710", 3, "10", "-15", "-5"),
    ("1025", 0, "25", "0", "25"),
]


def check_paper(db):
    frame = step(db, "AAPL")
    book = str(uuid4())

    def order(action, quantity=None, request=None):
        return execute_action(db, book, "AAPL", action, request or str(uuid4()), quantity, frame["observation"]["id"])

    def advance():
        nonlocal frame
        previous_id = frame["observation"]["id"]
        frame = step(db, "AAPL", frame)
        for _ in range(2):
            advance_replay_account(db, book, "AAPL", previous_id, frame["observation"]["id"], frame["candles"])

    request_id = str(uuid4())
    buy = order("buy", 4, request_id)
    assert order("buy", 4, request_id) == buy and buy["status"] == "pending"
    assert order("wait") is None
    advance()
    assert order("hold") is None
    advance()
    advance()
    assert order("sell", 1)["status"] == "pending"
    advance()
    advance()
    advance()
    assert order("close")["quantity"] == 3
    advance()
    assert order("sell", 1)["status"] == "rejected"
    state = paper_state(db, book, "AAPL")
    assert sum(item["status"] == "filled" for item in state["orders"]) == 3
    assert len(state["orders"]) == 4
    actual = [tuple(row[key] for key in ("cash", "shares", "realized", "unrealized", "total"))
              for row in reversed(state["pnl"])]
    assert [tuple(map(Decimal, row)) for row in actual] == [
        tuple(map(Decimal, row)) for row in EXPECTED
    ], actual
    assert paper_state(db, "live", "AAPL")["orders"] == []
    print("PASS: three fills, five P&L rows, idempotent requests and replay steps, and empty-position rejection")


def check_live_matching(db):
    def order(action, quantity):
        return execute_action(db, "live", "AAPL", action, str(uuid4()), quantity)

    def observe(time, price):
        record_observation(db, {"id": "AAPL", "time": int(at(time).timestamp() * 1000), "price": price},
                           datetime.now(UTC))
        process_live_account(db)

    order("buy", 2)
    process_live_account(db)
    assert paper_state(db, "live", "AAPL")["orders"][0]["status"] == "pending"
    observe("15:00:05", 100)
    order("buy", 2)
    observe("15:00:10", 120)
    order("sell", 1)
    observe("15:00:15", 125)
    state = paper_state(db, "live", "AAPL")
    assert Decimal(state["account"]["cost_basis"]) == 330
    assert Decimal(state["pnl"][0]["realized"]) == 15
    assert Decimal(state["pnl"][0]["unrealized"]) == 45
    assert order("sell", 0.5)["status"] == "rejected"
    order("buy", 30)
    order("sell", 2)
    order("sell", 2)
    observe("15:00:20", 120)
    state = paper_state(db, "live", "AAPL")
    assert [row["status"] for row in state["orders"][:3]] == ["rejected", "filled", "rejected"]
    assert state["account"]["shares"] == 1
    print("PASS: live observation fence, weighted average of two buys, invalid quantity and fill-time rechecks")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path)
    args = parser.parse_args()
    if args.seed:
        if args.seed.exists():
            parser.error("The fixture database already exists.")
        seed(args.seed)
    else:
        with TemporaryDirectory(prefix="invest-prompt9-", dir=Path(__file__).resolve().parents[1] / "backend/data") as directory:
            db = Path(directory) / "observations.sqlite3"
            seed(db)
            check_indicators(db)
            check_paper(db)
            check_live_matching(db)
