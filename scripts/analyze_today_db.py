import sqlite3
import pandas as pd
import os

db_path = r'data\robotrader.db'

def inspect_db():
    if not os.path.exists(db_path):
        print(f"DB file not found at: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. List Tables
        print("=== Tables in DB ===")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        for table in tables:
            print(table[0])
            
        print("\n=== Today's Candidates (2026-01-27) ===")
        try:
            query = "SELECT * FROM candidate_stocks WHERE selection_date = '2026-01-27'"
            df = pd.read_sql(query, conn)
            if not df.empty:
                print(df.to_string())
            else:
                print("No candidates found for 2026-01-27")
        except Exception as e:
            print(f"Error querying candidate_stocks: {e}")

        # Check for trades table (guessing name, will adjust if needed based on table list)
        trade_tables = [t[0] for t in tables if 'trade' in t[0] or 'order' in t[0]]
        for t_name in trade_tables:
            print(f"\n=== Data in {t_name} for 2026-01-27 ===")
            try:
                # Try to find a date column
                cursor.execute(f"PRAGMA table_info({t_name})")
                columns = [info[1] for info in cursor.fetchall()]
                date_col = next((c for c in columns if 'date' in c or 'time' in c), None)

                if date_col:
                    query = f"SELECT * FROM {t_name} WHERE {date_col} LIKE '2026-01-27%'"
                    df = pd.read_sql(query, conn)
                    if not df.empty:
                        print(df.head().to_string())
                        print(f"... total {len(df)} rows")
                    else:
                        print(f"No rows for today in {t_name}")
                else:
                    print(f"Could not identify date column in {t_name}. Columns: {columns}")
                    print("Sample data:")
                    print(pd.read_sql(f"SELECT * FROM {t_name} LIMIT 5", conn).to_string())

            except Exception as e:
                print(f"Error querying {t_name}: {e}")

        conn.close()
    except Exception as e:
        print(f"Error accessing DB: {e}")

if __name__ == "__main__":
    inspect_db()
