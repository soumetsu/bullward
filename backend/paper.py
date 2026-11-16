"""Local long-only paper accounts and append-only valuation history."""

from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import sqlite3

from .candles import load_observed_minutes
from .observations import SOURCE
from .timeframes import aggregate_candles

STARTING_CASH = "1000"
LIVE_BOOK = "live"
DEFAULT_ORDER_SIZE = 1
MAX_DECISION_AGE = timedelta(minutes=5)

SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS paper_accounts (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, cash TEXT NOT NULL,
    shares INTEGER NOT NULL DEFAULT 0 CHECK (shares >= 0),
    cost_basis TEXT NOT NULL DEFAULT '0', realized TEXT NOT NULL DEFAULT '0',
    cursor_id INTEGER REFERENCES observations(id)
);
CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
    request_id TEXT NOT NULL, symbol TEXT NOT NULL, action TEXT NOT NULL, side TEXT NOT NULL,
    requested_quantity TEXT, quantity INTEGER, submitted_at TEXT NOT NULL, after_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'filled', 'rejected')), reason TEXT,
    UNIQUE (account_id, request_id)
);
CREATE TABLE IF NOT EXISTS paper_fills (
    order_id INTEGER PRIMARY KEY REFERENCES paper_orders(id),
    observation_id INTEGER NOT NULL REFERENCES observations(id),
    price TEXT NOT NULL, executed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_pnl (
    id INTEGER PRIMARY KEY, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
    symbol TEXT NOT NULL, event TEXT NOT NULL, event_key TEXT NOT NULL,
    event_time TEXT NOT NULL, price_time TEXT NOT NULL, price TEXT NOT NULL,
    cash TEXT NOT NULL, shares INTEGER NOT NULL, realized TEXT NOT NULL,
    unrealized TEXT NOT NULL, total TEXT NOT NULL,
    UNIQUE(account_id, event, event_key)
);
"""


@contextmanager
def transaction(db_path):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        yield connection


def stamp(time):
    return time.isoformat(timespec="microseconds")


def _has_accounts(connection):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'paper_accounts'"
    ).fetchone() is not None


def initialize_paper(db_path):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        if not _has_accounts(connection):
            connection.executescript(SCHEMA)


def _account(connection, book):
    return connection.execute("SELECT * FROM paper_accounts WHERE id = ?", (book,)).fetchone()


def _observation(connection, row_id, symbol):
    return connection.execute(
        """SELECT id, source_time_ms, received_at_utc, source_message_json ->> '$.price' AS price
           FROM observations WHERE id = ? AND ticker = ? AND source = ?""",
        (row_id, symbol, SOURCE),
    ).fetchone()


def execute_action(db_path, book, symbol, action, request_id, quantity=None, cursor=None):
    """Submit a manual intent for the next observed price."""
    if action in ("hold", "wait"):
        return None
    if action not in ("buy", "sell", "close"):
        raise ValueError("Unknown paper action.")
    initialize_paper(db_path)
    with transaction(db_path) as connection:
        return submit_order(connection, book, symbol, action, request_id, quantity, cursor)


def ensure_account(connection, book, symbol, cursor):
    connection.execute(
        "INSERT OR IGNORE INTO paper_accounts (id, symbol, cash, cursor_id) VALUES (?, ?, ?, ?)",
        (book, symbol, STARTING_CASH, cursor),
    )
    account = _account(connection, book)
    if symbol != account["symbol"] or (book != LIVE_BOOK and cursor != account["cursor_id"]):
        raise ValueError("The order must use the current paper account and replay observation.")
    return account


def submit_order(connection, book, symbol, action, request_id, quantity, cursor):
    """Record an order in the caller's SQLite transaction."""
    requested = repr(quantity)
    prior = connection.execute(
        "SELECT * FROM paper_orders WHERE account_id = ? AND request_id = ?", (book, request_id)
    ).fetchone()
    if prior:
        if (symbol, action, requested) != (prior["symbol"], prior["action"], prior["requested_quantity"]):
            raise ValueError("That request ID belongs to a different order.")
        return dict(prior)

    if book == LIVE_BOOK:
        submitted = stamp(datetime.now(UTC))
        fence, = connection.execute("SELECT COALESCE(MAX(id), 0) FROM observations").fetchone()
    else:
        observation = _observation(connection, cursor, symbol)
        if observation is None:
            raise ValueError("Start replay before placing a paper order.")
        submitted, fence = observation["received_at_utc"], observation["id"]

    account = ensure_account(connection, book, symbol, cursor)
    if action == "close":
        quantity = account["shares"]
    side = "buy" if action == "buy" else "sell"
    reason = None
    if action == "close" and not quantity:
        reason = "There is no position to close."
    elif type(quantity) is not int or not 1 <= quantity <= 2**53 - 1:
        reason = "Enter a positive whole number of shares."
        quantity = None
    elif side == "sell" and quantity > account["shares"]:
        reason = "The account has fewer shares than requested."

    order = connection.execute(
        """INSERT INTO paper_orders
           (account_id, request_id, symbol, action, side, requested_quantity, quantity,
            submitted_at, after_id, status, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *""",
        (book, request_id, symbol, action, side, requested, quantity,
         submitted, fence, "rejected" if reason else "pending", reason),
    ).fetchone()
    return dict(order)


def _price(value):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _pnl(connection, account, event, key, event_time, price_time, price):
    realized = Decimal(account["realized"])
    unrealized = account["shares"] * price - Decimal(account["cost_basis"])
    connection.execute(
        """INSERT INTO paper_pnl (account_id, symbol, event, event_key, event_time, price_time,
           price, cash, shares, realized, unrealized, total) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id, event, event_key) DO NOTHING""",
        (account["id"], account["symbol"], event, str(key), event_time, price_time, str(price),
         account["cash"], account["shares"], str(realized), str(unrealized), str(realized + unrealized)),
    )


def _mark_candles(connection, account, candles):
    if not account["shares"]:
        return
    last = connection.execute(
        "SELECT event_time FROM paper_pnl WHERE account_id = ? ORDER BY id DESC LIMIT 1", (account["id"],)
    ).fetchone()
    for row in candles:
        candle = row["candle"]
        ended_at = stamp(datetime.fromisoformat(candle["timestamp"]) + timedelta(minutes=1))
        if row["ended"] and (not last or ended_at > last["event_time"]):
            price = _price(candle["close"])
            if price is not None:
                _pnl(connection, account, "Candle close", candle["timestamp"],
                     ended_at, ended_at, price)


def has_automation(connection):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'paper_automation_jobs'"
    ).fetchone() is not None


