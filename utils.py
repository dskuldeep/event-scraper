"""Utility functions for logging and error handling."""

import logging
import json
from datetime import datetime
from typing import Any, Dict, Optional, List, Set
from pathlib import Path
import time
from collections import deque
from threading import Lock
from bs4 import BeautifulSoup
from google.genai import types
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
import uuid

from constants import (
    APP_NAME, USER_ID, SESSION_ID, MODEL,
    OUTPUT_DIR, OUTPUT_FILE
)
from prompts import EXTRACTION_AGENT_PROMPT, NAVIGATION_AGENT_PROMPT
from navigation_agent import NavigationAgent

# Configure logging with colored output
import logging.handlers
import coloredlogs

# Configure logging
coloredlogs.install(
    level=logging.INFO,
    fmt='%(asctime)s [%(levelname)s] %(message)s',
    level_styles={
        'debug': {'color': 'green'},
        'info': {'color': 'white'},
        'warning': {'color': 'yellow'},
        'error': {'color': 'red'},
        'critical': {'color': 'red', 'bold': True},
    }
)

# Set up file handler
file_handler = logging.FileHandler('event_finder.log')
file_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
)

# Create logger
logger = logging.getLogger('event_finder')
logger.addHandler(file_handler)

class EventFinderError(Exception):
    """Base exception class for event finder errors."""
    pass

class URLExtractionError(EventFinderError):
    """Raised when there's an error extracting URLs."""
    pass

class EventExtractionError(EventFinderError):
    """Raised when there's an error extracting event information."""
    pass

class EventDataStore:
    """Handles storage and retrieval of event data as separate files."""
    def __init__(self, folder: str = "events"):
        self.folder = Path(folder)
        self.folder.mkdir(exist_ok=True)
        self.data_lock = Lock()
    def store_event(self, event_data: Dict[str, Any]) -> None:
        """Store each event as a new incremented JSON file in the events folder."""
        with self.data_lock:
            try:
                # Find the next available eventN.json
                existing = sorted([int(f.stem.replace('event', '')) for f in self.folder.glob('event*.json') if f.stem.replace('event', '').isdigit()])
                next_num = (existing[-1] + 1) if existing else 1
                filename = self.folder / f"event{next_num}.json"
                with open(filename, 'w') as f:
                    json.dump(event_data, f, indent=2)
                logger.info(f"💾 Stored event: {event_data.get('event_name', 'Unknown Event')} in {filename}")
            except Exception as e:
                logger.error(f"Error storing event data: {str(e)}")
                raise
    def get_stored_urls(self) -> Set[str]:
        urls = set()
        for file in self.folder.glob('event*.json'):
            try:
                with open(file, 'r') as f:
                    event = json.load(f)
                    url = event.get('source_url') or event.get('url')
                    if url:
                        urls.add(url)
            except Exception:
                continue
        return urls

class URLManager:
    """Manages URL discovery and tracking."""
    
    def __init__(self):
        self.discovered_urls = set()
        self.processed_urls = set()
        self.url_queue = deque()
        
    def has_seen_url(self, url: str) -> bool:
        """Return True if the URL has already been discovered or processed."""
        return url in self.discovered_urls or url in self.processed_urls

    def add_urls(self, urls: List[str]) -> None:
        """Add new URLs to be processed."""
        from functions import clean_url  # Import here to avoid circular imports
        
        for url in urls:
            cleaned_url = clean_url(url)
            if cleaned_url and not self.has_seen_url(cleaned_url):
                self.discovered_urls.add(cleaned_url)
                self.url_queue.append(cleaned_url)
                logger.info(f"🔗 Added URL to queue: {cleaned_url}")
                
    def get_next_url(self) -> Optional[str]:
        """Get next URL to process."""
        while self.url_queue:
            url = self.url_queue.popleft()
            if url not in self.processed_urls:
                self.processed_urls.add(url)
                return url
        return None
        
    def has_urls(self) -> bool:
        """Check if there are URLs to process."""
        return bool(self.url_queue)

