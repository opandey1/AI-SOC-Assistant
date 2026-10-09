# Feedback Agreement and Influence Governance

Implementation after merged `c88f18a`, 9 October 2026. The owner confirms the
candidate-governance PR passed CI before merging. This implementation is a new
local branch, `codex/feedback-influence-governance`, and requires its own CI.

## 1. Consensus-Approved Training Cohorts (G2)

`src/feedback.py:FeedbackStore.review_snapshot` reads review decisions and raw
records in one SQLite query. `src/feedback_policy.py:build_cohort` retains the
latest decision **per ticket and canonical reviewer label**, not just the latest
decision across everyone. NFKC, case folding and whitespace normalization collapse
accidental name variants. Multiple reviews under one canonical name count once.

The default governed policy requires at least two distinct labels, unanimous
`false_positive` disposition and the same valid corrected family. A disagreeing
class, confirmed attack or investigation vote holds the entire ticket out; a
majority cannot override a conflict. Unanimous non-corrections are excluded. A
single correction awaits agreement. Superseding one's own decision can resolve
a conflict, with the old review still retained in append-only history. No review
is deleted and no SQLite schema migration is needed.

The existing five-correction minimum counts **consensus-approved tickets**. It does
not count votes, repeated reviewer names or conflicting tickets. This policy applies
to the console, retraining CLI and `ModelRegistry.create_candidate`. The historical
`feedback_examples()` helper remains a latest-overall query for low-level compatibility;
it is not the governed cohort. Explicit-output `retrain_from_feedback` callers must
pass `feedback_policy=FeedbackPolicy()` to opt into this policy. That library fitter
is not the selection/promotion API and must never target an active bundle.

**Trust limitation:** names are still free text. Two labels do not prove two humans;
aliases, collusion and forged names remain possible. This is a trusted-local prototype
workflow, not authenticated consensus or poisoning prevention. G5 remains open.

## 2. Actual RF Sample-Weight Budgets (G2)

`weight_plan` treats the UI's 1-60 slider as a **requested** per-correction weight,
not a promise that every correction receives it. Let `B` be the number of original
training rows after the existing optional random oversampling, each with weight 1.
Total explicit correction sample weight is capped at `0.10 * B`. Each reviewer's credited share
is capped at `0.05 * B`. These fractions are conservative prototype defaults, not
empirically optimized thresholds.

For each eligible correction, split requested weight equally between all endorsing
labels for influence accounting. Compute:

```text
scale = min(1, total_cap / requested_total,
               each_reviewer_cap / each_reviewer_requested_share)
effective_per_row_weight = requested_per_row_weight * scale
```

All correction rows are scaled uniformly. This preserves equal correction weights
and deterministic behavior, but an overrepresented reviewer can throttle the whole
cohort and leave budget unused. The original balanced training rows remain weight 1.
The actual capped vector is passed into the production RF fitter. ISO fitting and
calibration reuse are unchanged; feedback still updates only the RF.

For the 40-row unbalanced test fixture, five corrections requested at 25 receive
0.8 each: total 4, credited 2 each to two endorsers. With the 60-row oversampled
fixture they receive 1.2 each: total 6, credited 3 each. Large training populations
can retain the requested 25 if neither cap binds. Effective weights can be below 1.
Weight totals are numerical constraints (subject to floating-point roundoff), not
bounds on causal model influence, tree structure, label correctness or drift.
The existing RF `class_weight="balanced"` remains unchanged and is computed from
the augmented class population. scikit-learn multiplies those class weights by the
explicit sample weights. Therefore these caps govern the **sample_weight input**,
not the combined class-weighted mass; rare/novel-class corrections can still receive
larger relative influence after class balancing. Combined-weight/prior governance
and calibrated thresholds remain follow-up work, so G2 is partial rather than a
production influence-control closure.

## 3. Conflict and Reviewer Diagnostics (G4)