def _linked_job(connection, order_id):
    if has_automation(connection):
        return connection.execute(
            """SELECT j.*, r.enabled FROM paper_automation_jobs j
               JOIN paper_automation_runs r ON r.id = j.run_id WHERE j.order_id = ?""", (order_id,),
        ).fetchone()
    return None


def _fill(connection, account, order, observation, price):
    job = _linked_job(connection, order["id"])
    if job:
        if job["status"] != "pending":
            return
        cutoff = datetime.fromisoformat(job["cutoff"])
        market_time = datetime.fromtimestamp(observation["source_time_ms"] / 1000, UTC)
        clock = (datetime.now(UTC) if account["id"] == LIVE_BOOK
                 else datetime.fromisoformat(observation["received_at_utc"]))
        reason = None
        if not job["enabled"]:
            reason = "Paper automation stopped before the fill."
        elif clock - cutoff > MAX_DECISION_AGE:
            reason = "The decision expired before the fill."
        elif market_time < cutoff:
            reason = "The next receipt carried a market price from before the decision cutoff."
        if reason:
            _reject_fill(connection, order["id"], job, reason)
            return
    quantity = order["quantity"]
    cash = Decimal(account["cash"])
    basis = Decimal(account["cost_basis"])
    realized = Decimal(account["realized"])
    shares = account["shares"]
    value = quantity * price
    reason = None
    if order["side"] == "buy":
        if cash < value:
            reason = "Insufficient cash at the observed price."
        else:
            cash -= value
            shares += quantity
            basis += value
    elif shares < quantity or (order["action"] == "close" and shares != quantity):
        reason = "The position changed before the order could fill."
    else:
        disposed_cost = basis if shares == quantity else basis * quantity / shares
        cash += value
        realized += value - disposed_cost
        shares -= quantity
        basis -= disposed_cost

    order_id = order["id"]
    if reason:
        _reject_fill(connection, order_id, job, reason)
        return
    executed_at = observation["received_at_utc"] if account["id"] != LIVE_BOOK else stamp(datetime.now(UTC))
    connection.execute(
        "INSERT INTO paper_fills VALUES (?, ?, ?, ?)", (order_id, observation["id"], str(price), executed_at)
    )
    connection.execute("UPDATE paper_orders SET status = 'filled' WHERE id = ?", (order_id,))
    connection.execute(
        "UPDATE paper_accounts SET cash = ?, shares = ?, cost_basis = ?, realized = ? WHERE id = ?",
        (str(cash), shares, str(basis), str(realized), account["id"]),
    )
    price_time = stamp(datetime.fromtimestamp(observation["source_time_ms"] / 1000, UTC))
    _pnl(connection, _account(connection, account["id"]), "Fill", order_id,
         observation["received_at_utc"], price_time, price)
    if job:
        connection.execute("UPDATE paper_automation_jobs SET status = 'filled' WHERE id = ?", (job["id"],))


