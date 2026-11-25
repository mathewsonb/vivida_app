"""
pipeline_runner.py
Master ETL Orchestrator & Test Execution Suite for Vivida Event Aggregation.
Runs all 23 registered venue scrapers, collects metric telemetry, and executes
idempotent upserts directly into Supabase via vivida_etl.
"""
import argparse
import gc
import importlib
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

# ensure local modules are loaded when running locally
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def load_vivida_env() -> List[str]:
    """Load env vars with precedence: existing OS/Codespace secrets > .env.local > .env."""
    repo_root = Path(__file__).resolve().parent.parent
    loaded_files: List[str] = []

    for filename in (".env.local", ".env"):
        env_path = repo_root / filename
        if not env_path.is_file():
            continue

        try:
            from dotenv import load_dotenv

            # override=False ensures Codespaces/GitHub Actions secrets take precedence
            load_dotenv(dotenv_path=env_path, override=False)
        except ImportError:
            # Built-in fallback parser so .env.local works even without python-dotenv
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)

        loaded_files.append(str(env_path))

    return loaded_files

LOADED_ENV_FILES = load_vivida_env()

import pandas as pd

from vivida_etl import VividaIngestionPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("vivida_pipeline_runner")

# Master Scraper Target Registry (all 23 active scraper modules)
SCRAPER_REGISTRY: List[Dict[str, str]] = [
    {"name": "Emerald Queen Casino", "module": "scrapers.emeraldQueenCasino"},
    {"name": "Cider & Cedar", "module": "scrapers.ciderAndCedar"},
    {"name": "City of University Place", "module": "scrapers.cityOfUniversityPlace"},
    {"name": "Doyle's Pub", "module": "scrapers.doylesPublicHouse"},
    {"name": "Federal Way PAEC", "module": "scrapers.federalWayPerformingArtsCenter"},
    {"name": "Gig Harbor Events", "module": "scrapers.gigHarbor"},
    {"name": "Jazzbones", "module": "scrapers.jazzbones"},
    {"name": "McMenamins Elks Temple", "module": "scrapers.mcmenaminsElksTemple"},
    {"name": "New Frontier Lounge", "module": "scrapers.newFrontierLounge"},
    {"name": "Odd Otter Brewing", "module": "scrapers.oddOtterBrewing"},
    {"name": "Point Defiance Zoo & Aquarium",
        "module": "scrapers.pointDefianceZoo"},
    {"name": "The Proctor District", "module": "scrapers.proctorDistrict"},
    {"name": "Puyallup Fairgrounds", "module": "scrapers.puyallupFairgrounds"},
    {"name": "Tacoma Art Museum", "module": "scrapers.tacomaArtMuseum"},
    {"name": "Tacoma Arts Live", "module": "scrapers.tacomaArtsLive"},
    {"name": "Tacoma Arts Monthly", "module": "scrapers.tacomaArtsMonth"},
    {"name": "Tacoma Comedy Club", "module": "scrapers.tacomaComedyClub"},
    {"name": "Tacoma Dome", "module": "scrapers.tacomaDome"},
    {"name": "Metro Parks Tacoma", "module": "scrapers.tacomaParksAndRec"},
    {"name": "Visit Tacoma / Pierce County",
        "module": "scrapers.tacomaPierceCountyEvents"},
    {"name": "Tacoma City Theaters", "module": "scrapers.tacomaTheaters"},
    {"name": "The Spar Tavern", "module": "scrapers.theSparTavern"},
    {"name": "The Valley Tacoma", "module": "scrapers.theValley"},
]


