"""
2026-02-13 금요일 매매 분석 스크립트
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from datetime import datetime

# PostgreSQL 접속
DB_CONN_PARAMS = dict(host='172.23.208.1', port=5433, dbname='robotrader_orb', user='postgres')
conn = psycopg2.connect(**DB_CONN_PARAMS)
cursor = conn.cursor()

print("=" * 80)
print("📊 2026-02-13 금요일 매매 분석")
print("=" * 80)

# 1. 후보 종목 수
cursor.execute("SELECT COUNT(*) FROM candidate_stocks WHERE date = '2026-02-13'")
candidate_count = cursor.fetchone()[0]
print(f"\n1️⃣ 후보 종목 선정: {candidate_count}개")

# 2. 가상 거래 기록
cursor.execute("""
    SELECT stock_code, stock_name, action, quantity, price,
           (timestamp AT TIME ZONE 'Asia/Seoul')::text as local_time
    FROM virtual_trading_records
    WHERE DATE(timestamp AT TIME ZONE 'Asia/Seoul') = '2026-02-13'
    ORDER BY timestamp
""")
trades = cursor.fetchall()

print(f"\n2️⃣ 가상 거래 내역: 총 {len(trades)}건")
print("-" * 80)

buy_trades = []
sell_trades = []

for trade in trades:
    stock_code, stock_name, action, qty, price, time = trade
    print(f"{time} | {action:4s} | {stock_name}({stock_code}) | {qty:3d}주 @ {price:,}원")

    if action == 'BUY':
        buy_trades.append((stock_code, stock_name, qty, price, time))
    else:
        sell_trades.append((stock_code, stock_name, qty, price, time))

# 3. 손익 계산
print(f"\n3️⃣ 손익 분석")
print("-" * 80)

total_profit = 0
win_count = 0
loss_count = 0

for sell_code, sell_name, sell_qty, sell_price, sell_time in sell_trades:
    for buy_code, buy_name, buy_qty, buy_price, buy_time in buy_trades:
        if buy_code == sell_code:
            profit = (sell_price - buy_price) * sell_qty
            profit_rate = ((sell_price / buy_price) - 1) * 100
            total_profit += profit

            if profit > 0:
                win_count += 1
                status = "✅ 익절"
            else:
                loss_count += 1
                status = "❌ 손절"

            print(f"{status} | {sell_name}({sell_code})")
            print(f"  매수: {buy_qty}주 @ {buy_price:,}원 ({buy_time})")
            print(f"  매도: {sell_qty}주 @ {sell_price:,}원 ({sell_time})")
            print(f"  손익: {profit:+,}원 ({profit_rate:+.2f}%)")
            print()
            break

# 4. 종합 결과
print("=" * 80)
print(f"📈 종합 결과")
print("=" * 80)
print(f"총 매수: {len(buy_trades)}건")
print(f"총 매도: {len(sell_trades)}건")
print(f"승리: {win_count}건")
print(f"패배: {loss_count}건")
if len(sell_trades) > 0:
    win_rate = (win_count / len(sell_trades)) * 100
    print(f"승률: {win_rate:.1f}%")
print(f"총 손익: {total_profit:+,}원")
print("=" * 80)

conn.close()
