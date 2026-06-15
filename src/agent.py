"""LLM agent and threat-intelligence tools for SOC ticket generation."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import requests
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent


SYSTEM_PROMPT = """You are a Tier-2 SOC analyst assistant.
Produce a professional incident ticket in this exact structure:
1. Incident Summary (2-3 sentences: what happened, severity)
2. Attack Classification (type, confidence %)
3. Why flagged - Evidence (List the top features and their actual network metric true_value. Explain why this specific value is suspicious in plain English. STRICT RULE: NEVER print raw mathematical shap_value floats.)
4. Immediate Containment Steps (numbered, actionable)
5. Investigation Queries (Write exact, executable Splunk SPL queries inside code blocks. Do not just describe the query.)
6. Escalation Recommendation (P1/P2/P3 with rationale)

CRITICAL INSTRUCTIONS:
- Do NOT invent evidence not in the SHAP bundle.
- You must output only the incident ticket.
- Do NOT output Python code, scripts, or conversational filler.
- End your response immediately after the Escalation Recommendation.
"""


@tool
def lookup_ip_reputation(ip_address: str) -> str:
    """Query AbuseIPDB for IP address reputation."""

    api_key = os.getenv("ABUSEIPDB_API_KEY")
    if not api_key:
        return f"System Error: ABUSEIPDB_API_KEY is not set. Cannot verify {ip_address}."

    url = "https://api.abuseipdb.com/api/v2/check"
    querystring = {"ipAddress": ip_address, "maxAgeInDays": "90"}
    headers = {"Accept": "application/json", "Key": api_key}

    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=5)
        response.raise_for_status()
        data = response.json().get("data", {})
        score = data.get("abuseConfidenceScore", 0)
        isp = data.get("isp", "Unknown ISP")
        reports = data.get("totalReports", 0)
        return f"IP: {ip_address} | Abuse Score: {score}/100 | ISP: {isp} | Total Reports (90 days): {reports}"
    except requests.exceptions.RequestException as exc:
        return f"Failed to retrieve IP reputation for {ip_address}. Network/API Error: {exc}"


@tool
def lookup_cve(service_name: str) -> str:
    """Query NIST NVD for recent CVEs related to a service name."""

    url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    querystring = {"keywordSearch": service_name, "resultsPerPage": 3}

    try:
        response = requests.get(url, params=querystring, timeout=10)
        response.raise_for_status()
        vulnerabilities = response.json().get("vulnerabilities", [])
        if not vulnerabilities:
            return f"No recent CVEs found in NVD for service: {service_name}."

        summaries = []
        for item in vulnerabilities:
            cve = item.get("cve", {})
            cve_id = cve.get("id", "Unknown ID")
            description = next(
                (desc.get("value", "") for desc in cve.get("descriptions", []) if desc.get("lang") == "en"),
                "No description.",
            )
            description = " ".join(description.split())
            if len(description) > 420:
                description = f"{description[:417]}..."
            summaries.append(f"- {cve_id}: {description}")
        return f"Top {len(summaries)} CVEs related to '{service_name}':\n" + "\n".join(summaries)
    except requests.exceptions.RequestException as exc:
        return f"Failed to retrieve CVE data for {service_name}. API Error: {exc}"


def initialize_llm(provider: str = "ollama") -> Any:
    """Initialize the requested chat model provider."""

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model = os.getenv("SOC_ANTHROPIC_MODEL", "claude-sonnet-4-5")
        return ChatAnthropic(model=model, temperature=0.2)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        model = os.getenv("SOC_OPENAI_MODEL", "gpt-4o")
        return ChatOpenAI(model=model, temperature=0.2)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        model = os.getenv("SOC_OLLAMA_MODEL", "mistral")
        return ChatOllama(model=model, temperature=0.2)

    raise ValueError(f"Unsupported LLM provider: {provider}")


def build_agent(provider: str | None = None) -> Any:
    """Build a LangGraph ReAct SOC analyst agent."""

    active_provider = provider or os.getenv("SOC_LLM_PROVIDER", "ollama")
    llm = initialize_llm(active_provider)
    return create_react_agent(llm, [lookup_ip_reputation, lookup_cve], prompt=SYSTEM_PROMPT)


def _percent(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    return f"{float(value) * 100:.1f}%"


def render_template_ticket(
    shap_bundle: dict[str, Any],
    *,
    src_ip: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Render a deterministic ticket for demos or environments without a local LLM."""

    source_ip = src_ip or shap_bundle.get("source_ip") or "unknown"
    detected_at = timestamp or datetime.now(timezone.utc).isoformat()
    predicted_class = shap_bundle.get("predicted_class", "unknown")
    confidence = _percent(shap_bundle.get("rf_confidence"))
    drivers = shap_bundle.get("top_shap_drivers", [])[:5]

    evidence_lines = []
    for driver in drivers:
        feature = driver.get("feature", "unknown_feature")
        value = driver.get("true_value", "unknown")
        direction = driver.get("direction", "influences risk")
        evidence_lines.append(f"- {feature}: observed value {value} {direction}.")
    evidence = "\n".join(evidence_lines) if evidence_lines else "- No SHAP driver details were available."

    return f"""1. Incident Summary
Connection telemetry from {source_ip} was flagged as suspicious at {detected_at}. The detector classified the activity as {predicted_class} with {confidence} Random Forest confidence, requiring analyst validation and containment triage.

2. Attack Classification
Type: {predicted_class}
Confidence: {confidence}

3. Why flagged - Evidence
{evidence}

4. Immediate Containment Steps
1. Validate whether {source_ip} maps to an expected internal asset or approved scanner.
2. Review recent authentication, connection, and firewall events involving the source and destination pair.
3. Temporarily restrict the source if the activity is unauthorized or recurring.
4. Preserve packet, flow, and endpoint evidence before remediation.

5. Investigation Queries
```spl
index=network sourcetype=firewall src_ip="{source_ip}" earliest=-24h
| stats count, values(action), values(dest_ip), values(dest_port), values(app) by src_ip
```

```spl
index=network sourcetype=ids src_ip="{source_ip}" earliest=-24h
| table _time signature severity src_ip dest_ip dest_port
```

6. Escalation Recommendation
P2 - Escalate to the SOC lead if the traffic is not attributable to approved scanning, backup, or administrative activity. Raise to P1 if the same source shows confirmed exploitation, lateral movement, or impact on production services."""


def generate_incident_ticket(
    shap_bundle: dict[str, Any],
    *,
    src_ip: str | None = None,
    provider: str | None = None,
    agent_executor: Any | None = None,
    timestamp: str | None = None,
) -> str:
    """Generate an incident ticket from a SHAP bundle using an LLM or template mode."""

    active_provider = provider or os.getenv("SOC_LLM_PROVIDER", "ollama")
    if active_provider == "template":
        return render_template_ticket(shap_bundle, src_ip=src_ip, timestamp=timestamp)

    source_ip = src_ip or shap_bundle.get("source_ip") or "unknown"
    detected_at = timestamp or datetime.now(timezone.utc).isoformat()
    message = f"""
Generate a SOC incident ticket for the following flagged connection:

SHAP Analysis Bundle:
{json.dumps(shap_bundle, indent=2)}

Source IP: {source_ip}
Detection timestamp: {detected_at}
"""
    agent = agent_executor or build_agent(active_provider)
    result = agent.invoke({"messages": [("user", message)]})
    return result["messages"][-1].content
