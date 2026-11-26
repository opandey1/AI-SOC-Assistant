# Project Status and Verification

Snapshot: **26 November 2026**, `main` at `7c92836` (PR #7). This is a dated evidence
record, not a claim that every roadmap item is complete. It replaces the 9 October
snapshot; earlier task-level evidence stays in the task records linked at the end.

## Verification Snapshot

| Check | Evidence | Scope / limitation |
|---|---|---|
| Unit/AppTest tests | 355 passed: 84.13% in a clean export (Linux, Python 3.10.12) and 84.24% locally (Windows, Python 3.12.14), both on 25 November; 70% floor enforced | 2,753 runtime statements. Statement coverage is not semantic completeness. 924 dependency warnings remain. |
| Lint and dependency consistency | Black (47 files), flake8 and `pip check` pass | Same clean-export run. |
| GitHub Actions | PRs #4–#7 merged by the owner on 25–26 November. All jobs green after PR #5 (owner-confirmed) and on PR #6 [run 45666223921](https://github.com/opandey1/AI-SOC-Assistant/actions/runs/45666223921) and PR #7 [run 45676311393](https://github.com/opandey1/AI-SOC-Assistant/actions/runs/45676311393) (Codex read the logs; PR #7's Chromium job reported 23 passed) | Two Chromium failures were diagnosed from CI logs and fixed: font serving after PR #4 merged (fixed in PR #5), and a download-triggered rerun on PR #6's own run (fixed before merge). |
| Browser suite | 23 Chromium cases: 16 console workflows and 4 candidate lifecycle workflows at 1440/1024/390/320 px, plus 3 readiness checks; blocking CI job | Last full local run: all 23 passed on 25 November during the PR #5 fix (322.47 s). PRs #6 and #7 re-ran the affected cases locally. Synthetic fixtures, not a quality benchmark. |
| Real-data candidate check | Two governed candidates trained on KDDTrain+ and evaluated on KDDTest+ on 25 November; both rejected | Each added five correct corrections whose own predictions stayed the same. Each lowered accuracy and some class recalls while raising macro F1, so the zero-tolerance check rejected both. See [real-data review evidence](candidate_governance.md#real-data-review-evidence). Open under E3. |
| Dependency pins | Streamlit 1.62.0; [web-stack constraints](../constraints-web.txt) pin 8 ASGI packages for local installs, CI and Docker | Python 3.10 resolves Starlette 1.7.0; 3.11 and later resolve 1.10.0, which requires Python 3.11+. Other transitive dependencies still float. |
| Dependency advisories | `pip-audit` 2.7.3 on 26 November, Python 3.10 resolution of `requirements.txt`: 60 advisories in 8 packages | 42 in Pillow 12.3.0, all fixed only in 13.0.0, which Streamlit 1.62.0 excludes (`pillow<13`); 18 across 7 LangChain/LangGraph packages. Codex found the same counts in PR #7's Python 3.10 CI audit log. Not triaged. The CI audit step stays non-blocking. |
| Figma access | Last attempt on 9 October hit the Starter-plan quota | Not retried since. No drawing changes or inferred closures. |
| Evolution brief | Dated 155-test checkpoint | Not regenerated; this record holds the current numbers. |

## November Review Cycles

Claude reviewed the October code on 25 November in three cycles. Codex fixed each set
of findings, Claude approved the fixes, and the owner merged them.

| Area reviewed | Fixed | Merged |
|---|---|---|
| Candidate registry and feedback policy | A promote, rollback or report call on a registry folder without a database created an empty file that stopped the whole console; agreeing non-correction votes were counted as conflicts; six registry safeguards had no tests | PR #4 |
| Retraining, Model Operations and the Streamlit pin | Governance failures show a reason code and recovery step without exposing exception text; rejected candidates show their reasons beside the metrics; the Streamlit web stack is version-constrained | PR #6 |
| Triage, Review queue, UI helpers and browser harness | Source IPs are validated before scoring; a review is saved to the drafted ticket even if new alerts arrive before "Record review" | PR #7 |

PR #5 (Streamlit 1.62.0 for font serving) and the PR #6 download fix were CI-driven
repairs outside the review cycles. Two review findings were deferred and stay open:

- **E3 — acceptance rule.** On the real data, both candidates tried added five correct
  corrections whose own predictions stayed the same. Each lowered accuracy and some
  class recalls while raising macro F1, and the zero-tolerance check rejected both.
  These are two observed trade-offs under the current rule. How much comparable fits
  move the metrics has not been measured; matched controls should come before choosing
  a tolerance or a paired test on changed predictions with per-class floors.
- **G2 — weight caps.** The 10% total / 5% per-label caps are fractions of the 336,715
  oversampled training rows. They first scale the explicit sample weights at a cohort of
  1,347 consensus corrections at the default weight of 25 (562 at the maximum of 60;
  504 and 210 without oversampling). Smaller cohorts keep their full requested sample
  weight, and the caps act before RF class balancing.

## Closed Tracking Items

The stable IDs below refer to the workspace's `PENDING_ITEMS.md` and
`FIGMA_DESIGN_BACKLOG.md`, which live one level above this Git repository and are not
included in its commits.

- **B1 - Git attributes:** catch-all precedes specific rules; PDFs and images are binary.
- **B2 - Interpreter verification:** actual local versions are recorded above. Standardising
  the local interpreter on the CI matrix is a separate optional follow-up.
- **C1 - Ticket formatting:** template headings, sample ticket and tests are coordinated.
- **C4 - Review controls:** native captioned disposition cards and coloured family pills,
  explicit choice/name validation, ticket-scoped drafts and safe queue resets. Since PR #7
  a submitted review is attributed to the drafted ticket. Live Figma parity is unverified.
- **C5 - Persistence caption:** states that only generated alert tickets are stored.
- **D1 - JSON browser verification:** real normal/alert submissions, invalid input,
  rendered SHAP evidence and ticket download checked against an isolated database.
- **D2 - Narrow viewports, sampled core paths:** all three workspaces have four-width
  evidence. This closes the named gap, not whole-app or device certification.
- **E1 - Coverage measurement:** unexecuted runtime files stay in the denominator; floor 70%.
- **E2 - Dependency audit capability:** CI runs `pip-audit`, non-blocking. Closing the
  scanner setup does **not** close advisory remediation (see the advisory row above).
- **E6 - Portable browser suite and CI:** isolated fixtures, workflow/layout assertions and
  a blocking Chromium job; the two November Chromium failures were fixed in PRs #5 and #6.
- **F1 - LLM fallback telemetry:** rejection reason codes and provider failures are logged
  without ticket payloads. This is not a complete application-event log stream (F2).
- **G1 - Default eligible cohort minimum:** five consensus-approved corrections. A
  prototype policy, not statistical assurance.
- **G3 - Local candidate lifecycle:** separate bundles, RF non-regression checks, explicit
  promotion and rollback, integrity and freshness checks, transactional selection and
  history. Both candidates tried on the real data were rejected by the acceptance
  check (E3).
- **Figma item 1 - Evaluation panel:** completed in Figma and implemented in the app via
  `abb36b9` and `4ecfcf1`; [screen reference](https://www.figma.com/design/qh0Rkefos51ldMTGsL6FN0?node-id=12-4).

## Partial Progress, Not Closure

- **E3:** functional real-fit tests and a local RF non-regression check exist; two
  real-data candidates were rejected under the zero-tolerance rule while macro F1 rose.
  Matched controls, a revised acceptance rule, independent quality evaluation and
  rare-class floors remain open.
- **G2:** two-label unanimous agreement, conflict holdout and explicit sample-weight caps
  apply to governed candidates. At the real training size the caps only scale cohorts of
  1,347 or more corrections at weight 25, and they cover explicit sample weights only,
  not RF class balancing. Free-text labels are not authenticated (G5).
- **G4:** cohort status, conflict holdout and per-label peer-agreement diagnostics exist;
  agreeing non-correction votes no longer count as conflicts (PR #4). Peer agreement is
  not label correctness; ground-truth reliability analysis and adjudication remain open.
- **E7/E8:** local bundles link dataset, source and correction hashes, review IDs, Git
  state, parameters, versions, metrics and digests; the registry no longer creates empty
  storage on read. Complete dependency closure, archived dirty sources, signed lineage and
  remote/shadow operations remain open.
- **E9 - Dependency locking:** the Streamlit web stack is constrained; every other
  transitive dependency, including Pillow, still resolves to the newest allowed version.
- **D3:** the Docker build is confirmed in CI; nothing starts the image (E5).
- **Figma items 2-6 and 29:** app counterparts are implemented and browser-checked;
  the drawings remain historically open. ProtocolCard is reusable; the primitive library
  is not complete.

## Next Work, In Order

1. **Acceptance rule (E3):** run matched controls on the real files to measure how much
   comparable fits move the metrics, then choose and implement an acceptance rule on
   that evidence.
2. **Weight caps (G2):** make the budget bind at real data size and account for class
   balancing, with the UI and docs stating the real thresholds.
3. **Dependencies (E2, E9, E4, E5):** triage the 60 advisories (the Pillow set needs a
   Streamlit release that accepts Pillow 13; the LangChain set needs a stack migration),
   lock every installed package, scan the image and add a container smoke test.
4. **Figma foundations (27-31)** when quota permits; recheck item 6.
5. Remaining: G4/G5, F2-F4, C2/C3, D4 and H1-H3, by risk and benefit.

Profile pin and README publication (I1/I2) are unverified and must not be marked
complete from this repository alone.

## How This Snapshot Was Verified

Run on 25-26 November against a clean `git archive` of the merged commits, Linux,
Python 3.10.12, with `requirements.txt` (including the web-stack constraints) installed:

```bash
python -m pytest --cov --cov-report=term-missing --cov-fail-under=70
python -m black --check streamlit_app.py src tests browser_tests scripts
python -m flake8 streamlit_app.py src tests browser_tests scripts
python -m pip check
python -m pip_audit --requirement requirements.txt --progress-spinner off --format json
```

The Windows Python 3.12.14 figures, the browser runs and the PR #6/#7 CI log results are
from Codex's November HANDOFF entries. Commands for earlier snapshots are in the task records and in this file's Git
history.

## Task Records

- [Model Operations UI](model_operations_ui.md)
- [Review queue UI](review_queue_ui.md)
- [Triage workflow UI](triage_workflow_ui.md)
- [Browser testing](browser_testing.md)
- [Retraining tests](retraining_testing.md)
- [Candidate governance](candidate_governance.md)
- [Feedback governance](feedback_governance.md)
