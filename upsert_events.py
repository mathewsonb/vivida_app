import os
import sys
import json
import pandas as pd
from supabase import create_client, Client

# 1. Environment variables supplied via GitHub Secrets
SUPABASE_URL = os.environ.get("VITE_SUPABASE_URL")
SUPABASE_KEY = os.environ.get("VITE_SUPABASE_ANON_KEY") # Use Service Role Key for write permissions

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY environment variables.")
    sys.exit(1)

# Initialize Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

CSV_PATH = os.path.join("dev", "events.csv")

def run_upsert():
    if not os.path.exists(CSV_PATH):
        print(f"Error: CSV file not found at {CSV_PATH}")
        sys.exit(1)

    print(f"Loading data from {CSV_PATH}...")
    df = pd.read_csv(CSV_PATH)

    if df.empty:
        print("CSV is empty. Nothing to upsert.")
        return

    # Clean up array/list columns (e.g. start_time stored as a string representation of a list)
    if "start_time" in df.columns:
        def parse_start_time(val):
            if pd.isna(val) or not val:
                return []
            if isinstance(val, str):
                try:
                    return json.loads(val.replace("'", '"'))
                except Exception:
                    return [val]
            return val

        df["start_time"] = df["start_time"].apply(parse_start_time)

    # Replace NaN/NaT values with None for clean JSON serialization
    records = df.where(pd.notnull(df), None).to_dict(orient="records")

    print(f"Preparing to upsert {len(records)} events into Supabase 'events' table...")

    # Upsert in batches of 100
    batch_size = 100
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try:
            # 'on_conflict' matches your deterministic primary key column name
            response = supabase.table("events").upsert(batch, on_conflict="id").execute()
            print(f"Upserted batch {i//batch_size + 1} ({len(batch)} records)")
        except Exception as e:
            print(f"Error upserting batch starting at index {i}: {e}")
            sys.exit(1)

    print("Successfully completed Supabase upsert pipeline!")

if __name__ == "__main__":
    run_upsert()