import re
from typing import List, Dict, Optional
from urllib.parse import urlparse
import difflib
from datetime import datetime

def clean_url(url: str) -> str:
    """
    Clean and validate a URL string by removing markdown formatting and extra characters.
    
    Args:
        url (str): The URL to clean
        
    Returns:
        str: Cleaned URL string
    """
    # Remove markdown formatting
    url = re.sub(r'\[.*?\]', '', url)  # Remove [...] parts
    url = re.sub(r'\(|\)', '', url)     # Remove parentheses
    url = url.strip()
    
    # Filter out data URLs and other unwanted schemes
    if url.startswith('data:') or not url.startswith(('http://', 'https://')):
        return ''
    
    # Handle relative URLs
    if url.startswith('/'):
        return url
    
    # Basic URL validation
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = 'https://' + url
        return url
    except Exception:
        return ''

def extract_unique_urls(text: str) -> list[str]:
    """
    Extracts unique URLs from a given text string.
    
    Args:
        text (str): The input text containing URLs
        
    Returns:
        list[str]: A list of unique URLs found in the text
    """
    # Match both markdown-style and plain URLs
    url_patterns = [
        r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s\)\]]*',  # Regular URLs
        r'\[.*?\]\((https?://[^\s\)]+)\)',  # Markdown-style URLs
    ]
    
    urls = []
    for pattern in url_patterns:
        matches = re.findall(pattern, text)
        urls.extend(matches)
    
    # Clean and validate URLs
    cleaned_urls = [clean_url(url) for url in urls]
    valid_urls = [url for url in cleaned_urls if url and is_valid_url(url)]
    
    # Remove duplicates while preserving order
    return list(dict.fromkeys(valid_urls))

def is_valid_url(url: str) -> bool:
    """
    Validates if a given URL is properly formatted and likely to be an event page.
    
    Args:
        url (str): URL to validate
        
    Returns:
        bool: True if URL is valid and likely an event page
    """
    try:
        # Filter out data URLs
        if url.startswith('data:'):
            return False
            
        parsed = urlparse(url)
        if not all([parsed.scheme, parsed.netloc]):
            return False
            
        # Filter out unwanted schemes
        if parsed.scheme not in ['http', 'https']:
            return False
            
        event_terms = [
            'conference', 'summit', 'symposium', 'event', 'expo',
            'meetup', 'forum', 'congress', 'convention', 'workshop',
            'ai-', 'ml-', 'tech-', 'artificial-intelligence'
        ]
        
        path_and_domain = (parsed.netloc + parsed.path).lower()
        
        # Check if URL contains event-related terms or year
        return any(term in path_and_domain for term in event_terms) or \
               '2025' in path_and_domain
               
    except Exception:
        return False

def clean_text(text: str) -> str:
    """
    Cleans and normalizes text content.
    
    Args:
        text (str): Text to clean
        
    Returns:
        str: Cleaned text
    """
    if not text:
        return ""
        
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # Remove extra whitespace
    text = ' '.join(text.split())
    
    # Remove special characters but keep basic punctuation
    text = re.sub(r'[^\w\s.,!?-]', '', text)
    
    return text.strip()

def standardize_date(date_str: str) -> str:
    """
    Standardizes date format.
    
    Args:
        date_str (str): Date string to standardize
        
    Returns:
        str: Standardized date string
    """
    month_map = {
        'jan': 'January', 'feb': 'February', 'mar': 'March',
        'apr': 'April', 'may': 'May', 'jun': 'June',
        'jul': 'July', 'aug': 'August', 'sep': 'September',
        'oct': 'October', 'nov': 'November', 'dec': 'December'
    }
    
    date_lower = date_str.lower()
    
    # Replace abbreviated months
    for abbr, full in month_map.items():
        if abbr in date_lower:
            date_str = date_str.replace(date_lower.split(abbr)[0] + abbr, full)
    
    # Standardize date separators
    date_str = re.sub(r'(\d+)[./](\d+)[./](2025)', r'\1-\2-\3', date_str)
    
    return date_str

