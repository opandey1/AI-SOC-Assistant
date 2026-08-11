# Long-Term Implementation Record

This record maps the five Long-Term tasks in `AI-SOC-Assistant - Next Steps.docx` to the implemented code, reproducible commands, and observed validation results. Commands assume the repository root and a Windows virtual environment at `.venv`. During the final audit, the same commands were executed with Python 3.12.13 from `C:\Users\ojasp\AppData\Local\Temp\ai-soc-assistant-audit-py312-20260811\Scripts\python.exe`.

## 1. Live Sensor Ingestion

### What changed

- Added `src/runtime.py` so the CLI, event stream, and dashboard share one raw-record -> preprocessing -> classification -> SHAP -> ticket path.
- Added strict transformation of the 41 NSL-KDD input fields in `src/preprocess.py`, including unknown categorical handling and finite numeric validation.
- Added `src/streaming.py` with three modes:
  - `replay`: yields `KDDTest+` rows with an analyst-configurable delay.
  - `publish`: writes keyed JSON envelopes to Kafka.
  - `consume`: continuously scores Kafka events and stores alert tickets.
- Added a pinned Redpanda single-broker, producer, and consumer profile to `docker-compose.yml`.
- Kept `confluent-kafka` lazy-loaded so offline and non-Kafka workflows do not require a broker.

### Commands executed

```powershell
.\.venv\Scripts\python.exe -m pip install confluent-kafka==2.15.0
.\.venv\Scripts\python.exe -m src.streaming --help
.\.venv\Scripts\python.exe -m src.streaming replay --start-index 33 --limit 1 --delay 0 --model models\soc_model.joblib --no-store
.\.venv\Scripts\python.exe -m pytest tests\test_streaming.py
```

Observed replay result after feedback retraining:

```text
nsl-kdd-000033 192.0.2.34 -> NORMAL (fused confidence 50.3%, reason none)
Processed 1 event(s): 0 alert(s), 1 normal, 0 ticket(s) stored.
```

The Compose YAML was parsed successfully with seven services. Docker is not installed on the validation machine, so the Redpanda containers could not be launched locally. On a Docker host, the end-to-end command is:

```bash
docker compose --profile streaming up --build
```

## 2. Analyst Feedback Loop

### What changed

- Added `src/feedback.py` with WAL-enabled SQLite storage.
- Ticket writes are idempotent by `event_id`.
- Reviews are append-only; queries expose the latest analyst decision while preserving history.
- False positives default to corrected class `normal`, with validation against the five supported classes.
- Added `src/model_store.py` for atomic, versioned `joblib` artifacts.
- Added `src/retrain.py`. It keeps the Isolation Forest fixed, applies reviewed false positives to the Random Forest with an explicit weight, and compares correction behavior plus whole-test metrics before saving.
- The pipeline, stream consumer, and dashboard persist generated alert tickets to `state/soc_feedback.db` by default.

### Commands executed

The validation selected a real `KDDTest+` row whose ground truth was `normal` but whose fused baseline verdict was an alert, then stored and reviewed it:

```powershell
@'
from pathlib import Path
import numpy as np
from src.feedback import FeedbackStore
from src.ingest import LABEL_MAP
from src.runtime import analyze_raw_connection, build_runtime

runtime = build_runtime(search_roots=[Path.cwd()])
mask = (
    (runtime.preprocessor.y_test.to_numpy() == LABEL_MAP["normal"])
    & (runtime.models.fused_anomaly == 1)
)
index = int(np.flatnonzero(mask)[0])
analysis = analyze_raw_connection(
    runtime.dataset.test.iloc[index],
    runtime=runtime,
    source_ip="192.0.2.250",
    provider="template",
)
store = FeedbackStore(Path("state") / "soc_feedback.db")
ticket_id = store.log_analysis(
    analysis,
    event_id=f"validated-false-positive-{index}",
    source="validation-replay",
)
store.record_review(
    ticket_id,
    disposition="false_positive",
    corrected_class="normal",
    reviewed_by="validation",
)
print(index, ticket_id)
'@ | .\.venv\Scripts\python.exe -

.\.venv\Scripts\python.exe -m src.retrain --report state\retrain_report.json
.\.venv\Scripts\python.exe -m pytest tests\test_feedback.py tests\test_model_store.py
```

Observed result:

| Measure | Before | After |
|---|---:|---:|
| Corrected row prediction | `probe` | `normal` |
| Corrected-class probability | 2.83% | 50.22% |
| `KDDTest+` macro F1 | 51.49% | 51.42% |

