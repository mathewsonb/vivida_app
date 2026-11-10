"""
vivida_pipeline_runner.py
Aligned Standalone ETL, AI Enrichment & Supabase Ingestion Module.
"""
import os
import sys
import json
import logging
import concurrent.futures
from typing import List, Dict, Any, Optional, Literal

import pandas as pd
import numpy as np
import requests
from pydantic import BaseModel, Field, model_validator
from supabase import create_client, Client
from dotenv import load_dotenv, find_dotenv

# Relative package imports
from .vivida_etl_helpers import (
    get_census_coordinates,
    clean_str,
    generate_event_id,
)

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("vivida_etl")

# Load HuggingFace Transformers / PyTorch with defensive fallback
HAVE_TRANSFORMERS = False
try:
    import torch
    from transformers import pipeline
    HAVE_TRANSFORMERS = True
except ImportError:
    logger.warning("PyTorch/Transformers not installed. Mood classifier will use rule-based fallbacks.")

# Load Ollama client with defensive fallback
HAVE_OLLAMA = False
try:
    import ollama
    HAVE_OLLAMA = True
except ImportError:
    logger.warning("Ollama library not installed. Description cleaner will use text truncation fallbacks.")

# --- 1. CLASSIFICATION & SPECTRUM SCHEMAS ---
CATEGORY_GENRE_MAP: Dict[str, List[str]] = {
    "Live Music & Concerts": [
        "Rock, Pop & Alternative", "Hip-Hop, R&B & Electronic",
        "Country, Folk & Americana", "Jazz, Blues & Soul",
        "Classical, Opera & Vocal", "General / Other"
    ],
    "Stage & Performing Arts": [
        "Theater & Musicals", "Dance & Ballet", "Magic & Variety", "General / Other"
    ],
    "Comedy & Live Entertainment": [
        "Stand-Up & Improv", "Magic & Variety", "General / Other"
    ],
    "Visual Arts & Exhibitions": [
        "Art Gallery & Museum", "Craft Fair & Maker Market", "Auto & Trade Show", "General / Other"
    ],
    "Festivals & Community Markets": [
        "Craft Fair & Maker Market", "Farmer's Market & Street Fair", "General / Other"
    ],
    "Film & Screenings": ["Movie & Screening", "General / Other"],
    "Food & Beverage": ["Food & Beer Tasting", "Farmer's Market & Street Fair", "General / Other"],
    "Sports & Active Recreation": ["Fitness, Run & Outdoor Activity", "General / Other"],
    "Workshops & Learning": ["Lecture, Class & Workshop", "General / Other"],
    "Social & Games": ["Trivia & Game Night", "General / Other"],
    "Other": ["General / Other"]
}

class CleanedEventDescription(BaseModel):
    clean_summary: str = Field(description="Under 200 word structured summary")
    is_free: bool = Field(description="True if explicitly free entry or $0")
    age_restriction: Literal["Family", "All Ages", "18+", "21+", "Unknown"]
    indoor_outdoor: Literal["Indoor", "Outdoor", "Mixed", "Unknown"]
    primary_category: Literal[
        "Live Music & Concerts", "Stage & Performing Arts", "Comedy & Live Entertainment",
        "Visual Arts & Exhibitions", "Festivals & Community Markets", "Film & Screenings",
        "Food & Beverage", "Sports & Active Recreation", "Workshops & Learning",
        "Social & Games", "Other"
    ]
    genre_or_subtype: Literal[
        "Rock, Pop & Alternative", "Hip-Hop, R&B & Electronic", "Country, Folk & Americana",
        "Jazz, Blues & Soul", "Classical, Opera & Vocal", "Theater & Musicals",
        "Dance & Ballet", "Stand-Up & Improv", "Magic & Variety", "Art Gallery & Museum",
        "Craft Fair & Maker Market", "Farmer's Market & Street Fair", "Auto & Trade Show",
        "Movie & Screening", "Trivia & Game Night", "Food & Beer Tasting",
        "Fitness, Run & Outdoor Activity", "Lecture, Class & Workshop", "General / Other"
    ]

    @model_validator(mode="after")
    def validate_category_genre_pairing(self):
        allowed_genres = CATEGORY_GENRE_MAP.get(self.primary_category, ["General / Other"])
        if self.genre_or_subtype not in allowed_genres:
            self.genre_or_subtype = "General / Other"
        return self

SYSTEM_INSTRUCTION = """
You are a high-precision event data extraction assistant.
Analyze the raw scraped text and return a structured JSON response conforming strictly to the requested schema.

Guidelines:
- Strip out website navigation, footers, cookie notices, and ads.
- "clean_summary": Summarize the event concisely under 200 words.
- "is_free": true if explicit $0/free; otherwise false.
- "age_restriction": Choose one: "Family", "All Ages", "18+", "21+", or "Unknown".
- "indoor_outdoor": Choose one: "Indoor", "Outdoor", "Mixed", or "Unknown".
- "primary_category": Assign the SINGLE best fit from the allowed list.
- "genre_or_subtype": Assign the most specific sub-genre belonging to the chosen primary_category.
"""

