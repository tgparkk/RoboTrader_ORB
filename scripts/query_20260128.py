"""2026-01-28 DB 조회: 후보 종목, 가상매매 기록."""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "..", "data", "robotrader.db")
db_path = os.path.normpath(db_path)

if not os.path.exists(db_path):
    print("DB not found:", db_path)
    exit(1)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("Tables:", [r[0] for r in cur.fetchall()])

cur.execute(
    "SELECT selection_date, COUNT(*) FROM candidate_stocks "
    "GROUP BY selection_date ORDER BY selection_date DESC LIMIT 5"
)
print("candidate_stocks by date:", cur.fetchall())

cur.execute(
    "SELECT stock_code, stock_name FROM candidate_stocks "
    "WHERE selection_date LIKE '2026-01-28%' LIMIT 15"
)
print("2026-01-28 candidates sample:", cur.fetchall())

cur.execute("SELECT COUNT(*) FROM candidate_stocks WHERE selection_date LIKE '2026-01-28%'")
print("2026-01-28 candidates count:", cur.fetchone()[0])

try:
    cur.execute("SELECT COUNT(*) FROM virtual_trading_records")
    n = cur.fetchone()[0]
    print("virtual_trading_records count:", n)
    cur.execute(
        "SELECT id, stock_code, action, quantity, price, timestamp FROM virtual_trading_records "
        "WHERE timestamp LIKE '2026-01-28%' ORDER BY timestamp LIMIT 30"
    )
    rows = cur.fetchall()
    print("2026-01-28 virtual_trading_records:", len(rows), "rows")
    for r in rows[:15]:
        print(" ", r)
    cur.execute("SELECT COUNT(*) FROM virtual_trading_records WHERE timestamp LIKE '2026-01-28%'")
    print("2026-01-28 virtual total:", cur.fetchone()[0])
except Exception as e:
    print("virtual_trading_records error:", e)

conn.close()
