"""Main implementation of the event finder system."""

import os
import json
from typing import List, Dict, Any, Optional
import tenacity
import time
from datetime import datetime
import itertools
from bs4 import BeautifulSoup
from google.genai import types

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from constants import (
    APP_NAME, USER_ID, SESSION_ID, MODEL,
    REGIONS, SEARCH_KEYWORDS
)
from functions import extract_unique_urls, clean_text, standardize_date
from web_tools import setup_webdriver, visit_webpage
from utils import (
    logger,
    log_agent_action,
    EventFinderError,
    URLExtractionError,
    EventExtractionError,
    EventDataStore,
    URLManager,
    DocumentCollector,
    EventProcessor
)
from prompts import SEARCH_AGENT_PROMPT, EXTRACTION_AGENT_PROMPT

CRAWLED_LINKS_FILE = "crawled_links.json"

class EventFinder:
    def __init__(self):
        # Initialize services
        self.session_service = InMemorySessionService()
        self.session = self.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID
        )
        
        # Initialize agents
        self.search_agent = Agent(
            name="search_agent",
            model=MODEL,
            description="Agent to find event URLs",
            instruction=SEARCH_AGENT_PROMPT
        )
        
        self.extraction_agent = Agent(
            name="extraction_agent",
            model=MODEL,
            description="Agent to extract event information",
            instruction=EXTRACTION_AGENT_PROMPT
        )
        
        # Initialize runners
        self.search_runner = Runner(
            agent=self.search_agent,
            app_name=APP_NAME,
            session_service=self.session_service
        )
        
        self.extraction_runner = Runner(
            agent=self.extraction_agent,
            app_name=APP_NAME,
            session_service=self.session_service
        )
        
        # Initialize components
        self.event_store = EventDataStore()
        self.url_manager = URLManager()
        self.doc_collector = DocumentCollector()
        self.event_processor = EventProcessor(self.doc_collector, self.event_store)
        
        # Initialize webdriver
        self.driver = setup_webdriver()
    
    def search_events(self, query: str) -> List[str]:
        """Use search agent to find event URLs."""
        logger.info(f"🔍 Searching for: {query}")
        
        content = types.Content(
            role='user',
            parts=[types.Part(text=query)]
        )
        
        urls = []
        response = self.search_runner.run(
            user_id=USER_ID,
            session_id=SESSION_ID,
            new_message=content
        )
        
        for msg in response:
            if msg.is_final_response():
                urls.extend(extract_unique_urls(msg.content.parts[0].text))
        
        return urls
    
    def process_url_and_extract(self, url: str, depth: int = 0, max_depth: int = 2) -> None:
        """Visit a URL, collect its content, extract event info, and recursively process new event links."""
        if depth > max_depth or not url:
            return
        try:
            page_source = visit_webpage(url, self.driver)
            if not page_source:
                return
            soup = BeautifulSoup(page_source, 'lxml')
            title = soup.title.string if soup.title else ""
            # Add to document collector
            self.doc_collector.add_document(
                url=url,
                page_source=page_source,
                title=title,
                metadata={"crawl_time": datetime.now().isoformat()}
            )
            # Extract and store event info immediately (for this link only)
            unprocessed_docs = self.doc_collector.get_unprocessed_documents()
            if unprocessed_docs:
                doc = unprocessed_docs[0]
                try:
                    self.event_processor.process_documents()
                except Exception as e:
                    logger.error(f"Extraction error for {url}: {str(e)}")
            # Mark this link as crawled in a separate JSON file
            self._mark_crawled_link(url)
            # Find and process additional event URLs on the page (breadth-first)
            new_links = []
            for link in soup.find_all('a', href=True):
                href = link['href']
                if any(term in href.lower() for term in ['2025', 'conference', 'event', 'summit']):
                    self.url_manager.add_urls([href])
                    new_links.append(href)
            for new_url in new_links:
                next_url = self.url_manager.get_next_url()
                if next_url:
                    self.process_url_and_extract(next_url, depth=depth+1, max_depth=max_depth)
        except Exception as e:
            logger.error(f"Error processing URL {url}: {str(e)}")

    def _mark_crawled_link(self, url: str):
        """Append the crawled link to crawled_links.json for debugging and deduplication."""
        try:
            crawled = set()
            if os.path.exists(CRAWLED_LINKS_FILE):
                with open(CRAWLED_LINKS_FILE, 'r') as f:
                    try:
                        crawled = set(json.load(f))
                    except Exception:
                        crawled = set()
            crawled.add(url)
            with open(CRAWLED_LINKS_FILE, 'w') as f:
                json.dump(list(crawled), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to update {CRAWLED_LINKS_FILE}: {str(e)}")

    def run(self):
        """Main event finding loop with recursive extraction and a 100-link limit for debugging."""
        link_count = 0
        max_links = 100  # Set to a lower number for debugging
        try:
            for region, keyword in itertools.product(REGIONS, SEARCH_KEYWORDS):
                if link_count >= max_links:
                    logger.info(f"🔢 Link limit ({max_links}) reached. Stopping crawl for debugging.")
                    break
                query = f"{keyword} in {region}"
                urls = self.search_events(query)
                self.url_manager.add_urls(urls)
                while self.url_manager.has_urls() and link_count < max_links:
                    url = self.url_manager.get_next_url()
                    if url:
                        self.process_url_and_extract(url, depth=0, max_depth=2)
                        link_count += 1
                        logger.info(f"🔢 Processed {link_count}/{max_links} links.")
                        time.sleep(1)  # Rate limiting
        finally:
            self.driver.quit()

if __name__ == "__main__":
    try:
        finder = EventFinder()
        finder.run()
    except Exception as e:
        logger.error(f"Fatal error in event finder: {str(e)}")
        raise
