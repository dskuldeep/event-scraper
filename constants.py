"""Constants and configuration for the event finder system."""

import os
from datetime import datetime
from pathlib import Path
import dotenv

dotenv.load_dotenv()

# Agent Configuration
APP_NAME = "event_finder_agent"
USER_ID = "user1234"
SESSION_ID = "1234"
MODEL = os.getenv("MODEL", "gemini-pro")

# Search Configuration
REGIONS = [
    "United States", "India", "Europe", "Dubai", 
    "Singapore", "United Kingdom", "Canada", "Australia",
    "Germany", "France", "Japan", "South Korea"
]

# Search Keywords
SEARCH_KEYWORDS = [
    # Primary keywords
    "AI conferences 2025",
    "artificial intelligence events 2025",
    "machine learning conferences 2025",
    "data science conferences 2025",
    "tech conferences 2025",
    
    # Specialized keywords
    "deep learning symposium 2025",
    "AI summit 2025",
    "neural networks conference 2025",
    "artificial intelligence summit 2025",
    "robotics conference 2025",
    "computer vision conference 2025",
    "NLP conference 2025",
    
    # Industry-specific
    "AI in healthcare conference 2025",
    "AI in finance symposium 2025",
    "AI manufacturing conference 2025",
    "enterprise AI summit 2025",
    
    # Regional variations
    "international AI conference 2025",
    "global AI summit 2025",
    "regional tech conference 2025"
]

# Web Navigation Settings
MAX_DEPTH = 3  # Maximum recursion depth for following links
MAX_RELATED_LINKS = 5  # Maximum number of related links to follow per page
MIN_INTERVAL = 2  # Minimum interval between requests in seconds
REQUEST_TIMEOUT = 30  # Timeout for web requests in seconds
MAX_RETRIES = 3  # Maximum number of retries for failed requests

# File and Directory Settings
OUTPUT_DIR = Path("data")
OUTPUT_FILE = OUTPUT_DIR / "events.json"
LOG_FILE = "event_finder.log"

# Ensure output directory exists
OUTPUT_DIR.mkdir(exist_ok=True)

# Event Schema
EVENT_SCHEMA = {
    "event_name": None,
    "dates": {
        "start": None,
        "end": None,
        "timezone": None
    },
    "location": {
        "venue": None,
        "city": None,
        "state": None,
        "country": None,
        "virtual": False,
        "hybrid": False
    },
    "description": None,
    "topics": [],
    "registration": {
        "url": None,
        "deadline": None,
        "prices": []
    },
    "speakers": [],
    "organizer": {
        "name": None,
        "website": None,
        "contact_email": None
    },
    "source_url": None,
    "last_updated": None
}