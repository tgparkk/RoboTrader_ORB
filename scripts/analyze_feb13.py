import sqlite3

db_path = "data/robotrader.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("="*80)
print("2026-02-13 (FRI) Trading Analysis")
print("="*80)

# Candidates
cursor.execute("SELECT COUNT(*) FROM candidate_stocks WHERE DATE(selection_date) = '2026-02-13'")
candidate_count = cursor.fetchone()[0]
print(f"\n[1] Candidates Selected: {candidate_count}")

if candidate_count > 0:
    cursor.execute("""
        SELECT stock_code, stock_name, score, reasons
        FROM candidate_stocks
        WHERE DATE(selection_date) = '2026-02-13'
        LIMIT 10
    """)
    print("\nTop 10 Candidates:")
    for row in cursor.fetchall():
        code, name, score, reasons = row
        print(f"  - {name}({code}): score={score}")

# All Trades
cursor.execute("""
    SELECT stock_code, stock_name, action, quantity, price,
           datetime(timestamp, 'localtime') as local_time,
           profit_loss, profit_rate
    FROM virtual_trading_records
    WHERE DATE(timestamp, 'localtime') = '2026-02-13'
    ORDER BY timestamp
""")
trades = cursor.fetchall()

print(f"\n[2] Total Trades: {len(trades)}")
print("-"*80)

buy_count = 0
sell_count = 0
total_pnl = 0

for trade in trades:
    code, name, action, qty, price, time, pnl, pnl_rate = trade
    print(f"{time} | {action:4s} | {name:12s}({code}) | {qty:3d}@{price:>8,.0f}", end="")

    if action == 'BUY':
        buy_count += 1
        print()
    else:
        sell_count += 1
        if pnl is not None:
            print(f" | P&L: {pnl:>+10,.0f} ({pnl_rate:+6.2f}%)")
            total_pnl += pnl
        else:
            print()

# Sell-only summary
cursor.execute("""
    SELECT COUNT(*), SUM(profit_loss), AVG(profit_loss), AVG(profit_rate)
    FROM virtual_trading_records
    WHERE DATE(timestamp, 'localtime') = '2026-02-13'
      AND action = 'SELL'
      AND profit_loss IS NOT NULL
""")
sell_stats = cursor.fetchone()

cursor.execute("""
    SELECT COUNT(*)
    FROM virtual_trading_records
    WHERE DATE(timestamp, 'localtime') = '2026-02-13'
      AND action = 'SELL'
      AND profit_loss > 0
""")
win_count = cursor.fetchone()[0]

cursor.execute("""
    SELECT COUNT(*)
    FROM virtual_trading_records
    WHERE DATE(timestamp, 'localtime') = '2026-02-13'
      AND action = 'SELL'
      AND profit_loss < 0
""")
loss_count = cursor.fetchone()[0]

# Summary
print("\n" + "="*80)
print("[3] Summary")
print("="*80)
print(f"Total Buys:  {buy_count}")
print(f"Total Sells: {sell_count}")

if sell_stats[0] > 0:
    print(f"  - Wins:  {win_count}")
    print(f"  - Losses: {loss_count}")
    win_rate = (win_count / sell_stats[0]) * 100 if sell_stats[0] > 0 else 0
    print(f"  - Win Rate: {win_rate:.1f}%")
    print(f"Total P&L: {sell_stats[1]:+,.0f}")
    print(f"Avg P&L per trade: {sell_stats[2]:+,.0f}")
    print(f"Avg Return: {sell_stats[3]:+.2f}%")

print("="*80)

conn.close()
