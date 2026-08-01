import app.main as main


def test_methodology_returns_only_the_selected_strategy_formula():
    risk = main.methodology("min_risk")
    cost = main.methodology("min_cost")
    balanced = main.methodology("balanced")

    assert [factor["key"] for factor in risk["factors"]] == [
        "war",
        "natural_disaster",
        "trade_policy",
    ]
    assert set(risk) == {"strategy", "title", "formula", "factors", "note"}
    assert set(cost) == {"strategy", "title", "formula", "currency", "note"}
    assert set(balanced) == {"strategy", "title", "formula", "weights", "note"}


def test_openapi_exposes_methodology_endpoint():
    assert "/api/methodology" in main.app.openapi()["paths"]
