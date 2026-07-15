from gdelt.exposure import inferred_exposure
from gdelt.risk import article_id, article_severity, score_zone


def test_attack_news_creates_high_risk():
    articles = [{"url": f"https://example.com/{index}", "title": "Missile attack closes Red Sea shipping lane"} for index in range(6)]
    result = score_zone(articles)
    assert result["score"] >= 0.8
    assert result["level"] == "CRITICAL"


def test_duplicate_urls_are_deduplicated():
    article = {"url": "https://example.com/one", "title": "Shipping disruption"}
    assert len(score_zone([article, article])["articles"]) == 1
    assert article_id(article) == article_id(article)


def test_neutral_article_has_low_base_severity():
    score, terms = article_severity({"title": "Port publishes annual report"})
    assert score == 0.25
    assert terms == []


def test_asia_europe_sea_segment_is_red_sea_exposed():
    segment = {"mode": "sea", "from_country": "Singapore", "to_country": "Netherlands"}
    assert inferred_exposure("red-sea", segment)
    assert inferred_exposure("malacca-strait", segment)
    assert inferred_exposure("indian-ocean", segment)


def test_cape_route_is_not_red_sea_exposed():
    assert not inferred_exposure("red-sea", {"mode": "sea", "from_name": "Singapore Port", "to_name": "Cape Town", "from_country": "Singapore", "to_country": "South Africa"})


def test_air_route_is_not_red_sea_exposed():
    assert not inferred_exposure("red-sea", {"mode": "air", "from_country": "China", "to_country": "Germany"})


def test_trans_pacific_route_is_exposed_to_pacific():
    assert inferred_exposure("pacific-ocean", {"mode": "sea", "from_country": "China", "to_country": "United States"})


def test_middle_east_affects_air_routes():
    assert inferred_exposure("middle-east", {"mode": "air", "from_country": "China", "to_country": "Germany"})


def test_tariff_news_increases_severity():
    score, terms = article_severity({"title": "New tariffs imposed on shipping route"})
    assert score == 0.55
    assert "tariffs" in terms
