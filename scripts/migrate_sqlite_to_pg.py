"""
SQLite → PostgreSQL 마이그레이션 스크립트
기존 robotrader.db 데이터를 robotrader_orb PostgreSQL DB로 이관
"""
import sqlite3
import psycopg2
import psycopg2.extras
import sys
from pathlib import Path

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SQLITE_PATH = PROJECT_ROOT / "data" / "robotrader.db"

PG_CONFIG = {
    'host': '127.0.0.1',
    'port': 5433,
    'dbname': 'robotrader_orb',
    'user': 'postgres',
    'password': '',
}

TABLES = [
    'candidate_stocks',
    'stock_prices',
    'trading_records',
    'virtual_trading_records',
    'real_trading_records',
]


def migrate():
    if not SQLITE_PATH.exists():
        print(f"SQLite DB not found: {SQLITE_PATH}")
        return

    sq = sqlite3.connect(str(SQLITE_PATH))
    sq.row_factory = sqlite3.Row
    pg = psycopg2.connect(**PG_CONFIG)

    try:
        for table in TABLES:
            print(f"\n--- Migrating {table} ---")
            rows = sq.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"  (empty)")
                continue

            cols = [desc[0] for desc in sq.execute(f"SELECT * FROM {table} LIMIT 1").description]
            # id 컬럼 제외 (SERIAL 자동 생성)
            cols_no_id = [c for c in cols if c != 'id']

            placeholders = ', '.join(['%s'] * len(cols_no_id))
            col_names = ', '.join(cols_no_id)
            insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

            cur = pg.cursor()
            batch = []
            for row in rows:
                values = tuple(row[c] for c in cols_no_id)
                batch.append(values)

            psycopg2.extras.execute_batch(cur, insert_sql, batch, page_size=500)
            pg.commit()

            # SERIAL 시퀀스를 max(id)+1 로 리셋
            max_id_row = sq.execute(f"SELECT MAX(id) FROM {table}").fetchone()
            max_id = max_id_row[0] if max_id_row and max_id_row[0] else 0
            if max_id > 0:
                cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), %s)", (max_id,))
                pg.commit()

            print(f"  {len(batch)} rows migrated (seq reset to {max_id})")

        print("\n✅ Migration complete!")

    except Exception as e:
        pg.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        sq.close()
        pg.close()


if __name__ == '__main__':
    migrate()