class EventDocument:
    """Temporary storage for crawled event information before processing."""
    
    def __init__(self, url: str):
        self.url = url
        self.raw_content = ""
        self.page_title = ""
        self.extracted_text = ""
        self.related_links = []
        self.crawl_timestamp = datetime.now().isoformat()
        self.metadata = {}

class DocumentCollector:
    """Manages collection of crawled documents."""
    
    def __init__(self):
        self.documents = {}  # URL -> EventDocument
        self.processed_urls = set()
        self.doc_lock = Lock()
    
    def add_document(self, url: str, page_source: str, title: str = "", metadata: Dict = None) -> None:
        """Add a new document from crawled page."""
        with self.doc_lock:
            if url not in self.documents:
                doc = EventDocument(url)
                doc.raw_content = page_source
                doc.page_title = title
                doc.metadata = metadata or {}
                self.documents[url] = doc
                logger.info(f"📄 Added document for: {url}")
    
    def get_unprocessed_documents(self) -> List[EventDocument]:
        """Get documents that haven't been processed yet."""
        with self.doc_lock:
            unprocessed = [
                doc for url, doc in self.documents.items() 
                if url not in self.processed_urls
            ]
            return unprocessed
    
    def mark_processed(self, url: str) -> None:
        """Mark a document as processed."""
        with self.doc_lock:
            self.processed_urls.add(url)
            if url in self.documents:
                del self.documents[url]

