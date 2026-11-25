"""
vivida_pipeline_runner.py
Aligned Standalone ETL, LLM Enrichment, Zero-Shot Mood Classification & Supabase Ingestion Module.
"""

import concurrent.futures
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

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

LOCAL_TZ = ZoneInfo("America/Los_Angeles")

# ==============================================================================
# 1. PREPROCESSOR SCHEMA & LLM EXTRACTION (OLLAMA + GEMINI FALLBACK)
# ==============================================================================
OLLAMA_MODEL = "gemma3:4b"
GEMINI_MODEL = os.getenv("GEMINI_ENRICHMENT_MODEL", "gemini-2.5-flash")

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


def _get_gemini_api_key() -> Optional[str]:
    key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("VITE_GEMINI_API_KEY")
        or ""
    ).strip()
    return key or None


def _is_ollama_reachable() -> bool:
    try:
        ollama.list()
        return True
    except Exception:
        return False


def ensure_ollama_available(model_name: str = OLLAMA_MODEL) -> None:
    """Verifies Ollama daemon is reachable (auto-starting 'ollama serve' if offline) and model is pulled."""
    try:
        listing = ollama.list()
    except Exception as initial_err:
        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            raise RuntimeError(
                f"Ollama daemon is unreachable ({initial_err}) and 'ollama' binary was not found on PATH."
            ) from initial_err

        logger.info(
            "Ollama daemon is offline; auto-starting 'ollama serve' in background..."
        )
        subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        listing = None
        for _ in range(15):
            time.sleep(1.0)
            try:
                listing = ollama.list()
                logger.info("Ollama daemon started and is now reachable.")
                break
            except Exception:
                continue

        if listing is None:
            raise RuntimeError(
                "Ollama daemon failed to become reachable within 15s after auto-starting 'ollama serve'."
            ) from initial_err

    models = getattr(listing, "models", None) or (
        listing.get("models", []) if isinstance(listing, dict) else []
    )
    available_names = [
        (
            getattr(m, "model", None)
            or getattr(m, "name", None)
            or (m.get("model") or m.get("name") if isinstance(m, dict) else "")
        )
        for m in models
    ]
    if not any(
        name == model_name or name.startswith(f"{model_name}:")
        for name in available_names
    ):
        raise RuntimeError(
            f"Ollama model '{model_name}' is not installed (found: {available_names}). "
            f"Run 'ollama pull {model_name}' first."
        )


def _default_enrichment_fallback(raw_text: str) -> dict:
    return {
        "clean_summary": raw_text[:1000] if raw_text else "",
        "is_free": False,
        "age_restriction": "Unknown",
        "indoor_outdoor": "Unknown",
        "primary_category": "Other",
        "genre_or_subtype": "General / Other",
    }


def clean_scraped_description_ollama(raw_text: str) -> dict:
    fallback = _default_enrichment_fallback(raw_text)
    if not raw_text or not raw_text.strip():
        return fallback

    # Cap input at 1800 chars (~350 tokens) to eliminate slow CPU prefill on page boilerplate
    truncated_text = raw_text.strip()[:1800]

    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
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


def clean_scraped_description_gemini(
    raw_text: str,
    api_key: str,
    model_name: str = GEMINI_MODEL,
) -> dict:
    fallback = _default_enrichment_fallback(raw_text)
    if not raw_text or not raw_text.strip():
        return fallback

    truncated_text = raw_text.strip()[:1800]

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("[Gemini Fallback]: 'google-genai' is not installed.")
        return fallback

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=f"Extract structured event metadata:\n\n{truncated_text}",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=CleanedEventDescription,
                temperature=0.1,
                max_output_tokens=400,
            ),
        )
        raw_json = getattr(response, "text", None) or ""
        validated_data = CleanedEventDescription.model_validate_json(raw_json)
        return validated_data.model_dump()
    except Exception as e:
        logger.warning(f"[Gemini Cleaning Error]: {e}")
        return fallback


