# import required libraries
import os, sys
import json
import datetime
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load local .env file if executing on a developer machine
load_dotenv()

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Initialize the Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Define the structured output schema using Pydantic
class MoodVectors(BaseModel):
    energy: float = Field(
        ...,
        description="Rating from 0.0 (low/chill/meditative) to 1.0 (intense/high-decibel/active)",
    )
    social: float = Field(
        ...,
        description="Rating from 0.0 (solitary/intimate) to 1.0 (crowded/networking/party)",
    )
    novelty: float = Field(
        ...,
        description="Rating from 0.0 (routine/classic) to 1.0 (eccentric/experimental)",
    )


def extract_triaxial_vectors(
    title: str, description: str, category: str
) -> dict:
    """Uses Gemini 2.5 Flash to extract structured triaxial vectors."""
    prompt = f"""
    Analyze the following event and rate it on 3 distinct scale parameters from 0.0 to 1.0:
    - Energy (0.0 = low/chill/meditative, 1.0 = intense/high-decibel/active)
    - Social (0.0 = solitary/intimate, 1.0 = crowded/networking/party)
    - Novelty (0.0 = routine/classic, 1.0 = eccentric/experimental)

    Event Title: {title}
    Category: {category}
    Description: {description}
    """

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MoodVectors,
                temperature=0.1,
            ),
        )

        # Parse the structured JSON response
        data = json.loads(response.text)
        return {
            "energy": max(0.0, min(1.0, float(data.get("energy", 0.5)))),
            "social": max(0.0, min(1.0, float(data.get("social", 0.5)))),
            "novelty": max(0.0, min(1.0, float(data.get("novelty", 0.5)))),
        }
    except Exception as e:
        print(f"Gemini vector extraction error: {e}")
        return {"energy": 0.5, "social": 0.5, "novelty": 0.5}


def process_sample_feed():
    """Fetches raw events, processes them through the vector engine, and upserts to Supabase."""
    raw_events = [
        {
            "title": "Midnight Jazz & Ambient Saxophone",
            "venue": "The Velvet Cellar",
            "address": "404 Pine St",
            "description": "Candlelit late-night jazz session featuring improvised sax and soft acoustic rhythms.",
            "category": "Nightlife",
            "start_time": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=1)
            ).isoformat(),
        },
        {
            "title": "Underground Neon Warehouse Rave",
            "venue": "District 9 Silos",
            "address": "88 Industrial Way",
            "description": "High-BPM techno, laser arrays, and heavy bass lasting until sunrise.",
            "category": "Party",
            "start_time": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=2)
            ).isoformat(),
        },
    ]

    for item in raw_events:
        print(f"Processing: {item['title']}...")
        vectors = extract_triaxial_vectors(
            item["title"], item["description"], item["category"]
        )

        payload = {
            "title": item["title"],
            "venue": item["venue"],
            "address": item["address"],
            "description": item["description"],
            "category": item["category"],
            "start_time": item["start_time"],
            "energy_vector": vectors["energy"],
            "social_vector": vectors["social"],
            "novelty_vector": vectors["novelty"],
        }

        # Upsert into database using unique composite constraint
        res = (
            supabase.table("events")
            .upsert(payload, on_conflict="title, venue, start_time")
            .execute()
        )
        print(f"Upserted: {res.data}")


if __name__ == "__main__":
    process_sample_feed()