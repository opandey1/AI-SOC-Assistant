# Triage Workflow Implementation and Verification

Verified **9 October 2026** against merged baseline `5610d78` on `main`.
Implementation commit: `ea1f1a7` (`feat(ui): harden triage inputs and replay states`).
This pass improves the running Streamlit app; it does not edit Figma drawings.

## Tasks Completed

| Task / tracking reference | Implementation and rationale | Verification |
|---|---|---|
| Initial empty state / Figma 3 app counterpart | `streamlit_app.py` renders a no-current-result state without loading the model. `src/ui.py` replaces a sanitised-away SVG with Streamlit's existing Material icon font. | AppTest checks no implicit inference/write; browser confirms the icon and font render. |
| Cleared and alert verdicts / Figma 2, 10-13 app counterparts | Evidence headings distinguish flagged from cleared connections. SHAP is explicitly labelled as evidence for the **Random Forest class**, not the fused/Isolation Forest verdict. Supporting bars use that family's colour, including green for NORMAL. Cleared traffic has a no-ticket callout; alert tickets remain downloadable. | Dataset and JSON normal/alert cases, ISO-only alert with RF NORMAL, rendered SHAP rows and an actual Markdown download. |
| JSON input and errors / D1; Figma 4, 25 app counterparts | JSON must be an object containing all required model fields via `src.streaming.event_from_payload` before runtime loading. Malformed, non-object and incomplete submissions produce actionable native errors. Submitted JSON persists for correction; failed requests clear the old verdict. Scalar/finite validation stays in the shared inference path. | AppTest verifies validation order, result clearing and submitted-value retention. Real browser checks malformed JSON, arrays, missing fields, nested numeric values, nonnumeric strings and `NaN`, then successful normal/alert scoring. |
| Replay inputs, feed and status / Figma 5, 14, 15 app counterparts | Row controls are bounded to the test file. Replay has no misleading Source IP input because event IPs are generated. Progress uses the available cohort, while labels separately report requested/processed counts. The bounded feed and last successful event survive reruns. Partial failures retain already stored tickets and report that the remainder was not processed. | AppTest verifies end-of-file truncation, progress, retained rows, partial failure and alert-only storage. Browser replays three actual rows and retains the table when changing input mode. |
| Submission failure and database isolation | Each new request resets the previous result. Runtime/inference/storage failures transition the native status to error; internal exception text and payloads are not echoed or logged. Logs record stage and exception type only. Results reset when the review database changes, preventing stale ticket IDs from appearing tied to another store. | Failure/retry and database-switch tests, including sensitive exception-text assertions. |
| Responsive Triage / D2 sampled core paths | Scoped CSS retains native controls, wraps long context/evidence values, stacks evidence/ticket columns and score tracks below 1100 px, and keeps the mobile header clear of the sidebar toggle. No changes to detection algorithms, retraining or schema. | Empty/alert/clear/replay/JSON layouts checked at 1440, 1024, 390 and 320 px, plus evidence-row screenshots and overflow assertions. Desktop checks use an expanded sidebar. |

## Results

- **193 tests passed**, up from 171; **77.88% total coverage**, enforcing the existing
  70% floor. `streamlit_app.py` reached 97%; `src/retrain.py` remains at 37%.
- Black and flake8 passed. The test run reported 924 dependency warnings; they were
  not suppressed or remedied in this UI pass.
- Local execution used Python **3.13.14** / pytest **8.3.4** / Streamlit **1.60.0**.
  Python 3.13 is outside the supported 3.10-3.12 CI matrix. No new remote CI result
  is claimed for these unpushed commits.
- Headless Microsoft Edge/Playwright passed **20 state/width geometry checks**,
  with no page errors, Streamlit exceptions or document-level horizontal overflow.
  Additional cleared-evidence screenshots/assertions passed at all four widths.
- Actual deterministic inference used the local NSL-KDD files, not the mocked
  AppTest scorer. The first baseline runtime build fits models **in memory**;
  it is not a feedback retraining/artifact write.
- Read-only inspection of `outputs/triage-qa/browser-feedback-final.db` found
  exactly **3 DoS tickets**: one `dashboard-json` and two `nsl-kdd-replay`.
  It found **0 reviews** and no NORMAL tickets. Real analyst data and model
  artifacts were not changed.

