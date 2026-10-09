# Project Status and Verification

Snapshot: **9 October 2026**, following merged browser baseline `82c652a`, published
retraining PR head `869c530` and local browser-startup fix `767294f` on
`codex/retraining-regression-tests`.
This is a dated evidence record, not a claim that every roadmap item is complete.

## Verification Snapshot

| Check | Evidence | Scope / limitation |
|---|---|---|
| Unit/AppTest tests | 226 passed again locally, 81.25%; preceding clean `a46b44c` measured 81.07%; 70% floor enforced | Current fix changes browser tools/tests only. Both reports retain 2,229 production statements. App 97%; retraining/model-store 100% statements, not semantic/branch completeness. 924 dependency warnings remain. |
| Local tools | Python 3.13.14; pytest 8.3.4; Black (41 files), flake8 and pip check pass | Python 3.13 is outside the supported CI matrix, not a newly certified version. |
| GitHub Actions | Baseline [run 37901592150](https://github.com/opandey1/AI-SOC-Assistant/actions/runs/37901592150) passed all jobs. Later PR [run 37907779760](https://github.com/opandey1/AI-SOC-Assistant/actions/runs/37907779760) passed Python 3.10/3.11/3.12 and Docker; Chromium had 14 passed/two setup errors | Supplied log and public API conclusions checked for PR merge `775d1b0` / head `869c530`. Immediate read of an initially empty database widget caused the two failures before workflow inference. Local startup fix `767294f` needs a new remote run; do not merge based on the earlier baseline green. |
| Real feedback retraining | 30 focused retraining/persistence cases passed, including 25 newly added retraining cases | Actual RF/ISO/preprocessing/SQLite and artifact-consuming SHAP runtime execute on tiny synthetic fixtures. Missing-class probability lookup fixed. Weighting/latest-review eligibility, metrics, report/CLI integration and tested failed-fit/writer preservation verified. Not E3 quality gating or G3 candidate governance. See [implementation and commands](retraining_testing.md). |
| Model Operations browser QA | 1440, 1024, 390, and 320 px checked on 7 October | Fonts, card fit, panel stacking, track ratios, and exceptions; not whole-app mobile certification. See [implementation record](model_operations_ui.md). |
| Review queue browser QA | 1440, 1024, 390, and 320 px checked on 9 October | Native selection, family colours/keyboard operation, validation, an actual synthetic SQLite save, empty states, responsive geometry and screenshots. No real reviews or model artifacts overwritten. See [implementation record and commands](review_queue_ui.md). |
| Triage browser QA | 20 state/width geometry checks at 1440, 1024, 390 and 320 px on 9 October, plus evidence-row checks | Actual deterministic dataset/JSON/replay scoring, JSON errors, ticket download, retained feed and alert-only writes to isolated SQLite. No page errors or document overflow. Not every device/failure combination. See [implementation record and commands](triage_workflow_ui.md). |
| Portable browser suite | 19 Chromium cases passed from clean Git export `767294f` (284.34 seconds) | 16 isolated console workflows plus three controlled-HTML readiness checks for delayed/empty/wrong database values. Exact-path assertion now waits for hydration without editing input; no console errors/external HTTP. Does not click Retrain. Not quality/pixel/accessibility certification. See [startup diagnosis, fix and commands](browser_testing.md). |
| Figma access | Last attempt on 9 October: page metadata succeeded; Foundations inspection returned Starter-plan quota limit | Not retried in the browser or retraining pass. No content audit/drawing changes or inferred drawing closures. |
| Evolution brief | Regenerated from [versioned source](../scripts/generate_evolution_pdf.py) in the preceding cleanup | Preserves that dated 155-test checkpoint and Model Operations evidence; not regenerated for Review queue or Triage. This status record holds the latest test count. |

## Closed Tracking Items

The stable IDs below refer to the workspace's `PENDING_ITEMS.md` and
`FIGMA_DESIGN_BACKLOG.md`, which live one level above this Git repository and
are not included in its commits. Both trackers were reconciled after the startup fix.

- **B1 - Git attributes:** catch-all precedes specific rules; PDFs and images are binary.
- **B2 - Interpreter verification:** actual local versions recorded above. Standardising
  the local interpreter to the CI matrix is a separate optional follow-up, not completed.
- **C1 - Ticket formatting:** template headings, sample ticket, and tests are coordinated.
- **C4 - Review controls:** native captioned disposition cards and coloured family
  pills, explicit choice/name validation, isolated drafts and queue selection resets.
  Implementation is verified; exact live Figma parity remains unverified.
- **C5 - Persistence caption:** accurately states generated alert tickets only.
  No persistence semantics or schema were changed.
- **D1 - JSON browser verification:** real normal/alert submissions, invalid input,
  rendered SHAP evidence and ticket download checked against an isolated database.
- **D2 - Narrow viewports, sampled core paths:** all three workspaces now have
  four-width evidence; Triage includes dataset, JSON and replay inputs. This closes
  the named browser-check gap, not whole-app/device certification.
- **E1 - Coverage measurement:** CI measures unexecuted runtime files too; floor is 70%.
- **E2 - Dependency audit capability:** CI runs `pip-audit`, but it is non-blocking.
  Closing scanner setup does **not** close advisory remediation.
- **E6 - Portable browser suite/CI implementation:** committed isolated fixtures,
  workflow/layout assertions and blocking Chromium job, with a complete clean-export
  local run. Its first merged remote run is independently verified successful above.
  The later PR startup failure is fixed locally; corrected remote CI remains a merge blocker.
- **F1 - LLM fallback telemetry:** rejection reason codes and provider failures are logged
  without ticket payloads. This is not a complete JSON application-event log stream.
- **Figma item 1 - Evaluation panel:** completed in Figma and implemented in the app
  via `abb36b9` and `4ecfcf1`; [screen reference](https://www.figma.com/design/qh0Rkefos51ldMTGsL6FN0?node-id=12-4).

## Partial Progress, Not Closure

- **Real-retraining test task:** complete with 28 additional cases and an absent-class
  probability fix. **E3 remains open:** synthetic functional checks are not an
  independent quality benchmark, even though actual estimators fit and reports execute.
- **Figma item 29:** ProtocolCard now has reusable variants/instances, but the recurring
  primitive library is not complete. The original 31-item inventory has one closed,
  one partial, and 29 historically open items not live-rechecked in this session.
- **Figma items 2-5:** Triage app counterparts are implemented and browser-checked;
  cleared/empty/JSON/replay drawings remain historically open, not live-rechecked.
- **Figma item 6:** the app now has verified no-selection/empty-filter states and
  mapped controls. The Figma drawing is still historically open pending live access.
- **D3:** Docker CI build is confirmed; local Docker is unavailable. A passing build
  does not prove the container serves the app or scores an event end to end (E5).
- **E8:** artifact metadata already records version, timestamp, feedback ticket IDs,
  cohort size, weight, and which models changed. Commit/dataset provenance, a registry,
  and a candidate-to-active promotion boundary are still absent.

## Next Work, In Order

1. **Reusable Figma foundations (27-31):** populate token/type specimens, instance
   MetricTile/EvidenceRow, apply text styles, and define interactive states before
   drawing more duplicate screen states. Resume once quota permits; also recheck
   item 6 against the verified Review queue implementation.
2. **Publish startup fix and verify the existing PR:** its Python matrix and container
   build passed, but Chromium failed before two workflows. Publish `767294f` and
   require the updated PR's CI to pass before merging; baseline green is not clearance.
3. **Retraining governance (G1-G5 / E3 / E8):** next repository implementation:
   retain a separate candidate, define cohort/influence and per-class/macro-F1
   acceptance criteria, then explicit promotion and rollback with provenance.
   Real functional training tests now exist; the UI slider is not an analyst-trust control.
4. **Fresh advisory triage and operational checks (E2 / E4 / E5):** re-audit before
   quoting a current advisory count, test migration compatibility, scan the image, and
   run it with non-root volumes and an end-to-end smoke test.

Other open work remains: SHAP faithfulness validation; structured application logging,
drift and retention controls; experiment provenance; architecture SVG typography;
modern dataset validation and a live feature adapter. Profile pin/README publication
is unverified and must not be marked complete based on this repository alone.

The Triage pass in [triage_workflow_ui.md](triage_workflow_ui.md) includes executed
commands and its boundaries: submitted JSON retention is not autosave, replay is
dataset replay, and submission error handling does not cover every bootstrap
configuration failure. Native Review queue and Triage checks use isolated fixtures;
no real analyst state, model artifact or evaluation benchmark was overwritten.
The subsequent [browser pass](browser_testing.md) changes only test tooling/CI/docs;
it fits tiny synthetic models in memory and appends synthetic reviews inside
temporary workspaces. It does not alter the production UI or retraining policy.
The subsequent [retraining pass](retraining_testing.md) adds actual training/failure
tests and fixes an absent-baseline-class probability lookup. It writes models/reports
only in temporary test workspaces, changes no production UI or acceptance policy,
and does not overwrite published evaluation figures or operational analyst state.

## Tracking Cleanup Commands

The following historical commands were executed for `19d750d`, before the Review
queue pass. Its commands and detailed results are in [review_queue_ui.md](review_queue_ui.md).
Run from the repository in PowerShell. Commands below were executed for that cleanup;
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
SQLite edits, UI changes, dependency upgrades, or pushes were performed **during
that tracking cleanup**. The subsequent Review queue pass changes the UI and writes
synthetic test feedback only, as recorded above.