NOVELTY_SPECTRUM = {
    "traditional, conventional, classic, landmark": 0.05,
    "familiar, mainstream, popular, fresh": 0.3,
    "niche, specialized, alternative, eclectic": 0.7,
    "exotic, experimental, avant-garde, unconventional": 0.95
}

SOCIAL_SPECTRUM = {
    "solitary, introspective, secluded, independent": 0.05,
    "intimate, conversational, cozy, small-group": 0.3,
    "communal, interactive, social, outgoing": 0.7,
    "packed, festive, high-capacity, party-oriented": 0.95
}

ENERGY_SPECTRUM = {
    "restorative, stationary, serene, sedentary": 0.05,
    "leisurely, relaxed, seated, low-key": 0.3,
    "lively, animated, bustling, dynamic": 0.7,
    "intense, high-octane, rigorous, exhilarating": 0.95
}

ALL_CANDIDATES = list(NOVELTY_SPECTRUM.keys()) + list(SOCIAL_SPECTRUM.keys()) + list(ENERGY_SPECTRUM.keys())

CATEGORY_PRIORS = {
    "Live Music & Concerts": {"energy": 0.15, "social": 0.15, "novelty": 0.05},
    "Stage & Performing Arts": {"energy": 0.00, "social": 0.00, "novelty": 0.10},
    "Comedy & Live Entertainment": {"energy": 0.10, "social": 0.20, "novelty": 0.05},
    "Visual Arts & Exhibitions": {"energy": -0.15, "social": -0.10, "novelty": 0.15},
    "Festivals & Community Markets": {"energy": 0.10, "social": 0.25, "novelty": 0.10},
    "Film & Screenings": {"energy": -0.20, "social": -0.10, "novelty": -0.05},
    "Food & Beverage": {"energy": -0.05, "social": 0.15, "novelty": 0.05},
    "Sports & Active Recreation": {"energy": 0.30, "social": 0.20, "novelty": 0.00},
    "Workshops & Learning": {"energy": -0.10, "social": 0.05, "novelty": 0.20},
    "Social & Games": {"energy": 0.05, "social": 0.25, "novelty": 0.10},
}