Example browser observations (not new evaluation benchmarks): KDDTest+ row 2
cleared with RF class confidence 97.9%, ISO risk 16.8% and fused confidence 8.0%.
The alert JSON derived from row 0 produced a DoS ticket. Published evaluation
protocols and metrics are unchanged.

## Commands Executed

From the repository in PowerShell, the main verification commands were:

```powershell
git status --short --branch
git log -5 --oneline --decorate
Get-Content -LiteralPath '..\AGENT-PROTOCOL.md'
Get-Content -LiteralPath '..\AGENTS.md'
Get-Content -LiteralPath '..\HANDOFF.md'
Get-Content -LiteralPath '..\PENDING_ITEMS.md'
Get-Content -LiteralPath '..\FIGMA_DESIGN_BACKLOG.md'
& '.\.venv\Scripts\python.exe' C:\Users\ojasp\.agents\skills\developing-with-streamlit\scripts\discover.py --project-dir 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant'
& '.\.venv\Scripts\python.exe' -X utf8 -m streamlit docs st.text_area
& '.\.venv\Scripts\python.exe' -m pytest tests/test_streamlit_app.py tests/test_ui.py
& '.\.venv\Scripts\python.exe' -m pytest --cov --cov-report=term-missing --cov-report=json:outputs/triage-qa/coverage.json --cov-fail-under=70
& '.\.venv\Scripts\python.exe' -m black --check streamlit_app.py src tests scripts
& '.\.venv\Scripts\python.exe' -m flake8 streamlit_app.py src tests scripts
git diff --check
git add -- streamlit_app.py src/ui.py tests/test_streamlit_app.py tests/test_ui.py
git commit -m "feat(ui): harden triage inputs and replay states"
```

Local browser fixture preparation and checks used these commands:

```powershell
& '.\.venv\Scripts\python.exe' -m outputs.triage-qa.prepare
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" outputs/triage-qa/browser.cjs
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" outputs/triage-qa/draft.cjs
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" outputs/triage-qa/evidence.cjs
```

The preview was launched with `Start-Process -WindowStyle Hidden`, redirected
logs, and the following Python arguments:

```powershell
& '.\.venv\Scripts\python.exe' -u -m streamlit run streamlit_app.py --server.port 8503 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

The browser explicitly selected the isolated QA database before submitting an
event. That is essential: launching the app alone still defaults to the normal
`state/soc_feedback.db`. SQLite verification used a `mode=ro` connection and:

```sql
SELECT source, predicted_class, COUNT(*) FROM tickets GROUP BY source, predicted_class;
SELECT COUNT(*) FROM reviews;
```

Code, tests and documentation were edited with `apply_patch`. Browser screenshots,
the report, fixtures and SQLite files live under ignored `outputs/triage-qa/`.
Those scripts use this machine's bundled runtime paths; **they are not a portable
fresh-clone test suite or browser CI**. E6 remains open and is the next repository
verification task.

Figma's read-only page metadata call succeeded, but inspection of the Foundations
page then returned the Starter-plan quota limit. No nodes were changed and no new
pixel-parity audit was possible. Figma IDs 2-5 remain open as drawings, despite
their verified app counterparts.

## Boundaries and Follow-ups

- Native forms batch edits until submission. **Submitted JSON**, including failed
  submissions, survives mode changes; **unsubmitted edits can be discarded** when
  changing mode. This is not autosave or durable draft storage.
- Results show the last scored event/source/model, not a guarantee that the visible
  unsubmitted input or newly selected provider/model has already been scored.
- Error handling covers the submitted Triage operation. Dataset discovery and the
  initial SQLite-store construction retain their existing bootstrap behaviour.
- Replay is delayed dataset replay, not a real network sensor or Kafka consumer in
  the UI. A partial run is not rolled back; completed alert tickets remain stored.
- Browser evidence covers sampled core paths, not every error/device combination,
  formal accessibility certification, Docker runtime or cloud/Ollama providers.
- Preserve historical Review queue and evolution-brief snapshots. Use
  [project_status.md](project_status.md) for current totals and remaining work.
- Next: commit a portable, isolated browser harness (E6), then test the real
  retraining path and implement candidate acceptance/promotion governance.
