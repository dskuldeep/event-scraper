"""Web tools for navigating and extracting event information."""

import time
from typing import Dict, List, Optional
import json
from datetime import datetime
import logging
import re
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from urllib.parse import urljoin, urlparse

from constants import EVENT_SCHEMA, OUTPUT_FILE
from utils import logger

def setup_webdriver() -> webdriver.Chrome:
    """Initialize and return a configured Chrome webdriver (headless by default)."""
    options = Options()
    options.add_argument("--headless=new")  # Headless mode for Chrome 109+
    options.add_argument("--window-size=1920x1080")
    options.add_argument("--start-maximized")
    options.add_experimental_option("detach", True)
    # Add logging preferences
    options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver

def is_valid_url(url: str) -> bool:
    """Check if URL is valid and absolute."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

def make_absolute_url(base_url: str, relative_url: str) -> str:
    """Convert relative URL to absolute URL."""
    return urljoin(base_url, relative_url)

def visit_webpage(url: str, driver: webdriver.Chrome) -> Optional[str]:
    """
    Visit webpage and return rendered content.
    Handles dynamic content and scrolling.
    """
    from functions import clean_url  # Import here to avoid circular imports
    
    url = clean_url(url)
    if not url:
        logger.warning(f"Invalid URL: {url}")
        return None
        
    try:
        logger.info(f"🌐 Visiting: {url}")
        driver.get(url)
        
        # Show active URL in browser title
        driver.execute_script(f'document.title = "Scanning: {url}"')
        
        # Wait for body with exponential backoff
        for i in range(3):
            try:
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                break
            except TimeoutException:
                if i < 2:
                    time.sleep(2 ** i)
        
        # Scroll to reveal dynamic content
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
            
        # Wait for dynamic content to load
        time.sleep(2)
        
        return driver.page_source
        
    except Exception as e:
        logger.error(f"Error accessing {url}: {str(e)}")
        return None

def merge_event_info(base_event: Dict, new_event: Dict) -> Dict:
    """
    Merge information from two event dictionaries.
    
    Args:
        base_event: The base event dictionary
        new_event: The new event dictionary to merge
    
    Returns:
        Dict: Merged event dictionary
    """
    for key, value in new_event.items():
        if key in base_event and base_event[key]:
            continue
        base_event[key] = value
    return base_event

def extract_event_info(page_source: str, url: str, region: str, driver: webdriver.Chrome, depth: int = 0, visited_urls=None) -> Optional[Dict]:
    """
    Extract event information recursively from the page source.
    
    Args:
        page_source: HTML content of the page
        url: Source URL
        region: Region where the event is located
        depth: Current depth of recursion
        visited_urls: Set of already visited URLs
        
    Returns:
        Dict containing event information or None if extraction fails
    """
    if visited_urls is None:
        visited_urls = set()
    
    if depth > 2 or url in visited_urls:  # Limit recursion depth
        return None
        
    visited_urls.add(url)
    
    soup = BeautifulSoup(page_source, 'lxml')
    event_data = EVENT_SCHEMA.copy()
    event_data["url"] = url
    event_data["region"] = region
    event_data["last_updated"] = datetime.now().isoformat()
    
    # Extract event name (look for common heading patterns)
    title_candidates = [
        soup.find('h1'),
        soup.find('meta', {'property': 'og:title'}),
        soup.find('title')
    ]
    
    for candidate in title_candidates:
        if candidate:
            if candidate.string:
                event_data["event_name"] = candidate.string.strip()
                break
            elif candidate.get('content'):
                event_data["event_name"] = candidate.get('content').strip()
                break
    
    # Extract dates (look for date patterns)
    date_patterns = [
        r'\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+2025',
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+2025',
        r'\d{1,2}/\d{1,2}/2025'
    ]
    
    text_content = soup.get_text()
    for pattern in date_patterns:
        dates = re.findall(pattern, text_content)
        if dates:
            event_data["dates"] = ', '.join(dates)
            break
    
    # Extract description
    desc_candidates = [
        soup.find('meta', {'name': 'description'}),
        soup.find('meta', {'property': 'og:description'}),
        soup.find(class_=re.compile(r'description|about|overview', re.I))
    ]
    
    for candidate in desc_candidates:
        if candidate:
            if candidate.string:
                event_data["description"] = candidate.string.strip()
                break
            elif candidate.get('content'):
                event_data["description"] = candidate.get('content').strip()
                break
    
    # Extract speakers
    speaker_sections = soup.find_all(class_=re.compile(r'speaker|presenter|keynote', re.I))
    speakers = []
    for section in speaker_sections:
        speaker_names = section.find_all('h3') + section.find_all('h4')
        for name in speaker_names:
            if name.string and len(name.string.strip()) > 0:
                speakers.append(name.string.strip())
    
    event_data["speakers"] = speakers
    
    # Extract location
    location_patterns = [
        r'(?:Location|Venue|Place):\s*([^.|\n]+)',
        r'(?:held|taking place) (?:at|in)\s*([^.|\n]+)'
    ]
    
    for pattern in location_patterns:
        location_match = re.search(pattern, text_content, re.I)
        if location_match:
            event_data["location"] = location_match.group(1).strip()
            break
    
    # Only return if we found at least a name and either dates or location
    if not (event_data["event_name"] and (event_data["dates"] or event_data["location"])):
        return None
    
    # Find and follow relevant links
    related_links = []
    for link in soup.find_all('a', href=True):
        href = link['href']
        if any(term in href.lower() for term in ['agenda', 'schedule', 'speakers', 'details', 'register']):
            absolute_url = make_absolute_url(url, href)
            if absolute_url not in visited_urls:
                related_links.append(absolute_url)
    
    # Recursively extract information from related pages
    for related_url in related_links[:3]:  # Limit to 3 related links per page
        try:
            related_page = visit_webpage(related_url, driver)
            if related_page:
                related_info = extract_event_info(related_page, related_url, region, driver, depth + 1, visited_urls)
                if related_info:
                    # Merge information from related pages
                    event_data = merge_event_info(event_data, related_info)
        except Exception as e:
            logger.error(f"Error following related link {related_url}: {str(e)}")
            
    return event_data

def save_event_data(events: List[Dict]):
    """
    Save event data to a JSON file with deduplication.
    
    Args:
        events: List of event dictionaries to save
    """
    try:
        # Load existing data if file exists
        try:
            with open(OUTPUT_FILE, 'r') as f:
                existing_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            existing_data = []
        
        # Update existing data with new events
        for event in events:
            # Check if event already exists (based on URL)
            existing_idx = next((i for i, e in enumerate(existing_data) 
                               if e["url"] == event["url"]), None)
            
            if existing_idx is not None:
                # Update existing event if new data has more information
                if len(str(event)) > len(str(existing_data[existing_idx])):
                    existing_data[existing_idx].update(event)
            else:
                # Add new event
                existing_data.append(event)
        
        # Save updated data
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(existing_data, f, indent=2)
            
    except Exception as e:
        logger.error(f"Error saving event data: {str(e)}")
        raise