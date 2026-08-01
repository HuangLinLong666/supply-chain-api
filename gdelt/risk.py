from __future__ import annotations

import hashlib
import html
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


GDELT_SCORING_VERSION = "gdelt-event-cluster-v3"
DECISION_FACTOR_CATEGORIES = {
    "war": {"conflict"},
    "natural_disaster": {"natural_disaster"},
    "trade_policy": {"sanction", "trade_policy", "tariff"},
}
TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}
TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â€™", "ï»¿")

CATEGORY_TERMS: dict[str, dict[str, float]] = {
    "conflict": {
        "missile": 0.95,
        "attack": 0.92,
        "war": 0.92,
        "conflict": 0.82,
        "airstrike": 0.9,
        "strike": 0.72,
        "blockade": 0.86,
        "seized": 0.9,
        "closure": 0.78,
        "closed": 0.76,
    },
    "sanction": {
        "sanction": 0.78,
        "sanctions": 0.78,
        "embargo": 0.82,
        "blacklist": 0.74,
        "export ban": 0.76,
    },
    "trade_policy": {
        "trade restriction": 0.65,
        "trade policy": 0.55,
        "export control": 0.68,
        "import restriction": 0.62,
        "quota": 0.48,
    },
    "tariff": {
        "tariff": 0.55,
        "tariffs": 0.55,
        "duty": 0.52,
        "duties": 0.52,
        "customs levy": 0.55,
    },
    "strike": {
        "labor strike": 0.72,
        "dockworker strike": 0.78,
        "port strike": 0.78,
        "walkout": 0.68,
        "industrial action": 0.66,
    },
    "port_disruption": {
        "port closure": 0.82,
        "port congestion": 0.66,
        "congestion": 0.55,
        "shipping disruption": 0.72,
        "logistics disruption": 0.68,
        "terminal closure": 0.8,
        "channel blocked": 0.84,
    },
    "transport_accident": {
        "collision": 0.75,
        "grounding": 0.78,
        "derailment": 0.82,
        "crash": 0.82,
        "accident": 0.62,
        "explosion": 0.84,
        "fire": 0.68,
    },
    "natural_disaster": {
        "typhoon": 0.82,
        "cyclone": 0.82,
        "hurricane": 0.84,
        "earthquake": 0.86,
        "tsunami": 0.94,
        "flood": 0.72,
        "landslide": 0.72,
        "volcanic": 0.78,
    },
    "customs_delay": {
        "customs delay": 0.58,
        "border delay": 0.58,
        "clearance delay": 0.56,
        "inspection backlog": 0.6,
        "customs backlog": 0.62,
    },
}

CATEGORY_PRIORITY = (
    "conflict",
    "natural_disaster",
    "transport_accident",
    "port_disruption",
    "strike",
    "sanction",
    "trade_policy",
    "tariff",
    "customs_delay",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or "")).replace("\ufeff", "").strip()
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        try:
            repaired = text.encode("latin-1").decode("utf-8")
            if sum(marker in repaired for marker in MOJIBAKE_MARKERS) < sum(
                marker in text for marker in MOJIBAKE_MARKERS
            ):
                text = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split())


def canonicalize_url(value: Any) -> str | None:
    raw = clean_text(value)
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.netloc:
        return raw
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMETERS
    ]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.casefold() or "https", parts.netloc.casefold(), path, urlencode(sorted(query)), ""))


def normalize_title(value: Any) -> str:
    text = clean_text(value).casefold()
    return " ".join(TOKEN_PATTERN.findall(text))