The slight aggregate decrease is retained in the report rather than hidden. One correction proves behavioral update, not global model improvement. Full values are in `docs/evaluation/feedback_retraining/`.

## 3. Streamlit Analyst Dashboard

### What changed

- Added the canonical `streamlit_app.py` entry point.
- Added `.streamlit/config.toml` with a restrained light SOC theme and native Streamlit styling only.
- Added three workspaces:
  - Triage: dataset row, pasted JSON, or delayed live replay.
  - Review queue: filterable ticket table plus analyst disposition form.
  - Model: feedback count, guarded retraining, metrics, and report download.
- Cached fitted model resources and bounded row-data caches to avoid retraining on widget reruns.
- Added desktop and mobile-responsive metric rows and controls.

### Commands executed

```powershell
.\.venv\Scripts\python.exe -m pip install streamlit==1.60.0 joblib==1.5.2
.\.venv\Scripts\streamlit.exe run streamlit_app.py --server.address=127.0.0.1 --server.port=8501
```

Streamlit's application-testing API exercised the initial page, a full row analysis, the review queue, and model operations. Browser verification covered 1440x900 and 390x844 viewports, the SHAP chart, rendered ticket, and queue. The final mobile viewport had `scrollWidth == clientWidth` and the browser console had no errors.

The local dashboard is available at `http://localhost:8501` while the development server is running.

## 4. Multi-Dataset Validation

### What changed

- Added `scripts/download_unsw_nb15.py` with atomic download and SHA-256 enforcement.
- Added `src/validate_unsw.py` and tests.
- Trained only on NSL-KDD, using seven shared or closest-compatible fields.
- Used no UNSW labels for fitting, feature selection, thresholding, or tuning.
- Excluded `Generic` because the NSL-KDD five-family taxonomy has no defensible equivalent.
- Generated `docs/evaluation/unsw_transfer/metrics.json`, `metrics.md`, and `confusion_matrix.png`.

### Commands executed

```powershell
.\.venv\Scripts\python.exe scripts\download_unsw_nb15.py
.\.venv\Scripts\python.exe -m src.validate_unsw
.\.venv\Scripts\python.exe -m pytest tests\test_validate_unsw.py
```

Verified download:

```text
SHA256  734fe6642edf758f7c94d7d9149426b49d202fe8e7bf0bef47392489c3c0a559
Rows evaluated  63,461
```

Observed transfer result:

| Evaluation | Accuracy | Balanced accuracy | Macro F1 | Binary attack F1 |
|---|---:|---:|---:|---:|
| NSL-KDD common-feature hold-out | 97.05% | 95.49% | 87.97% | 99.13% |
| UNSW-NB15 zero-tuning transfer | 58.89% | 20.64% | 16.02% | 2.77% |

The severe drop is the finding: NSL-KDD feature semantics and distributions do not transfer safely to this newer capture environment. The README now treats the original 99.88% hold-out result as in-domain evidence rather than a production claim.

## 5. Unified SOC Roadmap

### What changed

- Added `docs/unified_soc_roadmap.md` and linked it as a headline README section.
- Defined the current integration contract: Kafka event envelope, structured SHAP analysis, SQLite human verdicts, and versioned model promotion.
- Mapped the next sequence from Zeek/Wazuh adapters through MCP tools, Agentic-SOC triage, Kali MCP validation, human approval, Sigma generation, and controlled retraining.
- Recorded security boundaries for telemetry privacy, narrow MCP permissions, active-validation allowlists, trusted artifacts, and promotion gates.

The separate Agentic-SOC/Unified SOC work currently exists as local design artifacts rather than a public GitHub repository. The README therefore links to the versioned roadmap and explicitly avoids a broken placeholder URL.

## Final Quality Gate

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m black --check streamlit_app.py src tests scripts
.\.venv\Scripts\python.exe -m flake8 streamlit_app.py src tests scripts
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m src.pipeline --no-llm --no-store
.\.venv\Scripts\python.exe -m src.validate_unsw
git status --short
git diff --check
```

Observed final audit:

- `104 passed` in the full pytest suite.
- Black checked 29 files without requiring changes.
- Flake8 completed without findings.
- `pip check` reported no broken requirements.
- The offline pipeline completed and generated a structured incident ticket.
- The Compose file parsed with seven services and four named volumes.
- `git diff --check` reported no whitespace errors.

Docker was not installed on the audit machine, so image builds and the live Redpanda
broker remain host-dependent validation steps. The Compose structure, profiles, service
dependencies, commands, health check, and persistent volume declarations were validated
statically.
