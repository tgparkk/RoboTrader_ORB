import psycopg2
import pandas as pd

DB_CONN_PARAMS = dict(host='172.23.208.1', port=5433, dbname='robotrader_orb', user='postgres')


def inspect_db():
    try:
        conn = psycopg2.connect(**DB_CONN_PARAMS)
        cursor = conn.cursor()

        # 1. List Tables
        print("=== Tables in DB ===")
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' ORDER BY table_name;
        """)
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

        # Check for trades table
        trade_tables = [t[0] for t in tables if 'trade' in t[0] or 'order' in t[0]]
        for t_name in trade_tables:
            print(f"\n=== Data in {t_name} for 2026-01-27 ===")
            try:
                # Find date column
                cursor.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = %s
                """, (t_name,))
                columns = [info[0] for info in cursor.fetchall()]
                date_col = next((c for c in columns if 'date' in c or 'time' in c), None)

                if date_col:
                    query = f"SELECT * FROM {t_name} WHERE CAST({date_col} AS TEXT) LIKE '2026-01-27%%'"
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