# --- 2. PIPELINE RUNNER CLASS ---
class VividaIngestionPipeline:
    def __init__(self, table_name: str = "events"):
        load_dotenv(find_dotenv(".env.local"))
        load_dotenv(find_dotenv(".env"))

        self.table_name = table_name
        self.supabase_url = os.getenv("VITE_SUPABASE_URL") or os.getenv("SUPABASE_URL")
        self.supabase_key = (
            os.getenv("VITE_SUPABASE_SERVICE_KEY")
            or os.getenv("VITE_SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )

        if not self.supabase_url or not self.supabase_key:
            raise ValueError("Missing VITE_SUPABASE_URL or VITE_SUPABASE_SERVICE_KEY environment variables.")

        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self._classifier_pipeline = None

    def _get_zero_shot_classifier(self):
        """Lazy-loads HuggingFace Zero-Shot classifier."""
        if not HAVE_TRANSFORMERS:
            return None
        if self._classifier_pipeline is None:
            try:
                device = 0 if torch.cuda.is_available() else -1
                torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
                logger.info("Initializing zero-shot classification pipeline (facebook/bart-large-mnli)...")
                self._classifier_pipeline = pipeline(
                    "zero-shot-classification",
                    model="facebook/bart-large-mnli",
                    device=device,
                    dtype=torch_dtype
                )
            except Exception as e:
                logger.warning(f"Could not load HuggingFace classifier: {e}. Falling back to prior-based mood scoring.")
                self._classifier_pipeline = None
        return self._classifier_pipeline

    def clean_scraped_description(self, raw_text: str) -> dict:
        fallback = {
            "clean_summary": raw_text[:1250] if raw_text else "",
            "is_free": False,
            "age_restriction": "Unknown",
            "indoor_outdoor": "Unknown",
            "primary_category": "Other",
            "genre_or_subtype": "General / Other"
        }

        if not HAVE_OLLAMA or not raw_text or len(raw_text.strip()) == 0:
            return fallback

        try:
            # Explicitly check for OLLAMA_HOST from environment or fallback to Docker host
            ollama_host = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
            client = ollama.Client(host=ollama_host)
            
            response = client.chat(
                model="gemma3:4b",
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
                    {"role": "user", "content": f"Clean and extract structured data from this raw scraped text:\n\n{raw_text}"},
                ],
                format=CleanedEventDescription.model_json_schema(),
                options={"temperature": 0.1, "num_ctx": 4096, "num_predict": 512},
            )
            content = response["message"]["content"]
            validated_data = CleanedEventDescription.model_validate_json(content)
            return validated_data.model_dump()
        except Exception as e:
            logger.warning(f"[Ollama Cleaning Warning]: {e}. Using fallback structure.")
            return fallback

    def process_event_batch_cleaning(self, df: pd.DataFrame, max_workers: int = 4) -> pd.DataFrame:
        """Executes description cleaning in parallel."""
        raw_texts = [
            row.get("big_desc") or row.get("description") or "" 
            for _, row in df.iterrows()
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(self.clean_scraped_description, raw_texts))

        df["clean_description"] = [r["clean_summary"] for r in results]
        df["is_free"] = [r["is_free"] for r in results]
        df["age_restriction"] = [r["age_restriction"] for r in results]
        df["indoor_outdoor"] = [r["indoor_outdoor"] for r in results]
        df["category"] = [r["primary_category"] for r in results]
        df["genre"] = [r["genre_or_subtype"] for r in results]

        return df

    def batch_mood_classifier(self, df: pd.DataFrame, batch_size: int = 16) -> pd.DataFrame:
        """Computes energy, social, and novelty vector scores."""
        classifier_pipe = self._get_zero_shot_classifier()
        
        novelty_coords, social_coords, energy_coords = [], [], []

        if classifier_pipe is not None:
            prompts = []
            for _, row in df.iterrows():
                desc = str(row.get("clean_description") or row.get("description") or "").strip()[:1000]
                cat = str(row.get("category", "Other"))
                gen = str(row.get("genre", "General / Other"))
                env = str(row.get("indoor_outdoor", "Unknown"))
                
                if not desc or len(desc) < 10:
                    prompts.append("Empty event description")
                else:
                    prompts.append(f"Category: {cat}. Subgenre: {gen}. Environment: {env}. Details: {desc}")

            logger.info(f"Executing zero-shot batch inference across {len(prompts)} events...")
            try:
                results = classifier_pipe(
                    prompts, candidate_labels=ALL_CANDIDATES, multi_label=True, batch_size=batch_size
                )
                for idx, res in enumerate(results):
                    score_map = dict(zip(res['labels'], res['scores']))

                    def calc_coord(spectrum):
                        total = sum(score_map.get(lbl, 0.0) for lbl in spectrum.keys())
                        return sum(score_map.get(lbl, 0.0) * val for lbl, val in spectrum.items()) / total if total > 0 else 0.5

                    b_nov = calc_coord(NOVELTY_SPECTRUM)
                    b_soc = calc_coord(SOCIAL_SPECTRUM)
                    b_ene = calc_coord(ENERGY_SPECTRUM)

                    row = df.iloc[idx]
                    cat = str(row.get("category", "Other"))
                    env = str(row.get("indoor_outdoor", "Unknown"))
                    priors = CATEGORY_PRIORS.get(cat, {"energy": 0.0, "social": 0.0, "novelty": 0.0})
                    outdoor_boost = 0.05 if env == "Outdoor" else 0.0

                    energy_coords.append(self.sanitize_mood_score(b_ene + priors["energy"]))
                    social_coords.append(self.sanitize_mood_score(b_soc + priors["social"] + outdoor_boost))
                    novelty_coords.append(self.sanitize_mood_score(b_nov + priors["novelty"]))
            except Exception as err:
                logger.error(f"Zero-shot classification error: {err}. Applying fallback mood scores.")
                classifier_pipe = None

        if classifier_pipe is None:
            for _, row in df.iterrows():
                cat = str(row.get("category", "Other"))
                env = str(row.get("indoor_outdoor", "Unknown"))
                priors = CATEGORY_PRIORS.get(cat, {"energy": 0.0, "social": 0.0, "novelty": 0.0})
                outdoor_boost = 0.05 if env == "Outdoor" else 0.0

                energy_coords.append(self.sanitize_mood_score(0.50 + priors["energy"]))
                social_coords.append(self.sanitize_mood_score(0.50 + priors["social"] + outdoor_boost))
                novelty_coords.append(self.sanitize_mood_score(0.50 + priors["novelty"]))

        df["energy"] = energy_coords
        df["social"] = social_coords
        df["novelty"] = novelty_coords
        return df

    def sanitize_urls(self, url_str: Any) -> Optional[str]:
        if not url_str or pd.isna(url_str) or str(url_str).strip() in ["", "NULL", "none"]:
            return None
        clean_url = str(url_str).strip()
        return clean_url.replace("http:", "https:", 1) if clean_url.startswith("http:") else clean_url

    def sanitize_mood_score(self, val: Any) -> float:
        try:
            if pd.isna(val) or val is None:
                return 0.50
            return round(max(0.00, min(1.00, float(val))), 2)
        except (ValueError, TypeError):
            return 0.50

    def transform_raw_events(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        if raw_df.empty:
            logger.warning("Empty DataFrame passed to transformation step.")
            return pd.DataFrame()

        df = raw_df.copy()

        # Sanitize complex types (lists/dicts) into primitive strings prior to grouping
        for col in df.columns:
            if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
                df[col] = df[col].apply(lambda x: json.dumps(x) if isinstance(x, (list, dict)) else x)

        # 1. Filter out invalid rows missing start times or titles
        df = df[df["start_time"].notnull() & (df["title"].astype(str).str.strip() != "")].reset_index(drop=True)
        if df.empty:
            logger.warning("No valid events remaining after filtering empty start_times/titles.")
            return pd.DataFrame()

        # 2. STEP A: Run Ollama LLM Description Cleaning & Schema Extraction
        logger.info("Executing Ollama description cleaning and feature extraction...")
        df = self.process_event_batch_cleaning(df)

        # 3. STEP B: Run Zero-Shot Mood Classifier (Energy, Social, Novelty)
        logger.info("Executing Mood Classifier vector inference...")
        df = self.batch_mood_classifier(df)

        # 4. Sanitize links
        for url_col in ["image_url", "ticket_link", "external_url"]:
            if url_col in df.columns:
                df[url_col] = df[url_col].apply(self.sanitize_urls)
            else:
                df[url_col] = None

        # 5. Geocoding
        if "address" in df.columns:
            unique_addrs = [addr for addr in df["address"].dropna().unique() if str(addr).strip()]
            coords_map = {addr: get_census_coordinates(addr) for addr in unique_addrs}
            df["latitude"] = df["address"].map(lambda a: coords_map.get(a, (None, None))[0] if a else None)
            df["longitude"] = df["address"].map(lambda a: coords_map.get(a, (None, None))[1] if a else None)
        else:
            df["address"] = None
            df["latitude"] = None
            df["longitude"] = None

        # 6. Group, Deduplicate Schedules & Format Output Schema
        group_cols = ["title", "venue_name"]

        def process_group(group):
            first = group.iloc[0]

            title_val = first["title"] if "title" in first else (group.name[0] if isinstance(group.name, tuple) else group.name)
            venue_val = first["venue_name"] if "venue_name" in first else (group.name[1] if isinstance(group.name, tuple) else "")

            sub_df = pd.DataFrame({
                "start_time": group["start_time"].astype(str),
                "external_url": group["external_url"].astype(str) if "external_url" in group else ""
            })
            deduped_idx = sub_df.drop_duplicates(subset=["start_time", "external_url"]).index
            deduped_group = group.loc[deduped_idx]

            schedule_list = [
                {
                    "start_time": str(row["start_time"]),
                    "register": row.get("external_url")
                }
                for _, row in deduped_group.iterrows()
            ]

            event_id = generate_event_id(
                title=str(title_val),
                venue_name=str(venue_val),
                start_time=group["start_time"].tolist(),
                address=str(first.get("address", ""))
            )

            # Use LLM description if available; fallback to raw scraper description
            final_description = first.get("clean_description") or first.get("description")

            return pd.Series({
                "id": event_id,
                "title": str(title_val).strip(),
                "venue_name": str(venue_val).strip(),
                "address": first.get("address"),
                "latitude": first.get("latitude"),
                "longitude": first.get("longitude"),
                "description": final_description,
                "is_free": bool(first.get("is_free", False)),
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

        transformed_df = df.groupby(group_cols, as_index=False, group_keys=False).apply(process_group).reset_index(drop=True)
        return transformed_df

    def upsert_to_supabase(self, transformed_df: pd.DataFrame, batch_size: int = 100) -> int:
        if transformed_df.empty:
            logger.info("No records to upsert.")
            return 0

        records = transformed_df.replace({np.nan: None}).to_dict(orient="records")
        total_records = len(records)
        logger.info(f"Preparing to upsert {total_records} records into '{self.table_name}'...")

        upserted_count = 0
        for i in range(0, total_records, batch_size):
            batch = records[i : i + batch_size]
            try:
                response = self.client.table(self.table_name).upsert(
                    batch, on_conflict="id"
                ).execute()
                upserted_count += len(batch)
                logger.info(f"Upserted batch {i // batch_size + 1} ({len(batch)} records).")
            except Exception as e:
                logger.error(f"Failed to upsert batch starting at index {i}: {e}")
                if hasattr(e, "message"):
                    logger.error(f"PostgREST Error Message: {e.message}")
                if hasattr(e, "details"):
                    logger.error(f"PostgREST Error Details: {e.details}")

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