import sqlite3
import pandas as pd
import os

db_path = r'data\robotrader.db'

if not os.path.exists(db_path):
    print(f"DB file not found at: {db_path}")
else:
    try:
        conn = sqlite3.connect(db_path)
        # Check today's candidates (assuming format 'YYYYMMDD' or 'YYYY-MM-DD')
        # Trying '20260114' first as per common convention
        query = "SELECT * FROM candidate_stocks WHERE selection_date LIKE '2026-01-14%'"
        df = pd.read_sql(query, conn)
        
        if df.empty:
            print("No candidates found for 2026-01-14.")
            # Check recent dates just in case
            print("\nMost recent entries:")
            print(pd.read_sql("SELECT selection_date, count(*) as count FROM candidate_stocks GROUP BY selection_date ORDER BY selection_date DESC LIMIT 3", conn))
        else:
            print(f"Candidates for 2026-01-14 ({len(df)} items):")
            print(df.to_string())
            
        conn.close()
    except Exception as e:
        print(f"Error reading DB: {e}")