def title_tokens(value: Any) -> set[str]:
    return {token for token in normalize_title(value).split() if len(token) > 1}


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def parse_seen_datetime(value: Any, *, now: datetime | None = None, future_tolerance_minutes: int = 5) -> datetime | None:
    reference = (now or utc_now()).astimezone(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        text = clean_text(value).replace("Z", "+00:00")
        parsed = None
        for pattern in ("%Y%m%dT%H%M%S%z", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                pass
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed > reference + timedelta(minutes=future_tolerance_minutes):
        return None
    return parsed


def parse_seen_date(value: Any, *, now: datetime | None = None) -> str | None:
    parsed = parse_seen_datetime(value, now=now)
    return parsed.isoformat() if parsed else None


def classify_article(article: dict[str, Any]) -> dict[str, Any]:
    text = f"{clean_text(article.get('title'))} {clean_text(article.get('description'))}".casefold()
    category_hits: dict[str, list[tuple[str, float]]] = {}
    for category, terms in CATEGORY_TERMS.items():
        hits = [
            (term, severity)
            for term, severity in terms.items()
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text)
        ]
        if hits:
            category_hits[category] = hits
    if not category_hits:
        return {
            "event_category": "other",
            "matched_categories": [],
            "matched_terms": [],
            "severity": 0.25,
            "classification_status": "unclassified",
        }
    primary = max(
        category_hits,
        key=lambda category: (
            max(severity for _, severity in category_hits[category]),
            -CATEGORY_PRIORITY.index(category),
        ),
    )
    matched_terms = sorted({term for hits in category_hits.values() for term, _ in hits})
    severity = max(severity for hits in category_hits.values() for _, severity in hits)
    return {
        "event_category": primary,
        "matched_categories": [category for category in CATEGORY_PRIORITY if category in category_hits],
        "matched_terms": matched_terms,
        "severity": round(severity, 4),
        "classification_status": "classified",
    }


def article_severity(article: dict[str, Any]) -> tuple[float, list[str]]:
    result = classify_article(article)
    return float(result["severity"]), list(result["matched_terms"])


def article_id(article: dict[str, Any]) -> str:
    identity = str(article.get("url") or article.get("title") or "")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def prepare_article(article: dict[str, Any], *, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    raw_title = str(article.get("title") or "")
    raw_url = str(article.get("url") or "")
    title = clean_text(raw_title)
    url = clean_text(raw_url)
    if not title and not url:
        return None, "missing_identity"
    seen_at = parse_seen_datetime(article.get("seen_at") or article.get("seendate"), now=now)
    if seen_at is None:
        return None, "invalid_or_future_seen_at"
    canonical_url = canonicalize_url(url)
    canonical_domain = urlsplit(canonical_url).netloc if canonical_url and urlsplit(canonical_url).netloc else None
    normalized_title = normalize_title(title)
    classification = classify_article({**article, "title": title})
    identifier = str(article.get("article_id") or article_id({"url": url, "title": title}))
    content_identity = f"{normalized_title}|{seen_at.date().isoformat()}|{classification['event_category']}"
    content_hash = hashlib.sha256(content_identity.encode("utf-8")).hexdigest()[:24]
    return (
        {
            **article,
            **classification,
            "article_id": identifier,
            "raw_title": raw_title or None,
            "raw_url": raw_url or None,
            "title": title,
            "url": url or None,
            "canonical_url": canonical_url,
            "canonical_url_hash": hashlib.sha256((canonical_url or identifier).encode("utf-8")).hexdigest()[:24],
            "domain": clean_text(article.get("domain")) or canonical_domain,
            "normalized_title": normalized_title,
            "title_tokens": title_tokens(title),
            "content_hash": content_hash,
            "seen_at": seen_at.isoformat(),
            "seen_datetime": seen_at,
            "time_status": "valid_utc",
        },
        None,
    )


class DisjointSet:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def cluster_articles(
    articles: list[dict[str, Any]],
    *,
    similarity_threshold: float = 0.82,
    window_hours: int = 48,
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    if not articles:
        return []
    ordered = sorted(articles, key=lambda item: item["seen_datetime"])
    disjoint = DisjointSet(len(ordered))
    exact_url_owner: dict[str, int] = {}
    for index, article in enumerate(ordered):
        canonical_hash = article["canonical_url_hash"]
        if canonical_hash in exact_url_owner:
            disjoint.union(index, exact_url_owner[canonical_hash])
        else:
            exact_url_owner[canonical_hash] = index
        for previous in range(index - 1, -1, -1):
            other = ordered[previous]
            if (article["seen_datetime"] - other["seen_datetime"]).total_seconds() > window_hours * 3600:
                break
            if article["event_category"] != other["event_category"]:
                continue
            if article["normalized_title"] == other["normalized_title"] or jaccard_similarity(
                article["title_tokens"], other["title_tokens"]
            ) >= similarity_threshold:
                disjoint.union(index, previous)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, article in enumerate(ordered):
        grouped.setdefault(disjoint.find(index), []).append(article)
    clusters: list[dict[str, Any]] = []
    for members in grouped.values():
        representative = max(members, key=lambda item: (item["severity"], -len(item["title"])))
        prefix = f"gdelt-cluster-{normalize_title(namespace)}-" if namespace else "gdelt-cluster-"
        cluster_id = prefix + min(item["content_hash"] for item in members)
        domains = sorted({clean_text(item.get("domain")).casefold() for item in members if item.get("domain")})
        cluster = {
            "cluster_id": cluster_id,
            "event_category": representative["event_category"],
            "representative_title": representative["title"],
            "representative_article_id": representative["article_id"],
            "first_seen": min(item["seen_datetime"] for item in members).isoformat(),
            "last_seen": max(item["seen_datetime"] for item in members).isoformat(),
            "severity": max(float(item["severity"]) for item in members),
            "article_count": len(members),
            "distinct_domain_count": len(domains),
            "domains": domains,
            "article_ids": sorted(str(item["article_id"]) for item in members),
            "source_credibility_status": "unavailable",
            "confidence_basis": "source_diversity_not_domain_credibility",
        }
        clusters.append(cluster)
        for member in members:
            member["event_cluster_id"] = cluster_id
            member["cluster_article_count"] = len(members)
            member["cluster_distinct_domain_count"] = len(domains)
    return sorted(clusters, key=lambda item: (item["last_seen"], item["severity"]), reverse=True)


def freshness_weight(seen_at: str, *, now: datetime, half_life_hours: float) -> float:
    observed_at = parse_seen_datetime(seen_at, now=now)
    if observed_at is None:
        return 0.0
    age_hours = max(0.0, (now - observed_at).total_seconds() / 3600)
    return 0.5 ** (age_hours / half_life_hours)


def score_decision_factors(clusters: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    factors: dict[str, dict[str, Any]] = {}
    for factor_key, categories in DECISION_FACTOR_CATEGORIES.items():
        matching = [cluster for cluster in clusters if cluster.get("event_category") in categories]
        if not matching:
            continue
        severities = sorted((float(cluster.get("effective_severity") or 0.0) for cluster in matching), reverse=True)
        top_mean = sum(severities[:3]) / min(3, len(severities))
        score = min(1.0, 0.7 * severities[0] + 0.3 * top_mean)
        domains = {domain for cluster in matching for domain in cluster.get("domains") or [] if domain}
        cluster_coverage = min(1.0, len(matching) / 3)
        source_diversity = min(1.0, len(domains) / 3)
        confidence = min(0.95, 0.45 + 0.3 * cluster_coverage + 0.2 * source_diversity)
        factors[factor_key] = {
            "score": round(score, 4),
            "confidence": round(confidence, 4),
            "cluster_ids": sorted(str(cluster["cluster_id"]) for cluster in matching),
            "observed_at": max(str(cluster["last_seen"]) for cluster in matching),
            "categories": sorted(categories),
        }
    return factors


def score_zone(
    articles: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    similarity_threshold: float = 0.82,
    cluster_window_hours: int = 48,
    freshness_half_life_hours: float = 24,
    cluster_namespace: str | None = None,
) -> dict[str, Any]:
    reference = (now or utc_now()).astimezone(timezone.utc)
    prepared: list[dict[str, Any]] = []
    rejected = Counter()
    exact_seen: set[tuple[str, str]] = set()
    for raw_article in articles:
        article, reason = prepare_article(raw_article, now=reference)
        if article is None:
            rejected[reason or "invalid"] += 1
            continue
        exact_key = (article["canonical_url_hash"], article["normalized_title"])
        if exact_key in exact_seen:
            rejected["exact_duplicate"] += 1
            continue
        exact_seen.add(exact_key)
        prepared.append(article)
    clusters = cluster_articles(
        prepared,
        similarity_threshold=similarity_threshold,
        window_hours=cluster_window_hours,
        namespace=cluster_namespace,
    )
    scoreable = [cluster for cluster in clusters if cluster["event_category"] != "other"]
    for cluster in clusters:
        weight = freshness_weight(cluster["last_seen"], now=reference, half_life_hours=freshness_half_life_hours)
        cluster["freshness_weight"] = round(weight, 4)
        cluster["effective_severity"] = round(float(cluster["severity"]) * weight, 4)
    decision_factors = score_decision_factors(clusters)
    if not scoreable:
        return {
            "score": None,
            "level": "UNKNOWN",
            "status": "unavailable",
            "confidence": None,
            "articles": prepared,
            "clusters": clusters,
            "raw_article_count": len(articles),
            "valid_article_count": len(prepared),
            "cluster_count": len(clusters),
            "rejected_counts": dict(rejected),
            "category_counts": dict(Counter(item["event_category"] for item in prepared)),
            "decision_factors": decision_factors,
            "scoring_version": GDELT_SCORING_VERSION,
            "source_credibility_status": "unavailable",
        }
    severities = sorted((float(cluster["effective_severity"]) for cluster in scoreable), reverse=True)
    top_mean = sum(severities[:3]) / min(3, len(severities))
    score = min(1.0, 0.7 * severities[0] + 0.3 * top_mean)
    level = "CRITICAL" if score >= 0.8 else "HIGH" if score >= 0.6 else "MEDIUM" if score >= 0.35 else "LOW"
    domain_count = len({domain for cluster in scoreable for domain in cluster["domains"]})
    classified_ratio = len([item for item in prepared if item["event_category"] != "other"]) / len(prepared)
    cluster_coverage = min(1.0, len(scoreable) / 3)
    source_diversity = min(1.0, domain_count / 3)
    confidence = min(0.95, 0.45 + 0.25 * cluster_coverage + 0.2 * source_diversity + 0.1 * classified_ratio)
    return {
        "score": round(score, 4),
        "level": level,
        "status": "available",
        "confidence": round(confidence, 4),
        "articles": prepared,
        "clusters": clusters,
        "raw_article_count": len(articles),
        "valid_article_count": len(prepared),
        "cluster_count": len(clusters),
        "rejected_counts": dict(rejected),
        "category_counts": dict(Counter(item["event_category"] for item in prepared)),
        "decision_factors": decision_factors,
        "scoring_version": GDELT_SCORING_VERSION,
        "source_credibility_status": "unavailable",
    }
