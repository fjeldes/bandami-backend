"""
PII sanitization for AI API calls — GDPR data minimization.
Strips names, emails, phone numbers, and addresses from user-submitted text
before transmitting to third-party AI providers.
"""
import re

PII_PATTERNS = [
    (r'\b[A-Z][a-záéíóúñ]+ [A-Z][a-záéíóúñ]+ [A-Z][a-záéíóúñ]+\b', '[FULL_NAME]'),
    (r'\b[A-Z][a-záéíóúñ]+ [A-Z][a-záéíóúñ]+\b', '[NAME]'),
    (r'\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[EMAIL]'),
    (r'\b(?:\+?56\s?)?9?\d{4}[\s.-]?\d{4}\b', '[PHONE]'),
    (r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b', '[PHONE]'),
    (r'\b\d{5,}\b', '[NUMBER]'),
]


def sanitize_for_ai(text: str) -> str:
    """Remove PII patterns from text before sending to AI providers."""
    sanitized = text
    for pattern, replacement in PII_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized
