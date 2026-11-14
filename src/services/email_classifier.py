"""
Rule-based, high-accuracy email classifier for job application responses.
Categories:
- ACKNOWLEDGMENT
- RECRUITER_CONTACT
- SCREENING
- INTERVIEW
- ASSESSMENT
- REJECTION
- OFFER
- OTHER
"""

import re
from typing import Dict, Any, Tuple

CLASSIFICATION_PATTERNS = {
    "OFFER": [
        r"\bformal offer\b",
        r"\bpleased to offer\b",
        r"\boffer of employment\b",
        r"\bcongratulations.*offer\b",
        r"\bemployment agreement\b",
        r"\bwelcome to the team\b"
    ],
    "INTERVIEW": [
        r"\binvite.*interview\b",
        r"\binterview invitation\b",
        r"\bschedule.*interview\b",
        r"\bfirst round interview\b",
        r"\btechnical interview\b",
        r"\bvideo interview\b",
        r"\bzoom meeting\b",
        r"\bteams meeting\b",
        r"\bcalendly\.com\b"
    ],
    "SCREENING": [
        r"\bphone screen\b",
        r"\bbrief call\b",
        r"\bquick chat\b",
        r"\binitial discussion\b",
        r"\bintroductory call\b",
        r"\b15-20 minutes\b"
    ],
    "ASSESSMENT": [
        r"\bcoding challenge\b",
        r"\btechnical test\b",
        r"\bhackerrank\b",
        r"\bcodility\b",
        r"\bonline assessment\b",
        r"\btake-home task\b",
        r"\btechnical assessment\b"
    ],
    "REJECTION": [
        r"\bunfortunately\b",
        r"\bnot moving forward\b",
        r"\bnot to proceed\b",
        r"\bother candidates\b",
        r"\bdecided to pursue\b",
        r"\bhigh volume of applicants\b",
        r"\bunsuccessful this time\b",
        r"\bnot be progressing\b",
        r"\bregret to inform\b"
    ],
    "ACKNOWLEDGMENT": [
        r"\bapplication received\b",
        r"\bthank you for applying\b",
        r"\bthank you for your application\b",
        r"\bthanks for applying\b",
        r"\bthank you for your interest\b",
        r"\bconfirming receipt\b",
        r"\bwe have received your application\b",
        r"\bsuccessfully submitted\b",
        r"\bwas submitted successfully\b",
        r"\bjob application acknowledgment\b",
        r"\brecruitment registration confirmation\b"
    ],
    "RECRUITER_CONTACT": [
        r"\bcame across your profile\b",
        r"\breviewing your application\b",
        r"\btalent acquisition\b",
        r"\brecruiter\b",
        r"\bfound your cv\b",
        r"\bhas viewed your application\b",
        r"\bviewed your application\b"
    ]
}

def classify_email(subject: str, body: str, sender: str = "") -> Tuple[str, float, list[str]]:
    """
    Classifies an email subject and body into one of the target categories.
    Returns: (category, confidence, matched_keywords)
    """
    combined_text = f"{subject}\n{body}".lower()
    
    # Priority check: OFFER > REJECTION > INTERVIEW > ASSESSMENT > SCREENING > ACKNOWLEDGMENT > RECRUITER_CONTACT
    priority_order = [
        "OFFER",
        "REJECTION",
        "INTERVIEW",
        "ASSESSMENT",
        "SCREENING",
        "ACKNOWLEDGMENT",
        "RECRUITER_CONTACT"
    ]
    
    for category in priority_order:
        patterns = CLASSIFICATION_PATTERNS[category]
        matched = []
        for pat in patterns:
            if re.search(pat, combined_text, re.IGNORECASE):
                matched.append(pat)
        
        if matched:
            confidence = min(0.65 + (len(matched) * 0.15), 0.99)
            return category, round(confidence, 2), matched

    return "OTHER", 0.50, []
