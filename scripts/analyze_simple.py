import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

DB_CONN_PARAMS = dict(host='172.23.208.1', port=5433, dbname='robotrader_orb', user='postgres')
conn = psycopg2.connect(**DB_CONN_PARAMS)
cursor = conn.cursor()

print("="*80)
print("2026-02-13 (FRI) Trading Analysis")
print("="*80)

# Candidates
cursor.execute("SELECT COUNT(*) FROM candidate_stocks WHERE date = '2026-02-13'")
candidate_count = cursor.fetchone()[0]
print(f"\n[1] Candidates: {candidate_count}")

# Trades
cursor.execute("""
    SELECT stock_code, stock_name, action, quantity, price,
           (timestamp AT TIME ZONE 'Asia/Seoul')::text as local_time
    FROM virtual_trading_records
    WHERE DATE(timestamp AT TIME ZONE 'Asia/Seoul') = '2026-02-13'
    ORDER BY timestamp
""")
trades = cursor.fetchall()

print(f"\n[2] Trades: {len(trades)}")
print("-"*80)

buy_trades = {}
sell_trades = []

for trade in trades:
    stock_code, stock_name, action, qty, price, time = trade
    print(f"{time} | {action:4s} | {stock_name}({stock_code}) | {qty:3d}@{price:,}")

    if action == 'BUY':
        buy_trades[stock_code] = {'name': stock_name, 'qty': qty, 'price': price, 'time': time}
    else:
        sell_trades.append({'code': stock_code, 'name': stock_name, 'qty': qty, 'price': price, 'time': time})

# P&L
print(f"\n[3] P&L Analysis")
print("-"*80)

total_pnl = 0
wins = 0
losses = 0

for sell in sell_trades:
    if sell['code'] in buy_trades:
        buy = buy_trades[sell['code']]
        pnl = (sell['price'] - buy['price']) * sell['qty']
        pnl_pct = ((sell['price'] / buy['price']) - 1) * 100
        total_pnl += pnl

        if pnl > 0:
            wins += 1
            status = "WIN"
        else:
            losses += 1
            status = "LOSS"

        print(f"{status:4s} | {sell['name']}({sell['code']})")
        print(f"  BUY:  {buy['qty']:3d}@{buy['price']:>8,} ({buy['time']})")
        print(f"  SELL: {sell['qty']:3d}@{sell['price']:>8,} ({sell['time']})")
        print(f"  P&L: {pnl:+10,} ({pnl_pct:+6.2f}%)")
        print()

# Summary
print("="*80)
print("[4] Summary")
print("="*80)
print(f"Buys:  {len(buy_trades)}")
print(f"Sells: {len(sell_trades)}")
print(f"Wins:  {wins}")
print(f"Losses: {losses}")
if len(sell_trades) > 0:
    win_rate = (wins / len(sell_trades)) * 100
    print(f"Win Rate: {win_rate:.1f}%")
print(f"Total P&L: {total_pnl:+,}")
print("="*80)

conn.close()