def clean_scraped_description(raw_text: str) -> dict:
    """Default single-item cleaner using Ollama (kept for backward compatibility)."""
    return clean_scraped_description_ollama(raw_text)


def process_event_batch(df: pd.DataFrame, max_workers: int = 2) -> pd.DataFrame:
    """
    Processes event metadata using local Ollama when available, falling back to
    the Gemini API when Ollama is unreachable/uninstalled and a Gemini API key is set.
    """
    if df.empty:
        return df

    gemini_key = _get_gemini_api_key()
    use_gemini = False

    if _is_ollama_reachable():
        ensure_ollama_available(OLLAMA_MODEL)
    elif gemini_key:
        logger.info(
            f"Ollama daemon is unreachable; using Gemini API fallback ({GEMINI_MODEL}) for metadata extraction."
        )
        use_gemini = True
    else:
        # Attempt ensure_ollama_available so normal error/startup behavior is preserved when no Gemini key exists
        ensure_ollama_available(OLLAMA_MODEL)

    raw_texts = []
    for _, row in df.iterrows():
        title = str(row.get("title") or "").strip()
        venue = str(row.get("venue_name") or "").strip()
        body = str(row.get("big_desc") or row.get("description") or "").strip()
        prefix = f"Event: {title} | Venue: {venue}\n" if title else ""
        raw_texts.append(f"{prefix}{body}".strip())

    backend_label = f"Gemini ({GEMINI_MODEL})" if use_gemini else f"Ollama ({OLLAMA_MODEL})"
    logger.info(
        f"Executing {backend_label} metadata extraction across {len(raw_texts)} new events (workers={max_workers})..."
    )

    if use_gemini and gemini_key:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(
                executor.map(
                    lambda text: clean_scraped_description_gemini(
                        text, api_key=gemini_key, model_name=GEMINI_MODEL
                    ),
                    raw_texts,
                )
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(clean_scraped_description_ollama, raw_texts))

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
MOOD_MODEL_NAME = "facebook/bart-large-mnli"

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
    """Lazy-initializes zero-shot BART-large MNLI classifier for maximum scoring fidelity."""
    global _MOOD_CLASSIFIER
    if _MOOD_CLASSIFIER is None:
        has_gpu = torch.cuda.is_available()
        device = 0 if has_gpu else -1
        torch_dtype = torch.float16 if has_gpu else torch.float32
        logger.info(
            f"Loading zero-shot mood classifier ({MOOD_MODEL_NAME}) on device={device}..."
        )
        _MOOD_CLASSIFIER = pipeline(
            "zero-shot-classification",
            model=MOOD_MODEL_NAME,
            device=device,
            dtype=torch_dtype,
        )
    return _MOOD_CLASSIFIER


def batch_mood_classifier(df: pd.DataFrame, batch_size: int = 8) -> pd.DataFrame:
    """Computes globally calibrated [0, 1] mood vector coordinates across the DataFrame."""
    if df.empty:
        return df

    classifier = get_mood_classifier()
    effective_batch_size = 32 if torch.cuda.is_available() else min(batch_size, 8)

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
        f"Executing zero-shot mood batch inference ({MOOD_MODEL_NAME}) across {len(prompts)} events..."
    )
    with torch.inference_mode():
        results = classifier(
            prompts,
            candidate_labels=ALL_CANDIDATES,
            multi_label=True,
            batch_size=effective_batch_size,
        )

    if isinstance(results, dict):
        results = [results]

    novelty_coords = []
    social_coords = []
    energy_coords = []

    for idx, res in enumerate(results):
        score_map = dict(zip(res["labels"], res["scores"]))

        def calc_coord(spectrum: Dict[str, float]) -> float:
            total = sum(score_map.get(lbl, 0.0) for lbl in spectrum.keys())
            if total == 0:
                return 0.5
            return (
                sum(score_map.get(lbl, 0.0) * val for lbl, val in spectrum.items())
                / total
            )

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
        social_coords.append(round(b_soc + priors["social"] + outdoor_boost, 4))
        novelty_coords.append(round(b_nov + priors["novelty"], 4))

    # Preserve absolute cross-venue calibration on [0, 1] instead of per-venue min-max stretching
    df["energy"] = pd.Series(energy_coords, index=df.index).clip(0.0, 1.0)
    df["social"] = pd.Series(social_coords, index=df.index).clip(0.0, 1.0)
    df["novelty"] = pd.Series(novelty_coords, index=df.index).clip(0.0, 1.0)

    return df


