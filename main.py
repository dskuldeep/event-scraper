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
from navigation_agent import NavigationAgent

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
        # Maintain a persistent queue file
        self.QUEUE_FILE = "url_queue.json"
        self._load_url_queue()
        
        # Initialize webdriver
        self.driver = setup_webdriver()
    
    def _save_url_queue(self):
        """Save the current URL queue to a JSON file."""
        try:
            with open(self.QUEUE_FILE, 'w') as f:
                json.dump(list(self.url_manager.url_queue), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save URL queue: {str(e)}")

    def _load_url_queue(self):
        """Load the URL queue from a JSON file if it exists."""
        if os.path.exists(self.QUEUE_FILE):
            try:
                with open(self.QUEUE_FILE, 'r') as f:
                    urls = json.load(f)
                    for url in urls:
                        if url not in self.url_manager.url_queue:
                            self.url_manager.url_queue.append(url)
                logger.info(f"Loaded {len(urls)} URLs into queue from {self.QUEUE_FILE}")
            except Exception as e:
                logger.error(f"Failed to load URL queue: {str(e)}")

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
        time.sleep(2)  # Wait to avoid LLM rate limits
        
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
            text_content = soup.get_text(separator='\n', strip=True)
            links = [a.get('href') for a in soup.find_all('a', href=True)]
            # Use NavigationAgent to decide next action
            nav_agent = NavigationAgent()
            decision = nav_agent.decide(url, title, text_content, links)
            time.sleep(2)  # Wait to avoid LLM rate limits
            if decision["action"] == "click" and decision["links"]:
                # Add important links to the front of the queue (priority)
                for link in reversed(decision["links"]):
                    self.url_manager.url_queue.appendleft(link)
                logger.info(f"🔗 Navigation agent prioritized links: {decision['links']}")
                # Do not extract yet, just return (the prioritized links will be processed next)
                return
            # If action is extract, proceed as before
            self.doc_collector.add_document(
                url=url,
                page_source=page_source,
                title=title,
                metadata={"crawl_time": datetime.now().isoformat()}
            )
            unprocessed_docs = self.doc_collector.get_unprocessed_documents()
            if unprocessed_docs:
                doc = unprocessed_docs[0]
                try:
                    self.event_processor.process_documents()
                    time.sleep(2)  # Wait to avoid LLM rate limits
                except Exception as e:
                    logger.error(f"Extraction error for {url}: {str(e)}")
            self._mark_crawled_link(url)
            self._save_url_queue()
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
        max_links = 1000  # Set to a lower number for debugging
        try:
            for region, keyword in itertools.product(REGIONS, SEARCH_KEYWORDS):
                if link_count >= max_links:
                    logger.info(f"🔢 Link limit ({max_links}) reached. Stopping crawl for debugging.")
                    break
                query = f"{keyword} in {region}"
                urls = self.search_events(query)
                self.url_manager.add_urls(urls)
                self._save_url_queue()
                while self.url_manager.has_urls() and link_count < max_links:
                    url = self.url_manager.get_next_url()
                    if url:
                        self.process_url_and_extract(url, depth=0, max_depth=2)
                        link_count += 1
                        logger.info(f"🔢 Processed {link_count}/{max_links} links.")
                        time.sleep(1)  # Rate limiting
        finally:
            self._save_url_queue()
            self.driver.quit()

if __name__ == "__main__":
    try:
        finder = EventFinder()
        finder.run()
    except Exception as e:
        logger.error(f"Fatal error in event finder: {str(e)}")
        raise
