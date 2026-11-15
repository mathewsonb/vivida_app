"""
vivida_pipeline_runner.py
Aligned Standalone ETL, LLM Enrichment, Zero-Shot Mood Classification & Supabase Ingestion Module.
"""

import concurrent.futures
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Literal, Optional

import numpy as np
import ollama
import pandas as pd
from pydantic import BaseModel, Field, model_validator
from supabase import Client, create_client
import torch
from transformers import pipeline

from .vivida_etl_helpers import (
    generate_event_id,
    get_census_coordinates,
    parse_event_dates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("vivida_etl")


# ==============================================================================
# 1. PREPROCESSOR SCHEMA & OLLAMA EXTRACTION
# ==============================================================================

CATEGORY_GENRE_MAP: Dict[str, List[str]] = {
    "Live Music & Concerts": [
        "Rock, Pop & Alternative",
        "Hip-Hop, R&B & Electronic",
        "Country, Folk & Americana",
        "Jazz, Blues & Soul",
        "Classical, Opera & Vocal",
        "General / Other",
    ],
    "Stage & Performing Arts": [
        "Theater & Musicals",
        "Dance & Ballet",
        "Magic & Variety",
        "General / Other",
    ],
    "Comedy & Live Entertainment": [
        "Stand-Up & Improv",
        "Magic & Variety",
        "General / Other",
    ],
    "Visual Arts & Exhibitions": [
        "Art Gallery & Museum",
        "Craft Fair & Maker Market",
        "Auto & Trade Show",
        "General / Other",
    ],
    "Festivals & Community Markets": [
        "Craft Fair & Maker Market",
        "Farmer's Market & Street Fair",
        "General / Other",
    ],
    "Film & Screenings": [
        "Movie & Screening",
        "General / Other",
    ],
    "Food & Beverage": [
        "Food & Beer Tasting",
        "Farmer's Market & Street Fair",
        "General / Other",
    ],
    "Sports & Active Recreation": [
        "Fitness, Run & Outdoor Activity",
        "General / Other",
    ],
    "Workshops & Learning": [
        "Lecture, Class & Workshop",
        "General / Other",
    ],
    "Social & Games": [
        "Trivia & Game Night",
        "General / Other",
    ],
    "Other": [
        "General / Other",
    ],
}


class CleanedEventDescription(BaseModel):
    clean_summary: str = Field(description="Under 200 word structured summary")
    is_free: bool = Field(description="True if explicitly free entry or $0")
    age_restriction: Literal["Family", "All Ages", "18+", "21+", "Unknown"]
    indoor_outdoor: Literal["Indoor", "Outdoor", "Mixed", "Unknown"]

    primary_category: Literal[
        "Live Music & Concerts",
        "Stage & Performing Arts",
        "Comedy & Live Entertainment",
        "Visual Arts & Exhibitions",
        "Festivals & Community Markets",
        "Film & Screenings",
        "Food & Beverage",
        "Sports & Active Recreation",
        "Workshops & Learning",
        "Social & Games",
        "Other",
    ]

    genre_or_subtype: Literal[
        "Rock, Pop & Alternative",
        "Hip-Hop, R&B & Electronic",
        "Country, Folk & Americana",
        "Jazz, Blues & Soul",
        "Classical, Opera & Vocal",
        "Theater & Musicals",
        "Dance & Ballet",
        "Stand-Up & Improv",
        "Magic & Variety",
        "Art Gallery & Museum",
        "Craft Fair & Maker Market",
        "Farmer's Market & Street Fair",
        "Auto & Trade Show",
        "Movie & Screening",
        "Trivia & Game Night",
        "Food & Beer Tasting",
        "Fitness, Run & Outdoor Activity",
        "Lecture, Class & Workshop",
        "General / Other",
    ]

    @model_validator(mode="after")
    def validate_category_genre_pairing(self):
        """Enforces logical hierarchy between primary_category and genre_or_subtype."""
        allowed_genres = CATEGORY_GENRE_MAP.get(
            self.primary_category, ["General / Other"]
        )
        if self.genre_or_subtype not in allowed_genres:
            self.genre_or_subtype = "General / Other"
        return self


SYSTEM_INSTRUCTION = """
You are a high-precision event data extraction assistant.
Analyze the raw scraped text and return a structured JSON response conforming strictly to the requested schema.

Guidelines:
- Strip out website navigation, footers, cookie notices, and ads.
- "clean_summary": Summarize the event concisely under 200 words following this format: [Brief description of highlights and atmosphere, with name and location only if explicitly mentioned]."
- "is_free": true if explicit $0/free; otherwise false.
- "age_restriction": Choose one: "Family", "All Ages", "18+", "21+", or "Unknown".
- "indoor_outdoor": Choose one: "Indoor", "Outdoor", "Mixed", or "Unknown".
- "primary_category": Assign the SINGLE best fit from the allowed list.
- "genre_or_subtype": Assign the most specific sub-genre belonging to the chosen primary_category.
"""


def clean_scraped_description(raw_text: str) -> dict:
    fallback = {
        "clean_summary": raw_text[:1000] if raw_text else "",
        "is_free": False,
        "age_restriction": "Unknown",
        "indoor_outdoor": "Unknown",
        "primary_category": "Other",
        "genre_or_subtype": "General / Other",
    }

    if not raw_text or len(raw_text.strip()) == 0:
        return fallback

    # Cap input at 1800 chars (~350 tokens) to eliminate slow CPU prefill on page boilerplate
    truncated_text = raw_text.strip()[:1800]

    try:
        response = ollama.chat(
            model="gemma3:4b",
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {
                    "role": "user",
                    "content": f"Extract structured event metadata:\n\n{truncated_text}",
                },
            ],
            format=CleanedEventDescription.model_json_schema(),
            keep_alive="30m",
            options={
                "temperature": 0.1,
                "num_ctx": 1536,
                "num_predict": 220,
            },
        )

        content = response["message"]["content"]
        validated_data = CleanedEventDescription.model_validate_json(content)
        return validated_data.model_dump()

    except Exception as e:
        logger.warning(f"[Ollama Cleaning Error]: {e}")
        return fallback