# ==============================================================================
# 3. INGESTION PIPELINE ORCHESTRATOR
# ==============================================================================


class VividaIngestionPipeline:
    def __init__(self, table_name: str = "events"):
        self.table_name = table_name
        self.supabase_url = os.getenv("VITE_SUPABASE_URL") or os.getenv("SUPABASE_URL")
        self.supabase_key = (
            os.getenv("VITE_SUPABASE_SERVICE_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY")
        )

        if not self.supabase_url or not self.supabase_key:
            raise ValueError(
                "Missing VITE_SUPABASE_URL or VITE_SUPABASE_SERVICE_KEY environment variables."
            )

        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self._last_superseded_ids: List[str] = []

    @staticmethod
    def _is_valid_enrichment(row: Dict[str, Any]) -> bool:
        """Rejects unenriched fallback rows or missing mood coordinates so they get re-processed."""
        cat = str(row.get("category") or "").strip()
        desc = str(row.get("description") or "").strip()
        if not cat or not desc:
            return False
        if (
            row.get("energy") is None
            or row.get("social") is None
            or row.get("novelty") is None
        ):
            return False
        if (
            cat == "Other"
            and str(row.get("age_restriction") or "Unknown") == "Unknown"
            and str(row.get("indoor_outdoor") or "Unknown") == "Unknown"
        ):
            return False
        return True

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
        """Normalizes list, JSON array string, Timestamp, or raw date string into flat local YYYY-MM-DD strings."""
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
                    raw_items = parsed if isinstance(parsed, list) else [stripped]
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
                dt = item.tz_convert(LOCAL_TZ) if item.tzinfo is not None else item
                clean_dates.append(dt.strftime("%Y-%m-%d"))
            elif item is not None:
                s = str(item).strip().strip('"').strip("'")
                if not s or s.lower() in ("none", "nan", "nat"):
                    continue
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                    clean_dates.append(s)
                elif re.match(r"^\d{4}-\d{2}-\d{2}[T\s]", s):
                    dt = pd.to_datetime(s, errors="coerce")
                    if pd.notnull(dt):
                        if dt.tzinfo is not None:
                            dt = dt.tz_convert(LOCAL_TZ)
                        clean_dates.append(dt.strftime("%Y-%m-%d"))
                    else:
                        clean_dates.append(s[:10])
                else:
                    clean_dates.extend(parse_event_dates(s))
        return clean_dates

    def fetch_existing_enriched(
        self,
        event_ids: List[str],
        grouped_df: Optional[pd.DataFrame] = None,
        chunk_size: int = 100,
    ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
        """
        Fetches already-enriched rows by ID first, with a (title, venue_name) fallback
        for shifted schedules. Returns (existing_map, superseded_old_ids).
        """
        if not event_ids:
            return {}, []

        existing: Dict[str, Dict[str, Any]] = {}
        superseded_ids: List[str] = []
        select_cols = (
            "id, title, venue_name, description, is_free, energy, social, novelty, "
            "age_restriction, indoor_outdoor, genre, category"
        )

        # 1. Exact ID lookup
        for i in range(0, len(event_ids), chunk_size):
            chunk = event_ids[i : i + chunk_size]
            try:
                resp = (
                    self.client.table(self.table_name)
                    .select(select_cols)
                    .in_("id", chunk)
                    .execute()
                )
                for row in resp.data or []:
                    row_id = str(row.get("id", "")).strip()
                    if row_id and self._is_valid_enrichment(row):
                        existing[row_id] = row
            except Exception as e:
                logger.warning(
                    f"Could not fetch existing enriched cache chunk {i}: {e}"
                )

        # 2. Secondary (title, venue_name) lookup for missed IDs whose schedule window shifted
        if grouped_df is not None:
            missed = grouped_df[~grouped_df["id"].isin(existing.keys())]
            if not missed.empty:
                unique_titles = missed["title"].dropna().unique().tolist()
                by_tv: Dict[Tuple[str, str], Dict[str, Any]] = {}
                for i in range(0, len(unique_titles), 50):
                    t_chunk = unique_titles[i : i + 50]
                    try:
                        resp = (
                            self.client.table(self.table_name)
                            .select(select_cols)
                            .in_("title", t_chunk)
                            .execute()
                        )
                        for row in resp.data or []:
                            if self._is_valid_enrichment(row):
                                key = (
                                    str(row.get("title", "")).strip().lower(),
                                    str(row.get("venue_name", "")).strip().lower(),
                                )
                                by_tv[key] = row
                    except Exception as e:
                        logger.warning(
                            f"Secondary (title, venue) cache lookup warning: {e}"
                        )

                for _, mrow in missed.iterrows():
                    key = (
                        str(mrow["title"]).strip().lower(),
                        str(mrow["venue_name"]).strip().lower(),
                    )
                    if key in by_tv:
                        matched_row = by_tv[key]
                        existing[mrow["id"]] = matched_row
                        old_id = str(matched_row.get("id", "")).strip()
                        if old_id and old_id != mrow["id"]:
                            superseded_ids.append(old_id)

        logger.info(
            f"Cache lookup matched {len(existing)}/{len(event_ids)} events in '{self.table_name}' "
            f"({len(superseded_ids)} shifted schedules)."
        )
        return existing, superseded_ids

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
            coords_map = {addr: get_census_coordinates(addr) for addr in unique_addrs}
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
            .apply(process_group, include_groups=False)
            .reset_index(drop=True)
        )

        # 5. Split cached vs. unenriched events so we only run LLM + BART-large on new records
        existing_map, superseded_ids = self.fetch_existing_enriched(
            grouped_df["id"].tolist(), grouped_df=grouped_df
        )
        self._last_superseded_ids = superseded_ids

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
            new_df = batch_mood_classifier(new_df, batch_size=8)

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
                    enriched_df["clean_description"].astype(str).str.strip() != "",
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

        records = transformed_df.replace({np.nan: None}).to_dict(orient="records")
        total_records = len(records)
        logger.info(
            f"Preparing to upsert {total_records} records into '{self.table_name}'..."
        )

        upserted_count = 0
        for i in range(0, total_records, batch_size):
            batch = records[i : i + batch_size]
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

    def cleanup_superseded_events(
        self, superseded_ids: List[str], chunk_size: int = 100
    ) -> int:
        """Removes stale event rows whose schedule window shifted and was replaced by a new ID."""
        unique_ids = sorted(set(i for i in superseded_ids if i))
        if not unique_ids:
            return 0

        deleted_count = 0
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i : i + chunk_size]
            try:
                self.client.table(self.table_name).delete().in_(
                    "id", chunk
                ).execute()
                deleted_count += len(chunk)
            except Exception as e:
                logger.warning(
                    f"Failed to delete superseded event IDs chunk {i}: {e}"
                )

        if deleted_count:
            logger.info(
                f"Removed {deleted_count} superseded event row(s) from '{self.table_name}'."
            )
        return deleted_count

    def run_standalone_pipeline(self, raw_df: pd.DataFrame) -> bool:
        try:
            logger.info("Starting standalone VM ETL Pipeline execution...")
            self._last_superseded_ids = []
            transformed_df = self.transform_raw_events(raw_df)
            if not transformed_df.empty:
                upserted = self.upsert_to_supabase(transformed_df)
                if upserted > 0 and self._last_superseded_ids:
                    self.cleanup_superseded_events(self._last_superseded_ids)
                return True
            else:
                logger.warning("Pipeline finished with zero records transformed.")
                return False
        except Exception as e:
            logger.critical(
                f"Standalone pipeline execution failed: {e}", exc_info=True
            )
            return False