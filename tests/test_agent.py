"""Tests for deterministic template ticket generation.

These tests intentionally avoid importing any LLM dependency: the template /
``--no-llm`` path must work with the core scientific stack alone.
"""

from src.agent import generate_incident_ticket, render_template_ticket

SAMPLE_BUNDLE = {
    "predicted_class": "dos",
    "rf_confidence": 0.982,
    "source_ip": "192.168.1.47",
    "top_shap_drivers": [
        {
            "feature": "flag_S0",
            "true_value": 1,
            "shap_value": 0.123456789,  # must never leak into the ticket
            "direction": "increases risk",
        },
        {
            "feature": "serror_rate",
            "true_value": 1.0,
            "shap_value": 0.0816,
            "direction": "increases risk",
        },
    ],
}

REQUIRED_SECTIONS = (
    "1. Incident Summary",
    "2. Attack Classification",
    "3. Why flagged - Evidence",
    "4. Immediate Containment Steps",
    "5. Investigation Queries",
    "6. Escalation Recommendation",
)


def test_template_ticket_contains_all_required_sections():
    ticket = render_template_ticket(SAMPLE_BUNDLE, src_ip="192.168.1.47")
    for section in REQUIRED_SECTIONS:
        assert section in ticket


def test_template_ticket_includes_context_and_spl_queries():
    ticket = render_template_ticket(SAMPLE_BUNDLE, src_ip="10.0.0.9")
    assert "10.0.0.9" in ticket
    assert "```spl" in ticket
    assert "flag_S0" in ticket  # evidence uses the human-readable feature name
    assert "98.2%" in ticket  # confidence rendered as a percentage


def test_template_ticket_never_leaks_raw_shap_floats():
    ticket = render_template_ticket(SAMPLE_BUNDLE, src_ip="192.168.1.47")
    assert "0.123456789" not in ticket


def test_generate_incident_ticket_routes_template_provider_without_llm():
    # provider="template" must not require langchain/langgraph to be installed.
    ticket = generate_incident_ticket(SAMPLE_BUNDLE, src_ip="192.168.1.47", provider="template")
    assert "1. Incident Summary" in ticket