def process_event_batch(df: pd.DataFrame, max_workers: int = 2) -> pd.DataFrame:
    """Processes DataFrame with CPU-safe concurrency (max_workers=2) to avoid thread thrashing."""
    if df.empty:
        return df

    raw_texts = [
        str(row.get("big_desc") or row.get("description") or "").strip()
        for _, row in df.iterrows()
    ]

    logger.info(
        f"Executing Ollama metadata extraction across {len(raw_texts)} new events (workers={max_workers})..."
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(clean_scraped_description, raw_texts))

    df["clean_description"] = [r["clean_summary"] for r in results]
    df["is_free"] = [r["is_free"] for r in results]
    df["age_restriction"] = [r["age_restriction"] for r in results]
    df["indoor_outdoor"] = [r["indoor_outdoor"] for r in results]
    df["category"] = [r["primary_category"] for r in results]
    df["genre"] = [r["genre_or_subtype"] for r in results]

    return df


# ==============================================================================
# 2. ZERO-SHOT MOOD CLASSIFIER
# ==============================================================================

NOVELTY_SPECTRUM = {
    "traditional, conventional, classic, landmark": 0.05,
    "familiar, mainstream, popular, fresh": 0.3,
    "niche, specialized, alternative, eclectic": 0.7,
    "exotic, experimental, avant-garde, unconventional": 0.95,
}

SOCIAL_SPECTRUM = {
    "solitary, introspective, secluded, independent": 0.05,
    "intimate, conversational, cozy, small-group": 0.3,
    "communal, interactive, social, outgoing": 0.7,
    "packed, festive, high-capacity, party-oriented": 0.95,
}

ENERGY_SPECTRUM = {
    "restorative, stationary, serene, sedentary": 0.05,
    "leisurely, relaxed, seated, low-key": 0.3,
    "lively, animated, bustling, dynamic": 0.7,
    "intense, high-octane, rigorous, exhilarating": 0.95,
}

ALL_CANDIDATES = (
    list(NOVELTY_SPECTRUM.keys())
    + list(SOCIAL_SPECTRUM.keys())
    + list(ENERGY_SPECTRUM.keys())
)