Model Operations now counts eligible, awaiting-agreement, conflicting and excluded
tickets. The **Feedback governance** expander contains ticket IDs, exact latest
review IDs, participating labels, status and per-label counts. It also records
pairwise peer-agreement numerator/denominator/rate on overlapping tickets. Review
detail displays the latest saved reviewer label and exact review ID; latest disposition remains the
queue/filter view, not the training eligibility rule.

The cohort audit download omits raw telemetry, notes, ticket text and IP columns.
It includes reviewer labels and ticket/review IDs, which remain potentially sensitive.
Training reports/model metadata include the policy, cohort snapshot SHA-256, all
endorsing review IDs, requested/effective weights, total/reviewer budgets and actual
attributed weights. The report download records full weight attribution.

Peer agreement is **not accuracy**: two people can agree on the wrong label. No
ground-truth reviewer reliability score, automatic analyst ranking/blacklisting,
authenticated arbitration or adjudication queue is implemented. Conflict holdout
and diagnostics are completed local functionality; broader bias analysis remains open.
Snapshot construction currently scans review history, and peer comparisons are quadratic
in reviewer count per ticket. This is suitable for the small local prototype; a large SOC
needs incremental aggregation, bounded UI queries and a governed adjudication service.

## 4. Review Freshness and Migration

The governed fitter hashes the latest decision snapshot plus policy, then checks
again before saving. A review change during fitting aborts the save. At promotion,
`ModelRegistry._change` recomputes the cohort from the candidate's original database
and verifies the snapshot and recorded budget plan. A changed/retracted/new review
requires a new candidate, even if a re-review repeats the same class. The fingerprint
is deliberately conservative and includes decisions on reviewed, noneligible tickets.

Promotion holds SQLite `BEGIN IMMEDIATE` on the feedback database until the separate
registry transaction commits selection/history. Review writes wait or time out;
rollback/error releases the lock. This prevents a review write slipping between the
freshness check and pointer commit. It is not a distributed database protocol or
protection against filesystem-owner replacement/tampering. Hashes remain unsigned.

Missing feedback storage is opened `mode=rw` and fails without recreating a blank
database. The stored absolute database path means moving a candidate/database is not
automatically portable: train a new candidate at the new location. No production
database, artifact or published metric was modified during this implementation.

Old candidates lacking the current policy remain readable but cannot be newly
promoted. Existing active models, legacy imports and explicit rollback targets remain
usable; this pass does not silently unselect or retroactively certify them. The UI
disables promotion for old-policy or changed-review candidates, and the backend checks
again. New candidates keep the previous RF-only evaluation non-regression gate.

## Verification and Commands

Full local unit/AppTest verification: **326 passed in 135.70 seconds**, **83.72%**
coverage across 2,697 runtime statements (was 2,542), unchanged 70% floor. Forty-eight
cases added; policy 100%, app/registry 96%, retraining 98%, model persistence 100%
statement coverage. Black checks 46 files; flake8, pip check and whitespace checks pass.
The 924 existing dependency warnings remain. Clean exported `ca44e3f` also passes
all **326 cases in 101.15 seconds**, **83.57%** coverage; source hashes replace Git
metadata outside a real checkout. Retraining is 96% clean / 98% local, policy 100%
and app/registry 96%. No ignored operational data/models/state are exported.
All **23 Chromium cases pass in 366.74 seconds** from the same clean export:
20 real-console workflows at 1440, 1024, 390 and 320 pixels, plus three controlled
startup-readiness checks. The four candidate workflows collect five first reviews,
verify retraining remains disabled, add five agreeing second reviews, introduce and
resolve a class conflict, train, explicitly promote and roll back. Each verifies
12 append-only reviews and two selection-history records. JUnit has zero
failures/errors/skips; all 20 console reports have zero page errors or blocked
external browser HTTP requests. Final screenshots were inspected at all four widths.
These are workflow/layout checks, not pixel-diff, accessibility or all-device certification.
Synthetic fixtures exercise functional correctness, not independent detection quality.
E3 independent benchmarks/rare-class floors, G5 identity and E7/E8 complete replay
and signed/remote lineage remain open. No Figma nodes or dependency pins changed.