def run_pipeline_test(
    only_filter: Optional[List[str]] = None,
    skip_filter: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Runs end-to-end execution across registered scrapers and reports metrics."""
    targets = SCRAPER_REGISTRY
    if only_filter:
        needles = [s.lower() for s in only_filter]
        targets = [
            item
            for item in targets
            if any(
                n in item["name"].lower() or n in item["module"].lower()
                for n in needles
            )
        ]
    if skip_filter:
        needles = [s.lower() for s in skip_filter]
        targets = [
            item
            for item in targets
            if not any(
                n in item["name"].lower() or n in item["module"].lower()
                for n in needles
            )
        ]

    logger.info(
        f"Starting master pipeline test execution across {len(targets)} registered scrapers"
        f"{' (DRY-RUN)' if dry_run else ''}..."
    )
    start_time = time.time()

    pipeline = None if dry_run else VividaIngestionPipeline(table_name="events")
    summary_results: List[Dict[str, Any]] = []
    total_events_extracted = 0
    total_events_upserted = 0
    failed_scrapers: List[str] = []

    for idx, item in enumerate(targets, start=1):
        venue_name = item["name"]
        module_path = item["module"]
        logger.info(
            f"\n--- [{idx}/{len(targets)}] Running Task: {venue_name} ({module_path}) ---"
        )

        task_start = time.time()
        extracted_count = 0
        status = "FAILED"

        try:
            mod = importlib.import_module(module_path)

            if hasattr(mod, "scraper"):
                raw_df = mod.scraper()
            elif hasattr(mod, "scrape"):
                raw_df = mod.scrape()
            else:
                raise AttributeError(
                    f"Module '{module_path}' has no entry point function ('scraper' or 'scrape')."
                )

            if isinstance(raw_df, pd.DataFrame) and not raw_df.empty:
                extracted_count = len(raw_df)
                total_events_extracted += extracted_count
                logger.info(
                    f"Successfully scraped {extracted_count} raw events from {venue_name}."
                )

                if dry_run:
                    status = "DRY_RUN_OK"
                    total_events_upserted += extracted_count
                else:
                    success = pipeline.run_standalone_pipeline(raw_df)
                    if success:
                        status = "SUCCESS"
                        total_events_upserted += extracted_count
                    else:
                        status = "UPSERT_ERROR"
                        failed_scrapers.append(venue_name)
            else:
                logger.warning(f"No events returned for {venue_name}.")
                status = "NO_DATA"

        except Exception as e:
            logger.error(
                f"Execution error for {venue_name}: {e}", exc_info=True)
            status = f"EXCEPTION: {str(e)[:50]}"
            failed_scrapers.append(venue_name)
        finally:
            gc.collect()

        elapsed = round(time.time() - task_start, 2)
        summary_results.append(
            {
                "Venue": venue_name,
                "Status": status,
                "Records": extracted_count,
                "Duration (s)": elapsed,
            }
        )

    total_duration = round(time.time() - start_time, 2)

    logger.info("\n========================================================")
    logger.info("              MASTER PIPELINE EXECUTION SUMMARY          ")
    logger.info("========================================================")
    summary_df = pd.DataFrame(summary_results)
    logger.info(f"\n{summary_df.to_string(index=False)}")
    logger.info("--------------------------------------------------------")
    logger.info(f"Total Execution Time: {total_duration}s")
    logger.info(f"Total Raw Events Extracted: {total_events_extracted}")
    logger.info(f"Total Successful Ingestions: {total_events_upserted}")
    logger.info(f"Failed Scrapers Count: {len(failed_scrapers)}")
    if failed_scrapers:
        logger.info(f"Failed Scrapers: {', '.join(failed_scrapers)}")
    logger.info("========================================================\n")

    return {
        "summary": summary_results,
        "total_duration": total_duration,
        "total_extracted": total_events_extracted,
        "failed_scrapers": failed_scrapers,
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Vivida Master ETL Pipeline Runner"
    )
    parser.add_argument(
        "--only",
        nargs="+",
        help="Run only scrapers matching these venue/module substrings.",
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        help="Skip scrapers matching these venue/module substrings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run scraping and validation without writing to Supabase.",
    )
    args = parser.parse_args()

    if LOADED_ENV_FILES:
        logger.info(f"Loaded local env fallback file(s): {', '.join(LOADED_ENV_FILES)}")

    supabase_url = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    supabase_key = (
        os.environ.get("VITE_SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    gemini_key = os.environ.get("VITE_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")

    effective_dry_run = args.dry_run
    if not supabase_url or not supabase_key:
        logger.warning(
            "Supabase credentials missing from environment and .env.local. "
            "Switching to --dry-run mode (extraction only; skipping Supabase upserts)."
        )
        effective_dry_run = True

    if not gemini_key:
        logger.info("Gemini API key not set in env/.env.local (local Ollama enrichment will be used if configured).")

    results = run_pipeline_test(
        only_filter=args.only,
        skip_filter=args.skip,
        dry_run=effective_dry_run,
    )
    sys.exit(1 if results["failed_scrapers"] else 0)