CATEGORY_PRIORS = {
    "Live Music & Concerts":         {"energy": 0.15,  "social": 0.15,  "novelty": 0.05},
    "Stage & Performing Arts":       {"energy": 0.00,  "social": 0.00,  "novelty": 0.10},
    "Comedy & Live Entertainment":   {"energy": 0.10,  "social": 0.20,  "novelty": 0.05},
    "Visual Arts & Exhibitions":     {"energy": -0.15, "social": -0.10, "novelty": 0.15},
    "Festivals & Community Markets": {"energy": 0.10,  "social": 0.25,  "novelty": 0.10},
    "Film & Screenings":             {"energy": -0.20, "social": -0.10, "novelty": -0.05},
    "Food & Beverage":               {"energy": -0.05, "social": 0.15,  "novelty": 0.05},
    "Sports & Active Recreation":    {"energy": 0.30,  "social": 0.20,  "novelty": 0.00},
    "Workshops & Learning":          {"energy": -0.10, "social": 0.05,  "novelty": 0.20},
    "Social & Games":                {"energy": 0.05,  "social": 0.25,  "novelty": 0.10},
}

_MOOD_CLASSIFIER = None


def get_mood_classifier():
    """Lazy-initializes zero-shot classifier (uses DistilBERT on CPU for 4x speed, BART-large on GPU)."""
    global _MOOD_CLASSIFIER
    if _MOOD_CLASSIFIER is None:
        has_gpu = torch.cuda.is_available()
        device = 0 if has_gpu else -1
        torch_dtype = torch.float16 if has_gpu else torch.float32
        model_name = (
            "facebook/bart-large-mnli"
            if has_gpu
            else "typeform/distilbert-base-uncased-mnli"
        )
        logger.info(
            f"Loading zero-shot mood classifier ({model_name}) on device={device}..."
        )
        _MOOD_CLASSIFIER = pipeline(
            "zero-shot-classification",
            model=model_name,
            device=device,
            dtype=torch_dtype,
        )
    return _MOOD_CLASSIFIER


def batch_mood_classifier(df: pd.DataFrame, batch_size: int = 32) -> pd.DataFrame:
    """Computes normalized [0, 1] mood vector coordinates across the DataFrame in parallel batches."""
    if df.empty:
        return df

    classifier = get_mood_classifier()
    prompts = []
    for _, row in df.iterrows():
        desc = str(
            row.get("clean_description") or row.get("description") or ""
        ).strip()[:1000]
        cat = str(row.get("category", "Other"))
        gen = str(row.get("genre", "General / Other"))
        env = str(row.get("indoor_outdoor", "Unknown"))

        if not desc or len(desc) < 10:
            prompts.append("Empty event description")
        else:
            prompts.append(
                f"Category: {cat}. Subgenre: {gen}. Environment: {env}. Details: {desc}"
            )

    logger.info(
        f"Executing zero-shot mood batch inference across {len(prompts)} events..."
    )
    results = classifier(
        prompts,
        candidate_labels=ALL_CANDIDATES,
        multi_label=True,
        batch_size=batch_size,
    )

    if isinstance(results, dict):
        results = [results]

    novelty_coords = []
    social_coords = []
    energy_coords = []

    for idx, res in enumerate(results):
        score_map = dict(zip(res["labels"], res["scores"]))

        def calc_coord(spectrum):
            total = sum(score_map.get(lbl, 0.0) for lbl in spectrum.keys())
            if total == 0:
                return 0.5
            return sum(score_map.get(lbl, 0.0) * val for lbl, val in spectrum.items()) / total

        b_nov = calc_coord(NOVELTY_SPECTRUM)
        b_soc = calc_coord(SOCIAL_SPECTRUM)
        b_ene = calc_coord(ENERGY_SPECTRUM)

        row = df.iloc[idx]
        cat = str(row.get("category", "Other"))
        env = str(row.get("indoor_outdoor", "Unknown"))

        priors = CATEGORY_PRIORS.get(
            cat, {"energy": 0.0, "social": 0.0, "novelty": 0.0}
        )
        outdoor_boost = 0.05 if env == "Outdoor" else 0.0

        energy_coords.append(round(b_ene + priors["energy"], 4))
        social_coords.append(
            round(b_soc + priors["social"] + outdoor_boost, 4))
        novelty_coords.append(round(b_nov + priors["novelty"], 4))

    df["energy"] = energy_coords
    df["social"] = social_coords
    df["novelty"] = novelty_coords

    # Min-max scale each domain to [0, 1] safely (guarding against single-row / zero-variance batches)
    for col in ["energy", "social", "novelty"]:
        col_min = df[col].min()
        col_max = df[col].max()
        col_range = col_max - col_min
        if col_range > 0:
            df[col] = (df[col] - col_min) / col_range
        else:
            df[col] = df[col].clip(0.0, 1.0)

    return df


# ==============================================================================
# 3. INGESTION PIPELINE ORCHESTRATOR
# ==============================================================================

