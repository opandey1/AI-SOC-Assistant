# AI-Powered SOC Assistant

An explainable AI triage pipeline that classifies NSL-KDD network connections, fuses supervised and unsupervised anomaly signals, and generates analyst-ready SOC incident tickets with GenAI.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dataset](https://img.shields.io/badge/dataset-NSL--KDD-purple)](https://www.unb.ca/cic/datasets/nsl.html)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/opandey1/AI-SOC-Assistant/blob/main/notebooks/AI_Powered_SOC_Assistant.ipynb)

![System Architecture](docs/soc_architecture.svg)

## Why This Is Different

- **Native multi-class SOC detection:** Unlike binary anomaly demos, this pipeline classifies specific attack families: Normal, DoS, Probe, R2L, and U2R.
- **Dual-model triage:** A Random Forest predicts the attack family while an Isolation Forest adds an unsupervised anomaly signal for suspicious traffic patterns.
- **Explainable evidence:** SHAP identifies the strongest feature drivers for each flagged connection and passes analyst-readable values into the ticket.
- **Local-first GenAI:** Ollama support lets the assistant generate tickets without sending raw network telemetry to an external LLM API.
- **Operational output:** The final response is a structured incident ticket with containment steps and copy-pasteable Splunk SPL queries.

## Quickstart

```bash
git clone https://github.com/opandey1/AI-SOC-Assistant.git
cd AI-SOC-Assistant
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Place `KDDTrain+.txt` and `KDDTest+.txt` in the repo root or `data/`, then run a no-LLM smoke test:

```bash
python src/pipeline.py --no-llm
```

To generate the ticket with a local Ollama model:

```bash
ollama pull mistral
SOC_LLM_PROVIDER=ollama python src/pipeline.py
```

PowerShell users can set the provider like this:

```powershell
$env:SOC_LLM_PROVIDER = "ollama"
python src/pipeline.py
```

## Repository Structure

```text
src/
  ingest.py       NSL-KDD loading and attack-family mapping
  preprocess.py   one-hot encoding, scaling, and SMOTE balancing
  train.py        Random Forest, Isolation Forest, and fused scoring
  explain.py      SHAP explanation bundle generation
  agent.py        LangGraph SOC analyst agent and threat-intel tools
  pipeline.py     runnable command-line pipeline
notebooks/
  AI_Powered_SOC_Assistant.ipynb
docs/
  soc_architecture.svg
  SOC_Assistant_Evolution.pdf
  shap_example_output.json
  sample_ticket.md
```

## Demo Artifacts

- [Architecture diagram](docs/soc_architecture.svg)
- [Evolution brief](docs/SOC_Assistant_Evolution.pdf)
- [Example SHAP bundle](docs/shap_example_output.json)
- [Sample generated ticket](docs/sample_ticket.md)
- [Colab notebook](notebooks/AI_Powered_SOC_Assistant.ipynb)

## Evolution & Wins

### 1. Feature Engineering: Robust Categorical Encoding

**Initial state:** The pipeline used `LabelEncoder` for categorical network features such as protocol, service, and flag.

**Challenge:** `LabelEncoder` creates false mathematical ordinals and can crash when live traffic introduces a category not seen during training.

**Fix:** The preprocessing pipeline now uses `OneHotEncoder(handle_unknown="ignore")`.

**Win:** The feature representation is mathematically sound and resilient to novel service values.

### 2. Agent Orchestration: Migrating to LangGraph

**Initial state:** The assistant logic relied on older LangChain agent patterns.

**Challenge:** Modern tool-calling workflows benefit from clearer state handling and more reliable agent execution.

**Fix:** The agent layer uses `langgraph.prebuilt.create_react_agent`.

**Win:** The orchestration layer is more maintainable and better aligned with current LangChain/LangGraph patterns.

### 3. Environment Constraints: Air-Gapped LLM Execution

**Initial state:** LLM usage was tied to cloud provider calls.

**Challenge:** SOC environments often restrict transmission of raw telemetry, internal IPs, and security logs to external APIs.

**Fix:** `initialize_llm` supports a provider factory with Ollama, OpenAI, Anthropic, and template modes.

**Win:** The pipeline can run locally with Ollama for privacy-sensitive demos and restricted environments.

### 4. Threat Intel Integration: Fault-Tolerant API Tools

**Initial state:** Threat-intelligence tools were placeholders.

**Challenge:** Real APIs can fail, rate-limit, or return verbose nested payloads that overwhelm the LLM context.

**Fix:** AbuseIPDB and NVD lookups use timeouts, explicit error handling, and compact response formatting.

**Win:** API failures become ticket context instead of Python process failures.

### 5. Explainable AI: Cross-Version SHAP Compatibility

**Initial state:** SHAP extraction assumed one output shape.

**Challenge:** SHAP has changed multi-class output formats across versions.

**Fix:** The explanation layer handles both legacy list outputs and newer 3D array outputs.

**Win:** The code is more portable across dependency versions.

### 6. Forensic Accuracy: Real-World Metrics

**Initial state:** The explanation bundle risked exposing scaled z-score values to the LLM.

**Challenge:** SOC analysts need real packet, byte, count, and rate values, not normalized model inputs.

**Fix:** Model inference still uses scaled values, while SHAP evidence includes the unscaled processed feature values.

**Win:** Generated tickets read like analyst evidence rather than model internals.

### 7. GenAI Guardrails: Eliminating Hallucinations & Over-Generation

**Initial state:** Smaller local models could drift into code snippets or raw floating-point SHAP values.

**Challenge:** The assistant must produce a tight incident ticket, not a generic explanation or script.

**Fix:** The system prompt enforces ticket-only output, structured sections, exact Splunk SPL code blocks, and a ban on raw SHAP float leakage.

**Win:** The GenAI layer translates model evidence into actionable SOC workflow output.