Commands executed from the repository in PowerShell:

```powershell
git status --short --branch
git log -8 --oneline --decorate
git switch -c codex/feedback-influence-governance
& '.\.venv\Scripts\python.exe' 'C:\Users\ojasp\.agents\skills\developing-with-streamlit\scripts\discover.py' --project-dir 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant'
& '.\.venv\Scripts\python.exe' -m black src tests browser_tests streamlit_app.py
& '.\.venv\Scripts\python.exe' -m pytest tests/test_feedback_policy.py tests/test_feedback.py tests/test_model_registry.py tests/test_retrain.py tests/test_streamlit_app.py -q --disable-warnings
& '.\.venv\Scripts\python.exe' -m pytest tests/test_model_registry.py tests/test_feedback_policy.py tests/test_streamlit_app.py -q --disable-warnings
& '.\.venv\Scripts\python.exe' -m flake8 streamlit_app.py src tests browser_tests scripts
& '.\.venv\Scripts\python.exe' -m pytest browser_tests/test_candidates.py --browser chromium -k '1440 or 320' --tracing retain-on-failure --screenshot only-on-failure --output outputs/feedback-browser-focused-final --junitxml outputs/feedback-browser-focused-final/junit.xml
& '.\.venv\Scripts\python.exe' -m pytest --override-ini addopts='' -q --cov --cov-report=term-missing --cov-report=json:outputs/feedback-unit-final-coverage.json --cov-fail-under=70
& '.\.venv\Scripts\python.exe' -m pytest --override-ini addopts='' -q --cov --cov-report=term-missing --cov-report=json:outputs/feedback-unit-verified-coverage.json --cov-fail-under=70
& '.\.venv\Scripts\python.exe' -m pytest tests/test_feedback.py tests/test_streamlit_app.py --override-ini addopts='' -q --disable-warnings
& '.\.venv\Scripts\python.exe' -m black --check streamlit_app.py src tests browser_tests scripts
& '.\.venv\Scripts\python.exe' -m pip check
git diff --check
git add -- src/feedback.py src/feedback_policy.py src/retrain.py src/model_registry.py streamlit_app.py tests/test_feedback.py tests/test_feedback_policy.py tests/test_model_registry.py tests/test_retrain.py tests/test_streamlit_app.py browser_tests/test_candidates.py browser_tests/test_console.py
git diff --cached --check
git commit -m "feat(feedback): enforce local consensus and influence budgets"
```

Source/document edits use `apply_patch`; Black is the only mechanical rewrite.
Initial focused unit failures were outdated exact metadata and positional AppTest
status expectations; corrected to include governance and identify status by label.
The initial browser run attempted to read nonexistent saved reviewer text; adding
the latest-reviewer audit field provides durable attribution feedback. No forced
clicks, blanket retries, sleeps, weakened CI assertions or production-data tests
were introduced. Local Python 3.13.14 remains outside CI's supported 3.10-3.12 matrix.

Further browser checks identified that saved reviewer names are not unique per action,
the default dataframe row height did not match the existing 35-pixel layout arithmetic,
and stale/new button nodes can coexist during a rerun. Review IDs and exact selected
ticket readiness now provide durable assertions; native `row_height=35` aligns the
actual table with the layout (unit assertion added). Browser action selectors exclude
stale ancestors and accept the Material icon prefix in the audit-download name.
Panel containment is awaited before screenshots. A failed clean-export batch was
interrupted after diagnosing the selector; its leftover isolated server was matched
to its temporary ownership nonce/port/process tree before cleanup. No user previews
were stopped by that cleanup. Failed traces are retained under ignored outputs.

Clean-export verification commands (working directory `outputs/feedback-governance-final`
for pytest; export/commit commands run from the original repository):