class VividaIngestionPipeline:
    def __init__(self, table_name: str = "events"):
        self.table_name = table_name
        self.supabase_url = os.getenv(
            "VITE_SUPABASE_URL") or os.getenv("SUPABASE_URL")
        self.supabase_key = (
            os.getenv("VITE_SUPABASE_SERVICE_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY")
        )

        if not self.supabase_url or not self.supabase_key:
            raise ValueError(
                "Missing VITE_SUPABASE_URL or VITE_SUPABASE_SERVICE_KEY environment variables."
            )

        self.client: Client = create_client(
            self.supabase_url, self.supabase_key)

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

    def normalize_start_times(self, val: Any) -> List[str]:
        """Normalizes list, JSON array string, Timestamp, or raw date string into flat YYYY-MM-DD strings."""
        if val is None or (not isinstance(val, list) and pd.isna(val)):
            return []

        raw_items: List[Any] = []
        if isinstance(val, list):
            raw_items = val
        elif isinstance(val, str):
            stripped = val.strip()
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                    raw_items = parsed if isinstance(
                        parsed, list) else [stripped]
                except Exception:
                    raw_items = [stripped]
            elif stripped:
                raw_items = [stripped]
        else:
            raw_items = [val]

        clean_dates: List[str] = []
        for item in raw_items:
            if isinstance(item, dict):
                item = item.get("start_time", "")
            if isinstance(item, pd.Timestamp):
                clean_dates.append(item.strftime("%Y-%m-%d"))
            elif item is not None:
                s = str(item).strip().strip('"').strip("'")
                if not s or s.lower() in ("none", "nan", "nat"):
                    continue
                if re.match(r"^\d{4}-\d{2}-\d{2}", s):
                    clean_dates.append(s[:10])
                else:
                    clean_dates.extend(parse_event_dates(s))
        return clean_dates

    def fetch_existing_enriched(
        self,
        event_ids: List[str],
        grouped_df: Optional[pd.DataFrame] = None,
        chunk_size: int = 100,
    ) -> Dict[str, Dict[str, Any]]:
        """Fetches already-enriched rows by ID first, with a (title, venue_name) fallback for shifted schedules."""
        if not event_ids:
            return {}

        existing: Dict[str, Dict[str, Any]] = {}
        select_cols = (
            "id, title, venue_name, description, is_free, energy, social, novelty, "
            "age_restriction, indoor_outdoor, genre, category"
        )

        # 1. Exact ID lookup
        for i in range(0, len(event_ids), chunk_size):
            chunk = event_ids[i: i + chunk_size]
            try:
                resp = (
                    self.client.table(self.table_name)
                    .select(select_cols)
                    .in_("id", chunk)
                    .execute()
                )
                for row in resp.data or []:
                    row_id = str(row.get("id", "")).strip()
                    if row_id and row.get("category") is not None and str(row.get("description") or "").strip():
                        existing[row_id] = row
            except Exception as e:
                logger.warning(
                    f"Could not fetch existing enriched cache chunk {i}: {e}")

        # 2. Secondary (title, venue_name) lookup for missed IDs whose schedule window shifted
        if grouped_df is not None:
            missed = grouped_df[~grouped_df["id"].isin(existing.keys())]
            if not missed.empty:
                unique_titles = missed["title"].dropna().unique().tolist()
                by_tv: Dict[tuple, Dict[str, Any]] = {}
                for i in range(0, len(unique_titles), 50):
                    t_chunk = unique_titles[i: i + 50]
                    try:
                        resp = (
                            self.client.table(self.table_name)
                            .select(select_cols)
                            .in_("title", t_chunk)
                            .execute()
                        )
                        for row in resp.data or []:
                            if row.get("category") is not None and str(row.get("description") or "").strip():
                                key = (
                                    str(row.get("title", "")).strip().lower(),
                                    str(row.get("venue_name", "")
                                        ).strip().lower(),
                                )
                                by_tv[key] = row
                    except Exception as e:
                        logger.warning(
                            f"Secondary (title, venue) cache lookup warning: {e}")

                for _, mrow in missed.iterrows():
                    key = (
                        str(mrow["title"]).strip().lower(),
                        str(mrow["venue_name"]).strip().lower(),
                    )
                    if key in by_tv:
                        existing[mrow["id"]] = by_tv[key]

        logger.info(
            f"Cache lookup matched {len(existing)}/{len(event_ids)} events in '{self.table_name}'."
        )
        return existing

    def transform_raw_events(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """
        Transforms, enriches, classifies, and deduplicates raw event DataFrames
        to match target PostgreSQL schema.
        """
        if raw_df.empty:
            logger.warning("Empty DataFrame passed to transformation step.")
            return pd.DataFrame()

        df = raw_df.copy()

        # 1. Normalize & explode start_time lists so every row has a single flat string date
        df["start_time"] = df["start_time"].apply(self.normalize_start_times)
        df = df.explode("start_time").reset_index(drop=True)

        # Filter out invalid rows missing start times, titles, or venue names
        for req_col in ["title", "venue_name"]:
            if req_col not in df.columns:
                df[req_col] = ""

        df = df[
            df["start_time"].notnull()
            & (df["start_time"].astype(str).str.strip() != "")
            & df["title"].notnull()
            & (df["title"].astype(str).str.strip() != "")
            & df["venue_name"].notnull()
            & (df["venue_name"].astype(str).str.strip() != "")
        ].reset_index(drop=True)

        if df.empty:
            logger.warning(
                "No valid events remaining after filtering empty start_times/titles/venues."
            )
            return pd.DataFrame()

        # Normalize grouping keys before groupby so whitespace/case jitter doesn't split groups
        df["_norm_title"] = df["title"].astype(str).str.strip()
        df["_norm_venue"] = df["venue_name"].astype(str).str.strip()

        # 2. Sanitize links
        for url_col in ["image_url", "ticket_link", "external_url"]:
            if url_col in df.columns:
                df[url_col] = df[url_col].apply(self.sanitize_urls)
            else:
                df[url_col] = None

        # 3. Handle Geocoding
        if "address" in df.columns:
            unique_addrs = [
                addr for addr in df["address"].dropna().unique() if str(addr).strip()
            ]
            coords_map = {addr: get_census_coordinates(
                addr) for addr in unique_addrs}
            df["latitude"] = df["address"].map(
                lambda a: coords_map.get(a, (None, None))[0] if a else None
            )
            df["longitude"] = df["address"].map(
                lambda a: coords_map.get(a, (None, None))[1] if a else None
            )
        else:
            df["address"] = None
            df["latitude"] = None
            df["longitude"] = None

        # 4. Consolidate Schedules & Aggregate Groupings BEFORE LLM/ML Enrichment
        group_cols = ["_norm_title", "_norm_venue"]

        def process_group(group: pd.DataFrame) -> pd.Series:
            ordered = group.sort_values(
                by=["start_time", "external_url"], na_position="last"
            )
            first = ordered.iloc[0]
            event_title, event_venue = group.name

            deduped_schedules = ordered.drop_duplicates(
                subset=["start_time", "external_url"]
            )
            schedule_list = [
                {
                    "start_time": str(row["start_time"]).strip(),
                    "register": row["external_url"],
                }
                for _, row in deduped_schedules.iterrows()
            ]

            # Pick first non-null metadata across grouped rows
            def first_valid(col: str) -> Any:
                if col not in ordered.columns:
                    return None
                non_null = ordered[col].dropna()
                for val in non_null:
                    if str(val).strip():
                        return val
                return None

            addr_val = first_valid("address")
            desc_val = first_valid("description")
            big_desc_val = first_valid("big_desc") or desc_val

            event_id = generate_event_id(
                title=event_title,
                venue_name=event_venue,
                start_time=ordered["start_time"].tolist(),
                address=addr_val or "",
            )

            return pd.Series(
                {
                    "id": event_id,
                    "title": str(event_title).strip(),
                    "venue_name": str(event_venue).strip(),
                    "address": addr_val,
                    "latitude": first.get("latitude"),
                    "longitude": first.get("longitude"),
                    "description": desc_val,
                    "big_desc": big_desc_val,
                    "schedule": json.dumps(schedule_list),
                    "external_url": first_valid("external_url"),
                    "image_url": first_valid("image_url"),
                    "ticket_link": first_valid("ticket_link"),
                }
            )

        grouped_df = (
            df.groupby(group_cols, dropna=False, sort=False)
            .apply(process_group)
            .reset_index(drop=True)
        )

        # 5. Split cached vs. unenriched events so we only run Ollama + BART on new records
        existing_map = self.fetch_existing_enriched(grouped_df["id"].tolist())
        is_cached = grouped_df["id"].isin(existing_map.keys())
        new_df = grouped_df[~is_cached].copy().reset_index(drop=True)
        cached_df = grouped_df[is_cached].copy().reset_index(drop=True)

        if not cached_df.empty:
            logger.info(
                f"Reusing existing Supabase enrichment for {len(cached_df)} cached events."
            )
            cached_df["clean_description"] = cached_df["id"].map(
                lambda i: existing_map[i]["description"]
            )
            cached_df["is_free"] = cached_df["id"].map(
                lambda i: existing_map[i]["is_free"]
            )
            cached_df["energy"] = cached_df["id"].map(
                lambda i: existing_map[i]["energy"]
            )
            cached_df["social"] = cached_df["id"].map(
                lambda i: existing_map[i]["social"]
            )
            cached_df["novelty"] = cached_df["id"].map(
                lambda i: existing_map[i]["novelty"]
            )
            cached_df["age_restriction"] = cached_df["id"].map(
                lambda i: existing_map[i]["age_restriction"]
            )
            cached_df["indoor_outdoor"] = cached_df["id"].map(
                lambda i: existing_map[i]["indoor_outdoor"]
            )
            cached_df["genre"] = cached_df["id"].map(
                lambda i: existing_map[i]["genre"]
            )
            cached_df["category"] = cached_df["id"].map(
                lambda i: existing_map[i]["category"]
            )

        if not new_df.empty:
            new_df = process_event_batch(new_df, max_workers=2)
            new_df = batch_mood_classifier(new_df, batch_size=16)

        enriched_df = pd.concat([cached_df, new_df], ignore_index=True)

        # 6. Finalize Exact PostgreSQL Schema Columns
        transformed_df = pd.DataFrame(
            {
                "id": enriched_df["id"],
                "title": enriched_df["title"],
                "venue_name": enriched_df["venue_name"],
                "address": enriched_df["address"],
                "latitude": enriched_df["latitude"],
                "longitude": enriched_df["longitude"],
                "description": enriched_df["clean_description"].where(
                    enriched_df["clean_description"].astype(
                        str).str.strip() != "",
                    enriched_df["description"],
                ),
                "is_free": enriched_df["is_free"].astype(bool),
                "schedule": enriched_df["schedule"],
                "energy": enriched_df["energy"].apply(self.sanitize_mood_score),
                "social": enriched_df["social"].apply(self.sanitize_mood_score),
                "novelty": enriched_df["novelty"].apply(self.sanitize_mood_score),
                "age_restriction": enriched_df["age_restriction"],
                "indoor_outdoor": enriched_df["indoor_outdoor"],
                "external_url": enriched_df["external_url"],
                "image_url": enriched_df["image_url"],
                "genre": enriched_df["genre"],
                "ticket_link": enriched_df["ticket_link"],
                "category": enriched_df["category"],
            }
        )

        return transformed_df

    def upsert_to_supabase(
        self, transformed_df: pd.DataFrame, batch_size: int = 100
    ) -> int:
        if transformed_df.empty:
            logger.info("No records to upsert.")
            return 0

        records = transformed_df.replace(
            {np.nan: None}).to_dict(orient="records")
        total_records = len(records)
        logger.info(
            f"Preparing to upsert {total_records} records into '{self.table_name}'..."
        )

        upserted_count = 0
        for i in range(0, total_records, batch_size):
            batch = records[i: i + batch_size]
            try:
                self.client.table(self.table_name).upsert(
                    batch,
                    on_conflict="id",
                ).execute()
                upserted_count += len(batch)
                logger.info(
                    f"Upserted batch {i // batch_size + 1} ({len(batch)} records)."
                )
            except Exception as e:
                logger.error(
                    f"Failed to upsert batch starting at index {i}: {e}"
                )

        logger.info(
            f"Pipeline complete. Total records synchronized: {upserted_count}/{total_records}"
        )
        return upserted_count

    def run_standalone_pipeline(self, raw_df: pd.DataFrame) -> bool:
        try:
            logger.info("Starting standalone VM ETL Pipeline execution...")
            transformed_df = self.transform_raw_events(raw_df)
            if not transformed_df.empty:
                self.upsert_to_supabase(transformed_df)
                return True
            else:
                logger.warning(
                    "Pipeline finished with zero records transformed.")
                return False
        except Exception as e:
            logger.critical(
                f"Standalone pipeline execution failed: {e}", exc_info=True
            )
            return False
