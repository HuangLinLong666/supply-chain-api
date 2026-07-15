from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any


SEVERITY_TERMS = {
    "critical": (0.95, ("missile", "attack", "war", "killed", "closure", "closed", "seized")),
    "high": (0.78, ("conflict", "strike", "piracy", "explosion", "disruption", "blockade", "sanction", "embargo")),
    "medium": (0.55, ("congestion", "delay", "accident", "threat", "warning", "divert", "tariff", "tariffs", "duty", "duties")),
}


def article_id(article: dict[str, Any]) -> str:
    identity = str(article.get("url") or article.get("title") or "")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def article_severity(article: dict[str, Any]) -> tuple[float, list[str]]:
    text = f"{article.get('title', '')} {article.get('description', '')}".casefold()
    matched: list[str] = []
    score = 0.25
    for _, (value, terms) in SEVERITY_TERMS.items():
        hits = [term for term in terms if re.search(rf"\b{re.escape(term)}\b", text)]
        if hits:
            score = max(score, value)
            matched.extend(hits)
    return score, sorted(set(matched))


def score_zone(articles: list[dict[str, Any]]) -> dict[str, Any]:
    unique = {article_id(article): article for article in articles if article.get("url") or article.get("title")}
    scored = []
    for identifier, article in unique.items():
        severity, terms = article_severity(article)
        scored.append({**article, "article_id": identifier, "severity": severity, "matched_terms": terms})
    if not scored:
        return {"score": 0.0, "level": "LOW", "confidence": 0.2, "articles": []}
    count_signal = 1 - math.exp(-len(scored) / 6)
    severities = sorted((item["severity"] for item in scored), reverse=True)
    top_average = sum(severities[:5]) / min(len(severities), 5)
    score = min(1.0, 0.35 * count_signal + 0.65 * top_average)
    level = "CRITICAL" if score >= 0.8 else "HIGH" if score >= 0.6 else "MEDIUM" if score >= 0.35 else "LOW"
    confidence = min(1.0, 0.35 + len(scored) / 20)
    return {"score": round(score, 4), "level": level, "confidence": round(confidence, 4), "articles": scored}


def parse_seen_date(value: Any) -> str:
    text = str(value or "")
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()
