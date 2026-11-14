import re
import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

AU_STATES = {
    "nsw": "NSW", "new south wales": "NSW",
    "vic": "VIC", "victoria": "VIC",
    "qld": "QLD", "queensland": "QLD",
    "wa": "WA", "western australia": "WA",
    "sa": "SA", "south australia": "SA",
    "act": "ACT", "australian capital territory": "ACT",
    "tas": "TAS", "tasmania": "TAS",
    "nt": "NT", "northern territory": "NT",
}

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "tracking", "fbclid", "gclid", "src", "cid"
}

def normalize_url(raw_url: str) -> str:
    """Removes tracking query parameters, fragments, and standardizes trailing slashes."""
    parsed = urlparse(raw_url.strip())
    # Filter out tracking query params
    filtered_query = [
        (k, v) for k, v in parse_qsl(parsed.query)
        if k.lower() not in TRACKING_PARAMS
    ]
    # Standardize path
    path = parsed.path.rstrip("/")
    normalized = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        parsed.params,
        urlencode(filtered_query),
        ""  # drop fragment
    ))
    return normalized

def normalize_location(location_str: str) -> str:
    """Standardizes Australian locations to 'City, STATE' format."""
    loc = location_str.strip()
    if not loc:
        return "Australia"
    
    # Check for state match
    for state_key, state_code in AU_STATES.items():
        pattern = rf"\b{re.escape(state_key)}\b"
        if re.search(pattern, loc, re.IGNORECASE):
            # Clean up and ensure uppercase state code
            cleaned = re.sub(pattern, state_code, loc, flags=re.IGNORECASE).strip()
            # If formatted like 'Melbourne VIC', convert to 'Melbourne, VIC'
            cleaned = re.sub(r"\s+([A-Z]{2,3})$", r", \1", cleaned)
            return cleaned
            
    return loc

def compute_content_hash(text: str) -> str:
    """Computes a deterministic SHA256 hash of normalized text."""
    normalized_text = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

def sanitize_text_field(text: str) -> str:
    """Disarms HTML/script tags and strips control characters from raw inputs."""
    if not text:
        return ""
    # Strip HTML tags
    cleaned = re.sub(r"<[^>]*>", "", text)
    # Strip control characters except newline and tab
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
    return cleaned.strip()