class EventProcessor:
    """Processes collected documents into structured event data."""
    
    def __init__(self, collector: DocumentCollector, store: EventDataStore):
        self.collector = collector
        self.store = store
        self.processing_lock = Lock()
        
        # Initialize extraction agent
        self.extraction_agent = Agent(
            name="extraction_agent",
            model=MODEL,
            description="Agent to extract event information",
            instruction=EXTRACTION_AGENT_PROMPT
        )
        
        self.session_service = InMemorySessionService()
        self.session_id = str(uuid.uuid4())
        self.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=self.session_id
        )
        
        self.extraction_runner = Runner(
            agent=self.extraction_agent,
            app_name=APP_NAME,
            session_service=self.session_service
        )
        
    def process_documents(self) -> None:
        """Process all unprocessed documents. Supports multiple events per document."""
        with self.processing_lock:
            documents = self.collector.get_unprocessed_documents()
            for doc in documents:
                try:
                    soup = BeautifulSoup(doc.raw_content, 'lxml')
                    text_content = soup.get_text(separator='\n', strip=True)
                    links = [a.get('href') for a in soup.find_all('a', href=True)]
                    logger.info(f"\n--- Scraped Website Context (First 500 chars) ---\n{text_content[:500]}\n--- End Context ---\n")
                    navigation_agent = NavigationAgent()
                    decision = navigation_agent.decide(doc.url, doc.page_title, text_content, links)
                    if decision["action"] == "click" and decision["links"]:
                        logger.info(f"🔗 Navigation agent decided to click links: {decision['links']}")
                        url_manager = URLManager()
                        url_manager.add_urls(decision["links"])
                        continue
                    prompt = f"""
                    Extract event information from this webpage content:
                    URL: {doc.url}
                    Title: {doc.page_title}
                    Content (plain text):
                    {text_content}
                    Return the information in the exact JSON schema as specified. Only include fields where information was found, use null for missing fields.
                    """
                    content = types.Content(
                        role='user',
                        parts=[types.Part(text=prompt)]
                    )
                    events_data = self._extract_event_info(content)
                    if events_data:
                        for event_data in events_data:
                            if self._has_meaningful_fields(event_data):
                                event_data['source_url'] = doc.url
                                event_data['last_updated'] = datetime.now().isoformat()
                                self.store.store_event(event_data)
                                logger.info(f"📝 Extracted event: {event_data.get('event_name', 'Unknown Event')} from {doc.url}")
                            else:
                                logger.warning(f"⚠️ Skipped saving event for {doc.url} (all fields null/empty or invalid JSON)")
                    else:
                        logger.warning(f"⚠️ No valid events extracted for {doc.url}")
                    self.collector.mark_processed(doc.url)
                except Exception as e:
                    logger.error(f"Error processing document {doc.url}: {str(e)}")
    
    def _extract_event_info(self, content: types.Content) -> Optional[List[Dict]]:
        """Use extraction agent to parse event information. Supports multiple events."""
        try:
            response = self.extraction_runner.run(
                user_id=USER_ID,
                session_id=self.session_id,
                new_message=content
            )
            for msg in response:
                if msg.is_final_response():
                    raw = msg.content.parts[0].text
                    # Try to extract/fix JSON from LLM output (support multiple events)
                    json_blocks = self._extract_json_blocks(raw)
                    all_events = []
                    for json_str in json_blocks:
                        try:
                            event_data = json.loads(json_str)
                            if isinstance(event_data, dict):
                                all_events.append(event_data)
                            elif isinstance(event_data, list):
                                all_events.extend([e for e in event_data if isinstance(e, dict)])
                        except Exception as e:
                            logger.warning(f"⚠️ JSON parse error: {e}\nRaw output: {json_str}")
                            continue
                    return all_events if all_events else None
            return None
        except Exception as e:
            logger.error(f"Extraction error: {str(e)}")
            return None

    def _extract_json_blocks(self, text: str) -> List[str]:
        """Extract all JSON objects or arrays from text."""
        import re
        import json
        
        def balance_braces(json_str: str) -> str:
            """Balance any unmatched braces in JSON string."""
            open_count = json_str.count('{')
            close_count = json_str.count('}')
            open_array = json_str.count('[')
            close_array = json_str.count(']')
            
            # Add missing closing braces/brackets
            json_str = json_str.rstrip()
            if open_count > close_count:
                json_str += '}' * (open_count - close_count)
            if open_array > close_array:
                json_str += ']' * (open_array - close_array)
            
            return json_str
        
        def fix_truncated_array(json_str: str) -> str:
            """Fix truncated arrays in JSON string by completing them."""
            if '"speakers": [' in json_str and not '],' in json_str:
                # Find the last complete speaker object
                last_complete = json_str.rfind('},')
                if last_complete > -1:
                    # Keep everything up to last complete object and close arrays/objects
                    json_str = json_str[:last_complete+1] + ']}'
            return json_str

        # First try to match complete arrays
        array_match = re.search(r'\[\s*\{[\s\S]*?\}\s*\]', text)
        if array_match:
            json_str = array_match.group(0)
            try:
                # Verify if it's valid JSON
                json.loads(json_str)
                return [json_str]
            except json.JSONDecodeError:
                # Try to fix and validate
                fixed = balance_braces(fix_truncated_array(json_str))
                try:
                    json.loads(fixed)
                    return [fixed]
                except json.JSONDecodeError:
                    pass

        # If no valid array found, try to extract individual complete objects
        object_matches = re.finditer(r'\{\s*"event_name"[\s\S]*?\}(?=\s*[,\]])', text)
        objects = []
        
        for match in object_matches:
            try:
                json_str = match.group(0)
                # Fix any truncated arrays within the object
                fixed = balance_braces(fix_truncated_array(json_str))
                json.loads(fixed)  # Validate JSON
                objects.append(fixed)
            except json.JSONDecodeError:
                continue
        
        if objects:
            # Wrap multiple objects in an array
            if len(objects) > 1:
                return [f"[{','.join(objects)}]"]
            return objects
        
        return []

    def _has_meaningful_fields(self, event: Dict) -> bool:
        """Check if event has at least a name and more event-specific fields."""
        # Must have event_name
        if not event.get('event_name'):
            return False
            
        # Check if this looks like a speaker object
        if all(key in event for key in ['name', 'title', 'organization']) and len(event) <= 5:
            return False
            
        # Must have at least one of these event-specific fields
        event_specific_fields = ['dates', 'location', 'description', 'topics', 'registration']
        if not any(event.get(field) for field in event_specific_fields):
            return False
            
        return True

