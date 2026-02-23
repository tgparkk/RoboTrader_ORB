"""PG 데이터 범위 확인"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2

conn = psycopg2.connect(host='127.0.0.1', port=5433, dbname='robotrader_orb', user='postgres')
cur = conn.cursor()

# minute_candles
cur.execute("SELECT MIN(candle_date), MAX(candle_date), COUNT(DISTINCT candle_date), COUNT(*), COUNT(DISTINCT stock_code) FROM minute_candles")
row = cur.fetchone()
print(f"=== minute_candles ===")
print(f"  기간: {row[0]} ~ {row[1]}")
print(f"  거래일수: {row[2]}일, 총 {row[3]:,}건, 종목수: {row[4]}개")

# 월별 분포
cur.execute("""
    SELECT TO_CHAR(candle_date, 'YYYY-MM') as month,
           COUNT(DISTINCT candle_date) as days,
           COUNT(DISTINCT stock_code) as stocks,
           COUNT(*) as records
    FROM minute_candles GROUP BY 1 ORDER BY 1
""")
print(f"\n  월별: ", end="")
for row in cur.fetchall():
    print(f"{row[0]}({row[1]}일/{row[2]}종목/{row[3]:,}건) ", end="")
print()

# daily_candles
cur.execute("SELECT MIN(candle_date), MAX(candle_date), COUNT(DISTINCT candle_date), COUNT(*), COUNT(DISTINCT stock_code) FROM daily_candles")
row = cur.fetchone()
print(f"\n=== daily_candles ===")
print(f"  기간: {row[0]} ~ {row[1]}")
print(f"  거래일수: {row[2]}일, 총 {row[3]:,}건, 종목수: {row[4]}개")

# cache files
from pathlib import Path
dates = set()
for f in Path('cache/minute_data').glob('*.pkl'):
    parts = f.stem.split('_')
    if len(parts) >= 2:
        dates.add(parts[-1])
sorted_dates = sorted(dates)
print(f"\n=== cache/minute_data ===")
print(f"  기간: {sorted_dates[0]} ~ {sorted_dates[-1]}")
print(f"  거래일수: {len(sorted_dates)}일")

conn.close()
