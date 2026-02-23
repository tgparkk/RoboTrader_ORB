"""미청산 가상 포지션 확인 스크립트"""
import sqlite3
import glob
import os

# Find all .db files
for db_path in glob.glob(os.path.join(os.path.dirname(__file__), '..', '**', '*.db'), recursive=True):
    print(f"\nDB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cur.fetchall()]
    print(f"  Tables: {tables}")

    if 'virtual_trading_records' in tables:
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
        print(f"\n  미청산 BUY 레코드: {len(rows)}건")
        for r in rows:
            print(f"    ID={r[0]} {r[1]}({r[2]}) {r[4]}주 @{r[3]:,.0f}원  매수={r[5]}")
    conn.close()