```powershell
git add -- browser_tests/test_console.py browser_tests/test_candidates.py
git commit -m "test(browser): wait for live feedback governance controls"
git archive --format=zip --output=outputs/feedback-governance-final.zip HEAD
if (Test-Path -LiteralPath 'outputs\feedback-governance-final') { throw 'Clean-export destination already exists.' }
Expand-Archive -LiteralPath 'outputs\feedback-governance-final.zip' -DestinationPath 'outputs\feedback-governance-final'
& 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\.venv\Scripts\python.exe' -m pytest --override-ini addopts='' -q --cov --cov-report=term-missing --cov-report=json:'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\outputs\feedback-clean-unit-coverage.json' --cov-fail-under=70
& 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\.venv\Scripts\python.exe' -m pytest browser_tests --browser chromium --tracing retain-on-failure --screenshot only-on-failure --output 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\outputs\feedback-clean-browser-final' --junitxml 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\outputs\feedback-clean-browser-final\junit.xml'
```

Read-only result checks used XML/JSON parsers rather than counting text matches:

```powershell
[xml]$junit = Get-Content -LiteralPath 'outputs\feedback-clean-browser-final\junit.xml' -Raw
$junit.testsuites.testsuite | Select-Object tests, errors, failures, skipped, time
$reports = @(Get-ChildItem -LiteralPath 'outputs\feedback-clean-browser-final\console' -Filter browser.json -Recurse | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json })
[pscustomobject]@{ ConsoleReports=$reports.Count; PageErrors=($reports | ForEach-Object { $_.page_errors.Count } | Measure-Object -Sum).Sum; BlockedOutbound=($reports | ForEach-Object { $_.blocked_outbound_requests.Count } | Measure-Object -Sum).Sum }
```

A fresh intentional preview runs at `http://localhost:8502/`, launcher 25500 / Python
child 16152. Health returned HTTP 200 / `ok`. Existing previews were left running;
no scoring, review or retraining action was performed on the real-repository preview.
Imported modules load freshly instead of relying on an older server's module cache.
The task's isolated test servers are stopped on teardown.

Preview commands executed from the original repository:

```powershell
$port = 8501
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)
while ($listeners.LocalPort -contains $port) { $port++ }
$process = Start-Process -FilePath (Resolve-Path '.\.venv\Scripts\python.exe').Path -ArgumentList '-m','streamlit','run','streamlit_app.py','--server.port',"$port",'--server.address','127.0.0.1','--server.headless','true','--browser.gatherUsageStats','false' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -RedirectStandardOutput 'outputs\feedback-preview.stdout.log' -RedirectStandardError 'outputs\feedback-preview.stderr.log' -PassThru
[pscustomobject]@{LauncherPID=$process.Id; URL="http://127.0.0.1:$port"} | ConvertTo-Json
Invoke-WebRequest -Uri 'http://127.0.0.1:8502/_stcore/health' -UseBasicParsing | Select-Object StatusCode, Content | ConvertTo-Json
Get-CimInstance Win32_Process -Filter 'ParentProcessId=25500' | Select-Object ProcessId, ParentProcessId, CommandLine | ConvertTo-Json
```

Documentation and tracking cleanup commands:

```powershell
git diff --check
git add -- README.md docs/feedback_governance.md docs/candidate_governance.md docs/browser_testing.md docs/project_status.md
git diff --cached --check
git commit -m "docs: record feedback governance verification and trust limits"
git status --short --branch
git log -3 --oneline
```

Parent `PENDING_ITEMS.md` / `FIGMA_DESIGN_BACKLOG.md` were updated and `HANDOFF.md`
was appended using the escalated `--codex-run-as-apply-patch` helper. They are outside
this Git repository and are not included in its commits. No push was performed.

Example usage below is guidance, **not operational commands executed here**. Each
eligible ticket needs two agreeing labels; collect at least five eligible tickets:

```powershell
python -m src.feedback review 1 --disposition false_positive --corrected-class normal --analyst alice
python -m src.feedback review 1 --disposition false_positive --corrected-class normal --analyst bob
python -m src.retrain --registry models/registry --train data/KDDTrain+.txt --test data/KDDTest+.txt
python -m src.model_registry list
```

Do not impersonate another reviewer to satisfy the rule. Two genuinely independent
analysts should review the evidence; the software cannot enforce that independence yet.
