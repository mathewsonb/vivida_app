"""
pipeline_runner.py
Master ETL Orchestrator & Test Execution Suite for Vivida Event Aggregation.
Runs all 17 refactored scraper modules, collects metric telemetry, and executes
idempotent upserts directly into Supabase.
"""
import importlib
import logging
import os
import sys
import time
from typing import Dict, Any

import pandas as pd

# Import consolidated vivida_etl package
from vivida_etl import VividaIngestionPipeline

# Configure logging for master runner
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("vivida_pipeline_runner")

# Comprehensive list of all 17 scraper module targets
SCRAPER_REGISTRY = [
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
    {"name": "Point Defiance Zoo & Aquarium", "module": "scrapers.pointDefianceZoo"},
    {"name": "The Proctor District", "module": "scrapers.proctorDistrict"},
    {"name": "Puyallup Fairgrounds", "module": "scrapers.puyallupFairgrounds"},
    {"name": "Tacoma Art Museum", "module": "scrapers.tacomaArtMuseum"},
    {"name": "Tacoma Arts Live", "module": "scrapers.tacomaArtsLive"},
    {"name": "Tacoma Arts Monthly", "module": "scrapers.tacomaArtsMonth"},
    {"name": "Tacoma Comedy Club", "module": "scrapers.tacomaComedyClub"},
    {"name": "Tacoma Dome", "module": "scrapers.tacomaDome"},
    {"name": "Metro Parks Tacoma", "module": "scrapers.tacomaParksAndRec"},
    {"name": "Visit Tacoma / Pierce County", "module": "scrapers.tacomaPierceCountyEvents"},
    {"name": "Tacoma City Theaters", "module": "scrapers.tacomaTheaters"},
    {"name": "The Spar Tavern", "module": "scrapers.theSparTavern"},
    {"name": "The Valley Tacoma", "module": "scrapers.theValley"},
]

def run_pipeline_test() -> Dict[str, Any]:
    """Runs end-to-end execution across all registered scrapers and reports metrics."""
    logger.info("Starting master pipeline test execution for Supabase upsert verification...")
    start_time = time.time()
    
    pipeline = VividaIngestionPipeline(table_name="events")
    summary_results = []
    total_events_extracted = 0
    total_events_upserted = 0
    failed_scrapers = []

    for item in SCRAPER_REGISTRY:
        venue_name = item["name"]
        module_path = item["module"]
        logger.info(f"\n--- Running Task: {venue_name} ({module_path}) ---")
        
        task_start = time.time()
        extracted_count = 0
        status = "FAILED"
        
        try:
            # Dynamically import the scraper module
            mod = importlib.import_module(module_path)
            
            # Execute scraper function
            if hasattr(mod, "scraper"):
                raw_df = mod.scraper()
            elif hasattr(mod, "scrape"):
                raw_df = mod.scrape()
            else:
                raise AttributeError(f"Module '{module_path}' has no entry point function ('scraper' or 'scrape').")

            if isinstance(raw_df, pd.DataFrame) and not raw_df.empty:
                extracted_count = len(raw_df)
                total_events_extracted += extracted_count
                logger.info(f"Successfully scraped {extracted_count} raw events from {venue_name}.")

                # Hand-off raw data to autonomous ingestion pipeline
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
            logger.error(f"Execution error for {venue_name}: {e}", exc_info=True)
            status = f"EXCEPTION: {str(e)[:50]}"
            failed_scrapers.append(venue_name)

        elapsed = round(time.time() - task_start, 2)
        summary_results.append({
            "Venue": venue_name,
            "Status": status,
            "Records": extracted_count,
            "Duration (s)": elapsed
        })

    total_duration = round(time.time() - start_time, 2)
    
    # Render Execution Telemetry Summary
    logger.info("\n========================================================")
    logger.info("             MASTER PIPELINE EXECUTION SUMMARY          ")
    logger.info("========================================================")
    summary_df = pd.DataFrame(summary_results)
    logger.info(f"\n{summary_df.to_string(index=False)}")
    logger.info("--------------------------------------------------------")
    logger.info(f"Total Execution Time: {total_duration}s")
    logger.info(f"Total Raw Events Extracted: {total_events_extracted}")
    logger.info(f"Total Successful Ingestions: {total_events_upserted}")
    logger.info(f"Failed Scrapers Count: {len(failed_scrapers)}")
    logger.info("========================================================\n")

    return {
        "summary": summary_results,
        "total_duration": total_duration,
        "total_extracted": total_events_extracted,
        "failed_scrapers": failed_scrapers
    }

if __name__ == "__main__":
    # Check Supabase Environment Credentials
    supabase_url = os.environ.get("VITE_SUPABASE_URL")
    supabase_key = os.environ.get("VITE_SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        logger.warning(
            "VITE_SUPABASE_URL or VITE_SUPABASE_SERVICE_KEY environment variables missing. "
            "Pipeline will execute extraction logic, but database upserts may run in dry-run mode."
        )

    results = run_pipeline_test()
    if results["failed_scrapers"]:
        sys.exit(1)
    else:
        sys.exit(0)