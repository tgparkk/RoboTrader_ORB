"""미청산 가상 포지션 전량 매수가 기준 매도 처리 스크립트"""
import sqlite3
from datetime import datetime

DB_PATH = "data/robotrader.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 미청산 BUY 레코드 조회
cur.execute("""
    SELECT b.id, b.stock_code, b.stock_name, b.price, b.quantity, b.timestamp
    FROM virtual_trading_records b
    WHERE b.action='BUY' AND b.is_test=1
      AND NOT EXISTS (
        SELECT 1 FROM virtual_trading_records s
        WHERE s.action='SELL' AND s.buy_record_id=b.id
      )
    ORDER BY b.timestamp
""")
rows = cur.fetchall()
print(f"미청산 BUY 레코드: {len(rows)}건\n")

if not rows:
    print("청산할 포지션 없음")
    conn.close()
    exit()

now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
total_pnl = 0
closed = 0

for buy_id, code, name, buy_price, qty, buy_ts in rows:
    sell_price = float(buy_price)  # 매수가로 매도 (손익 0원 처리)
    pnl = 0.0
    pnl_rate = 0.0

    cur.execute("""
        INSERT INTO virtual_trading_records
        (stock_code, stock_name, action, price, quantity, timestamp,
         is_test, profit_loss, profit_rate, buy_record_id, reason)
        VALUES (?, ?, 'SELL', ?, ?, ?, 1, ?, ?, ?, ?)
    """, (
        code, name, sell_price, qty, now_str,
        pnl, pnl_rate, buy_id, "수동 일괄 청산 (미청산 정리)"
    ))

    total_pnl += pnl
    closed += 1
    print(f"  SELL {code}({name}) {qty}주 @{sell_price:,.0f}원 → 손익 {pnl:+,.0f}원")

conn.commit()
conn.close()

print(f"\n{'='*50}")
print(f"총 {closed}건 매도 처리 완료")
print(f"총 손익: {total_pnl:+,.0f}원 (전량 매수가 기준 청산)")
