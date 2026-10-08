# Project Status and Verification

Snapshot: **9 October 2026**, following the merge of `4ecfcf1` into `main`.
This is a dated evidence record, not a claim that every roadmap item is complete.

## Verification Snapshot

| Check | Evidence | Scope / limitation |
|---|---|---|
| Local automated tests | 155 passed; 73.63% coverage; 70% floor enforced | Rerun on 9 October. `src/retrain.py` remains at 37%; tests do not execute a full feedback retraining run. Dependency warnings remain. |
| Local tools | Python 3.13.14; pytest 8.3.4; Black and flake8 pass | Python 3.13 is outside the supported CI matrix, not a newly certified version. |
| GitHub Actions | Owner confirmed Python 3.10, 3.11, 3.12 jobs and Docker build passed on 9 October | Confirmation is from the owner, not an independently retrieved CI run. Workflow includes dependency consistency, format, lint, coverage, Compose validation, build, and non-root image-user inspection. |
| Model Operations browser QA | 1440, 1024, 390, and 320 px checked on 7 October | Fonts, card fit, panel stacking, track ratios, and exceptions; not whole-app mobile certification. See [implementation record](model_operations_ui.md). |
| Figma access | Read-only MCP call on 9 October returned Starter-plan quota limit | No new live file audit or design edits were possible. Historical state items must be rechecked before closing them. |
| Evolution brief | Regenerated from [versioned source](../scripts/generate_evolution_pdf.py) | Current date/count, scoped UI evidence, and explicit remaining governance gaps. PDF text and both rendered pages checked. |

## Closed Tracking Items

The stable IDs below refer to the workspace's `PENDING_ITEMS.md` and
`FIGMA_DESIGN_BACKLOG.md`, which live one level above this Git repository and
are not included in its commits. Both trackers were reconciled in this session.

- **B1 - Git attributes:** catch-all precedes specific rules; PDFs and images are binary.
- **B2 - Interpreter verification:** actual local versions recorded above. Standardising
  the local interpreter to the CI matrix is a separate optional follow-up, not completed.
- **C1 - Ticket formatting:** template headings, sample ticket, and tests are coordinated.
- **E1 - Coverage measurement:** CI measures unexecuted runtime files too; floor is 70%.
- **E2 - Dependency audit capability:** CI runs `pip-audit`, but it is non-blocking.
  Closing scanner setup does **not** close advisory remediation.
- **F1 - LLM fallback telemetry:** rejection reason codes and provider failures are logged
  without ticket payloads. This is not a complete JSON application-event log stream.
- **Figma item 1 - Evaluation panel:** completed in Figma and implemented in the app
  via `abb36b9` and `4ecfcf1`; [screen reference](https://www.figma.com/design/qh0Rkefos51ldMTGsL6FN0?node-id=12-4).

## Partial Progress, Not Closure

- **Figma item 29:** ProtocolCard now has reusable variants/instances, but the recurring
  primitive library is not complete. The original 31-item inventory has one closed,
  one partial, and 29 historically open items not live-rechecked in this session.
- **D2:** Model Operations has responsive browser evidence; other views still need it.
- **D3:** Docker CI build is confirmed; local Docker is unavailable. A passing build
  does not prove the container serves the app or scores an event end to end (E5).
- **E8:** artifact metadata already records version, timestamp, feedback ticket IDs,
  cohort size, weight, and which models changed. Commit/dataset provenance, a registry,
  and a candidate-to-active promotion boundary are still absent.

## Next Work, In Order

1. **Review queue UI (C4 / Figma 6):** reconcile disposition cards and corrected-family
   pills with native accessible controls; handle the no-selection state. Fix its caption
   claiming every scored connection is stored: only generated alert tickets are persisted.
2. **Reusable Figma foundations (27-31):** populate token/type specimens, instance
   MetricTile/EvidenceRow, apply text styles, and define interactive states before
   drawing more duplicate screen states. Resume once quota permits.
3. **Triage alignment (2-5 and related states):** cleared verdict, initial empty state,
   JSON input, and live replay; verify all input modes and narrow widths.
4. **Portable browser harness (E6):** replace the ignored, machine-specific manual
   harness with reproducible fixtures and CI screenshots/assertions.
5. **Retraining governance (G1-G5 / E3 / E8):** exercise the real training path, retain a
   separate candidate, define per-class/macro-F1 acceptance criteria, then explicit
   promotion and rollback. The UI slider is not an analyst-trust control.
6. **Fresh advisory triage and operational checks (E2 / E4 / E5):** re-audit before
   quoting a current advisory count, test migration compatibility, scan the image, and
   run it with non-root volumes and an end-to-end smoke test.

Other open work remains: SHAP faithfulness validation; structured application logging,
drift and retention controls; experiment provenance; architecture SVG typography;
modern dataset validation and a live feature adapter. Profile pin/README publication
is unverified and must not be marked complete based on this repository alone.

## Commands Executed

Run from the repository in PowerShell. Commands below were executed for this cleanup;
the marker command is internal artifact bookkeeping, not a project prerequisite.

```powershell
git status --short --branch
git log -5 --oneline --decorate
Get-Content -LiteralPath '..\AGENT-PROTOCOL.md'
Get-Content -LiteralPath '..\AGENTS.md'
Get-Content -LiteralPath '..\HANDOFF.md'
Get-Content -LiteralPath '..\PENDING_ITEMS.md'
Get-Content -LiteralPath '..\FIGMA_DESIGN_BACKLOG.md'
Get-Content -LiteralPath '.github/workflows/ci.yml'
Get-Content -LiteralPath '.gitattributes'
Select-Xml -LiteralPath 'docs/soc_architecture.svg' -XPath "//*[local-name()='text']" | Measure-Object | Select-Object Count
& '.\.venv\Scripts\python.exe' -c "import sys, pytest; print(sys.version); print('pytest', pytest.__version__)"
& '.\.venv\Scripts\python.exe' -m pytest --cov --cov-report=term-missing --cov-report=json:outputs/tracking-cleanup-coverage.json --cov-fail-under=70
& '.\.venv\Scripts\python.exe' -m black --check streamlit_app.py src tests scripts
& '.\.venv\Scripts\python.exe' -m flake8 streamlit_app.py src tests scripts
& '.\.venv\Scripts\python.exe' -m pip check
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" scripts/generate_evolution_pdf.py
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdfinfo.exe" docs/SOC_Assistant_Evolution.pdf
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe" -r 120 -png docs/SOC_Assistant_Evolution.pdf outputs/tracking-cleanup/evolution
git diff --check
git check-attr text diff merge -- docs/SOC_Assistant_Evolution.pdf
git add -- README.md docs/project_status.md scripts/generate_evolution_pdf.py docs/SOC_Assistant_Evolution.pdf
git commit -m "docs: reconcile post-merge tracking and refresh evolution brief"
```

Tracker/source edits used `apply_patch`. Figma inspection used a read-only plugin API
call and failed on quota; it did not modify the file. PDF text assertions used the
bundled `pypdf`, and both rendered pages were visually inspected. No model retraining,
SQLite edits, UI changes, dependency upgrades, or pushes were performed.