def log_agent_action(
    agent_name: str,
    action: str,
    status: str,
    details: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log agent actions with relevant details and visual progress indicators.
    
    Args:
        agent_name: Name of the agent performing the action
        action: Description of the action
        status: Status of the action (success/failure)
        details: Additional details about the action
    """
    details = details or {}
    
    # Create visual indicators
    if action == "url_extraction":
        icon = "🔍" if status == "success" else "❌"
    elif action == "event_extraction":
        icon = "📅" if status == "success" else "⚠️"
    elif action == "save_checkpoint":
        icon = "💾" if status == "success" else "❗"
    else:
        icon = "ℹ️"
    
    # Add progress information
    if details.get("url"):
        logger.info(f"{icon} [{agent_name}] Processing: {details['url']}")
    
    if details.get("event_name"):
        logger.info(f"{icon} [{agent_name}] Found event: {details['event_name']}")
    
    if details.get("region") and details.get("keyword"):
        logger.info(f"{icon} [{agent_name}] Searching {details['region']} for '{details['keyword']}'")
    
    # Log the full entry for debugging
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "agent": agent_name,
        "action": action,
        "status": status,
        "details": details
    }
    
    logger.debug(json.dumps(log_entry))

def save_checkpoint(data: Dict[str, Any], checkpoint_name: str) -> None:
    """
    Save agent progress checkpoint with visual feedback.
    
    Args:
        data: Data to save
        checkpoint_name: Name of the checkpoint
    """
    checkpoint_dir = Path("checkpoints")
    checkpoint_dir.mkdir(exist_ok=True)
    
    checkpoint_file = checkpoint_dir / f"{checkpoint_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    with open(checkpoint_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    logger.info(f"💾 Saved checkpoint: {checkpoint_file.name}")
    
    # Save a progress summary
    summary = {
        "timestamp": datetime.now().isoformat(),
        "urls_visited": len(data.get("visited_urls", [])),
        "keywords_discovered": len(data.get("discovered_keywords", [])),
        "regions_completed": len(data.get("visited_regions", []))
    }
    
    summary_file = checkpoint_dir / "search_progress_summary.json"
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)
        
def load_latest_checkpoint(checkpoint_name: str) -> Optional[Dict[str, Any]]:
    """
    Load the latest checkpoint for recovery with visual feedback.
    
    Args:
        checkpoint_name: Name of the checkpoint to load
        
    Returns:
        Dict containing checkpoint data if found, None otherwise
    """
    checkpoint_dir = Path("checkpoints")
    if not checkpoint_dir.exists():
        return None
        
    checkpoints = list(checkpoint_dir.glob(f"{checkpoint_name}_*.json"))
    if not checkpoints:
        return None
        
    latest_checkpoint = max(checkpoints, key=lambda p: p.stat().st_mtime)
    
    with open(latest_checkpoint, 'r') as f:
        data = json.load(f)
        
    logger.info(f"📂 Loaded checkpoint: {latest_checkpoint.name}")
    return data

def get_search_progress() -> Dict[str, Any]:
    """
    Get current search progress statistics.
    
    Returns:
        Dict containing progress statistics
    """
    checkpoint_dir = Path("checkpoints")
    summary_file = checkpoint_dir / "search_progress_summary.json"
    
    if summary_file.exists():
        with open(summary_file, 'r') as f:
            return json.load(f)
    
    return {
        "timestamp": datetime.now().isoformat(),
        "urls_visited": 0,
        "keywords_discovered": 0,
        "regions_completed": 0
    }