def merge_event_data(existing_event: Dict, new_event: Dict) -> Dict:
    """
    Intelligently merges two event dictionaries, keeping the most complete and accurate information.
    
    Args:
        existing_event (Dict): Existing event data
        new_event (Dict): New event data to merge
        
    Returns:
        Dict: Merged event dictionary
    """
    merged = existing_event.copy()
    
    for key, new_value in new_event.items():
        if not new_value:  # Skip empty values
            continue
            
        existing_value = merged.get(key)
        
        if not existing_value:
            merged[key] = new_value
        else:
            # Handle different types of fields differently
            if key in ['speakers', 'topics', 'prices']:
                # Merge lists, remove duplicates while preserving order
                combined = existing_value + [x for x in new_value if x not in existing_value]
                merged[key] = combined
                
            elif key in ['description']:
                # Keep the longer description
                if len(new_value) > len(existing_value):
                    merged[key] = new_value
                    
            elif key in ['dates', 'location']:
                # Keep both if they're different
                if new_value != existing_value:
                    merged[key] = f"{existing_value}; {new_value}"
                    
            elif key == 'event_name':
                # Use string similarity to decide if names should be combined
                similarity = difflib.SequenceMatcher(None, existing_value, new_value).ratio()
                if similarity < 0.8:  # If names are significantly different
                    merged[key] = f"{existing_value} / {new_value}"
                    
            elif key == 'registration_url':
                # Keep the most recently updated registration URL
                merged[key] = new_value
    
    # Update last_updated timestamp
    merged['last_updated'] = datetime.now().isoformat()
    
    return merged

def extract_topics(text: str) -> List[str]:
    """
    Extracts relevant AI/ML topics from text.
    
    Args:
        text (str): Text to analyze
        
    Returns:
        List[str]: List of identified topics
    """
    topics = set()
    
    # Common AI/ML topics to look for
    topic_patterns = [
        r'(?:deep|machine|reinforcement|supervised|unsupervised)\s+learning',
        r'neural\s+networks?',
        r'computer\s+vision',
        r'natural\s+language\s+processing',
        r'nlp',
        r'artificial\s+intelligence',
        r'ai',
        r'robotics?',
        r'data\s+science',
        r'big\s+data',
        r'cloud\s+computing',
        r'edge\s+ai',
        r'generative\s+ai',
        r'large\s+language\s+models?',
        r'llms?',
        r'transformers?',
        r'automation',
        r'autonomous\s+systems?'
    ]
    
    # Find all matches
    for pattern in topic_patterns:
        matches = re.finditer(pattern, text.lower())
        for match in matches:
            # Capitalize first letter of each word
            topic = ' '.join(word.capitalize() for word in match.group().split())
            topics.add(topic)
    
    return sorted(list(topics))

def extract_prices(text: str) -> List[str]:
    """
    Extracts price information from text.
    
    Args:
        text (str): Text to analyze
        
    Returns:
        List[str]: List of price information found
    """
    prices = []
    
    # Price patterns
    patterns = [
        r'\$\d+(?:,\d{3})*(?:\.\d{2})?(?:\s*-\s*\$\d+(?:,\d{3})*(?:\.\d{2})?)?',  # USD
        r'€\d+(?:,\d{3})*(?:\.\d{2})?(?:\s*-\s*€\d+(?:,\d{3})*(?:\.\d{2})?)?',    # EUR
        r'£\d+(?:,\d{3})*(?:\.\d{2})?(?:\s*-\s*£\d+(?:,\d{3})*(?:\.\d{2})?)?',    # GBP
        r'\d+(?:,\d{3})*(?:\.\d{2})?\s*(?:USD|EUR|GBP)'  # Amount followed by currency code
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            price = match.group().strip()
            if price not in prices:
                prices.append(price)
    
    return prices