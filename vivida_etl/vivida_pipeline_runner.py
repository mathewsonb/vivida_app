"""
vivida_pipeline_runner.py
Aligned Standalone ETL & Supabase Ingestion Module.
"""

import os
import sys
import json
import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
from supabase import create_client, Client

# use relative import within package
from .vivida_etl_helpers import (
    get_census_coordinates,
    clean_str,
    generate_event_id,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("vivida_etl")


class VividaIngestionPipeline:
    def __init__(self, table_name: str = "events"):
        self.table_name = table_name
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

        if not self.supabase_url or not self.supabase_key:
            raise ValueError(
                "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variables."
            )

        self.client: Client = create_client(self.supabase_url, self.supabase_key)

    def sanitize_urls(self, url_str: Any) -> Optional[str]:
        if not url_str or pd.isna(url_str) or str(url_str).strip() in ["", "NULL", "none"]:
            return None
        clean_url = str(url_str).strip()
        if clean_url.startswith("http:"):
            return clean_url.replace("http:", "https:", 1)
        return clean_url

    def sanitize_mood_score(self, val: Any) -> float:
        """Ensures mood scores strictly adhere to NUMERIC(3,2) BETWEEN 0 AND 1."""
        try:
            if pd.isna(val) or val is None:
                return 0.50
            float_val = float(val)
            bounded_val = max(0.00, min(1.00, float_val))
            return round(bounded_val, 2)
        except (ValueError, TypeError):
            return 0.50

    def transform_raw_events(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """
        Transforms raw event DataFrames to match target PostgreSQL schema.
        """
        if raw_df.empty:
            logger.warning("Empty DataFrame passed to transformation step.")
            return pd.DataFrame()

        df = raw_df.copy()

        # 1. Filter out invalid rows missing start times or titles
        df = df[df["start_time"].notnull() & (df["title"].str.strip() != "")].reset_index(drop=True)
        if df.empty:
            logger.warning("No valid events remaining after filtering empty start_times/titles.")
            return pd.DataFrame()

        # 2. Sanitize links (Prevent Mixed Content on GitHub Pages)
        for url_col in ["image_url", "ticket_link", "external_url"]:
            if url_col in df.columns:
                df[url_col] = df[url_col].apply(self.sanitize_urls)
            else:
                df[url_col] = None

        # 3. Handle Geocoding
        if "address" in df.columns:
            unique_addrs = [addr for addr in df["address"].dropna().unique() if str(addr).strip()]
            coords_map = {addr: get_census_coordinates(addr) for addr in unique_addrs}
            df["latitude"] = df["address"].map(lambda a: coords_map.get(a, (None, None))[0] if a else None)
            df["longitude"] = df["address"].map(lambda a: coords_map.get(a, (None, None))[1] if a else None)
        else:
            df["address"] = None
            df["latitude"] = None
            df["longitude"] = None

        # 4. Consolidate Schedules & Aggregate Groupings into Exact PostgreSQL Schema
        group_cols = ["title", "venue_name"]
        
        def process_group(group):
            first = group.iloc[0]
            
            # Build deduped schedule string (TEXT column)
            deduped_schedules = group.drop_duplicates(subset=["start_time", "external_url"])
            schedule_list = [
                {
                    "start_time": str(row["start_time"]),
                    "register": row["external_url"]
                }
                for _, row in deduped_schedules.iterrows()
            ]

            # Generate unique deterministic 24-char primary key
            event_id = generate_event_id(
                title=first["title"],
                venue_name=first["venue_name"],
                start_time=group["start_time"].tolist(),
                address=first.get("address", "")
            )

            # Determine free event status
            is_free_val = False
            if "is_free" in first and not pd.isna(first["is_free"]):
                is_free_val = bool(first["is_free"])
            else:
                desc_text = str(first.get("description", "")).lower()
                is_free_val = any(w in desc_text for w in ["free", "no cost", "admission free", "$0"])

            return pd.Series({
                "id": event_id,
                "title": str(first["title"]).strip(),
                "venue_name": str(first["venue_name"]).strip(),
                "address": first.get("address"),
                "latitude": first.get("latitude"),
                "longitude": first.get("longitude"),
                "description": first.get("description"),
                "is_free": is_free_val,
                "schedule": json.dumps(schedule_list),
                "energy": self.sanitize_mood_score(first.get("energy")),
                "social": self.sanitize_mood_score(first.get("social")),
                "novelty": self.sanitize_mood_score(first.get("novelty")),
                "age_restriction": first.get("age_restriction"),
                "indoor_outdoor": first.get("indoor_outdoor"),
                "external_url": first.get("external_url"),
                "image_url": first.get("image_url"),
                "genre": first.get("genre"),
                "ticket_link": first.get("ticket_link"),
                "category": first.get("category")
            })

        transformed_df = df.groupby(group_cols, dropna=False).apply(process_group).reset_index(drop=True)
        return transformed_df

    def upsert_to_supabase(self, transformed_df: pd.DataFrame, batch_size: int = 100) -> int:
        if transformed_df.empty:
            logger.info("No records to upsert.")
            return 0

        # Replace NaN with None so JSON serialization passes null to Postgres
        records = transformed_df.replace({np.nan: None}).to_dict(orient="records")
        total_records = len(records)
        logger.info(f"Preparing to upsert {total_records} records into '{self.table_name}'...")

        upserted_count = 0
        for i in range(0, total_records, batch_size):
            batch = records[i : i + batch_size]
            try:
                response = self.client.table(self.table_name).upsert(
                    batch, 
                    on_conflict="id"
                ).execute()
                upserted_count += len(batch)
                logger.info(f"Upserted batch {i // batch_size + 1} ({len(batch)} records).")
            except Exception as e:
                logger.error(f"Failed to upsert batch starting at index {i}: {e}")

        logger.info(f"Pipeline complete. Total records synchronized: {upserted_count}/{total_records}")
        return upserted_count

    def run_standalone_pipeline(self, raw_df: pd.DataFrame) -> bool:
        try:
            logger.info("Starting standalone VM ETL Pipeline execution...")
            transformed_df = self.transform_raw_events(raw_df)
            if not transformed_df.empty:
                self.upsert_to_supabase(transformed_df)
                return True
            else:
                logger.warning("Pipeline finished with zero records transformed.")
                return False
        except Exception as e:
            logger.critical(f"Standalone pipeline execution failed: {e}", exc_info=True)
            return False