def _reject_fill(connection, order_id, job, reason):
    connection.execute("UPDATE paper_orders SET status = 'rejected', reason = ? WHERE id = ?", (reason, order_id))
    if job:
        connection.execute(
            "UPDATE paper_automation_jobs SET status = 'skipped', reason = ? WHERE id = ?", (reason, job["id"]),
        )


def advance_replay_account(db_path, book, symbol, previous_id, observation_id, candles):
    with transaction(db_path) as connection:
        if not _has_accounts(connection):
            raise ValueError("Place a paper order before advancing this account.")
        account = _account(connection, book)
        if account is None or account["symbol"] != symbol:
            raise ValueError("No paper account for this replay.")
        if account["cursor_id"] == observation_id:
            return
        if account["cursor_id"] != previous_id:
            raise ValueError("The paper account is at a different replay observation.")
        observation = _observation(connection, observation_id, symbol)
        if observation is None:
            raise ValueError("Replay observation is no longer available.")
        _mark_candles(connection, account, candles)
        price = _price(observation["price"])
        if price is not None:
            orders = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND status = 'pending' ORDER BY id", (book,)
            ).fetchall()
            for order in orders:
                _fill(connection, _account(connection, book), order, observation, price)
        connection.execute("UPDATE paper_accounts SET cursor_id = ? WHERE id = ?", (observation_id, book))


def _live_fills(connection, account):
    orders = connection.execute(
        "SELECT * FROM paper_orders WHERE account_id = ? AND status = 'pending' ORDER BY id", (LIVE_BOOK,)
    ).fetchall()
    matches = []
    for order in orders:
        rows = connection.execute(
            """SELECT id, source_time_ms, received_at_utc, source_message_json ->> '$.price' AS price
               FROM observations WHERE source = ? AND ticker = ? AND id > ? AND received_at_utc > ?
               ORDER BY received_at_utc, id""",
            (SOURCE, order["symbol"], order["after_id"], order["submitted_at"]),
        )
        for row in rows:
            if (price := _price(row["price"])) is not None:
                matches.append((row["received_at_utc"], row["id"], order["id"], order, row, price))
                break
    return sorted(matches)


def _live_candles(db_path, symbol, cutoff, at_id=None):
    options = {"as_of": cutoff}
    if at_id is not None:
        options["at_id"] = at_id
    minutes, _ = load_observed_minutes(db_path, symbol, **options)
    return aggregate_candles(minutes, "1m", cutoff)


def process_live_account(db_path):
    """Catch up pending orders after a restart and mark completed one-minute candles."""
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
        if not _has_accounts(reader):
            return
        working = reader.execute(
            """SELECT 1 FROM paper_accounts AS a WHERE id = ? AND
               (shares > 0 OR EXISTS (SELECT 1 FROM paper_orders WHERE account_id = a.id AND status = 'pending'))""",
            (LIVE_BOOK,),
        ).fetchone()
        if not working:
            return
    with transaction(db_path) as connection:
        for receipt, row_id, _, order, observation, price in _live_fills(connection, _account(connection, LIVE_BOOK)):
            account = _account(connection, LIVE_BOOK)
            if account["shares"]:
                candles = _live_candles(db_path, account["symbol"], datetime.fromisoformat(receipt), row_id)
                _mark_candles(connection, account, candles)
            _fill(connection, account, order, observation, price)
        account = _account(connection, LIVE_BOOK)
        if account["shares"]:
            _mark_candles(connection, account, _live_candles(db_path, account["symbol"], datetime.now(UTC)))


def paper_state(db_path, book, symbol):
    account = {"id": book, "symbol": symbol, "cash": STARTING_CASH, "shares": 0}
    orders, pnl, books = [], [], []
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        if _has_accounts(connection):
            stored = _account(connection, book)
            if stored:
                account = dict(stored)
                orders = [dict(row) for row in connection.execute(
                    """SELECT o.*, f.price, f.observation_id, f.executed_at, m.source_time_ms
                       FROM paper_orders o LEFT JOIN paper_fills f ON f.order_id = o.id
                       LEFT JOIN observations m ON m.id = f.observation_id
                       WHERE o.account_id = ? ORDER BY o.id DESC""", (book,),
                )]
                pnl = [dict(row) for row in connection.execute(
                    "SELECT * FROM paper_pnl WHERE account_id = ? ORDER BY id DESC", (book,),
                )]
            books = [dict(row) for row in connection.execute(
                """SELECT a.id, a.symbol, MIN(o.submitted_at) AS first_order
                   FROM paper_accounts a JOIN paper_orders o ON a.id = o.account_id
                   WHERE a.id != ? GROUP BY a.id ORDER BY MIN(o.id) DESC""", (LIVE_BOOK,),
            )]
    return {"account": account, "orders": orders, "pnl": pnl, "replays": books}
