# Candidate-First Retraining

Implementation record for the next governance pass after merged `8c7b459`,
9 October 2026. The owner confirmed that the preceding PR's corrected CI passed
before merging; local refs show `main` and `origin/main` at that merge. This pass
does not independently certify the preceding CI or publish a new branch.

## Tasks and Decisions

### Minimum Cohort and Weight Bounds

`src/retrain.py` now requires five eligible latest false-positive corrections by
default, before loading datasets or fitting models. Withdrawn/investigation and
confirmed-attack reviews remain ineligible. `src/feedback.py` exposes the latest
review ID in each training example without changing the SQLite schema or deleting
earlier reviews. The UI disables retraining below the minimum and shows the count.

Five is an explicit prototype policy, not a statistically justified sample size,
distinct-telemetry check or multi-analyst agreement threshold. Library callers can
explicitly configure a positive minimum; the console/candidate CLI use five.
The existing default weight is 25.0; library/CLI validation now shares the UI's
60.0 upper bound. Total or per-analyst influence is still not capped (G2/G4/G5).

### Separate Candidate Bundles

`src/model_registry.py` trains under an owned `.stage-<uuid>` directory, writes
the model/report/manifest, then publishes a complete directory and registers it.
UUID-suffixed model versions avoid the previous second-resolution collision.
The report's output path identifies its final published location, not staging.
Failed fitting removes staging. A complete directory left by a failed database
registration is inert: only registered IDs are selectable; retained files aid
diagnosis and require a future retention/cleanup policy.

Each model and report has a SHA-256 digest in the authoritative SQLite candidate
record; `manifest.json` is an export, not the active-pointer authority. Bundle
digests are checked before selection/loading, and candidate IDs cannot be paths.
Reads of an absent registry do not create it. This is a trusted-local registry,
not signed attestation or protection against its filesystem owner rewriting both
files and registry. Never load joblib files from untrusted sources.

### Evaluation Non-Regression Check

RF predictions must not degrade accuracy, fixed-five-class macro F1 or recall for
Normal, DoS, Probe, R2L and U2R. All five classes must have evaluation support;
empty/unknown populations and invalid/nonfinite metrics block promotion. A
numerical epsilon of `1e-12` only accommodates floating-point comparisons.
Strict non-regression can reject useful trade-offs or sampling noise; calibrated
acceptance tolerances and independent samples belong in the E3 follow-up.

Compare against both the freshly fitted original-data RF and the registry-selected
artifact (if one exists), transformed with that artifact's own encoder/scaler.
Published protocol cards remain historical baselines, not these candidate results.
The artifact/reference generation and digest are captured and checked again at
promotion; old candidates require a new run after selection changes.

This gate covers the supplied evaluation file, usually KDDTest+, not a sealed
independent benchmark. Repeated selection can overfit that file; feedback can
overlap it; tiny synthetic tests are functional evidence only. It does not gate
fused/ISO alert quality, calibration, precision, statistical confidence, latency,
SHAP faithfulness, modern-data transfer or all three published protocols. E3
remains open for provenance-controlled independent evaluation and quality CI.

### Explicit Promotion and Rollback

Training does not overwrite `models/soc_model.joblib` or switch the model selected
for inference. Model Operations offers an operator field, unchecked confirmation,
promotion (only passing/current-reference candidates) and rollback controls.
The operator field is free-text attribution, not authentication/authorization.

SQLite `BEGIN IMMEDIATE` serializes selection changes. Expected generation rejects
stale actions; active/previous pointers and the action log commit in one transaction.
Injected audit-write failures roll back the pointer, and concurrent promotions have
one winner. Rollback swaps the previous and active selection, retaining bundles;
successive rollbacks can toggle them. The first promotion preserves an existing
legacy artifact as a separate snapshot, or records baseline as the prior selection.
Changing the old legacy file later does not change that rollback snapshot.

The console defaults to Baseline on a new session. Retrained mode uses the selected
immutable-by-convention bundle path, so cache identity changes on promotion without
clearing unrelated runtimes during candidate training. A corrupt active bundle
halts UI loading; the CLI can restore a verified previous selection. A connection
already being scored can finish with its captured old runtime; this is not a global
in-flight request barrier. Streaming/other CLI consumers still choose explicit
`--model` paths and are not automatically reconfigured by a registry promotion.

### Run Provenance

Model metadata/report record train/test SHA-256, exact review IDs and a digest of
the correction snapshot, all `src/*.py` hashes, Git commit/dirty state when this is
an actual repository root, RF/ISO parameters, oversampling flag/seed, threshold,
Python and relevant numerical/serialization package versions. The manifest links
these metrics to serialized artifact/report digests and the selection reference.
Dataset hashes are checked before and after fitting; changed files abort saving.

Exported/copied app sources have null Git metadata instead of misattributing a
parent repository's commit. Source hashes still describe their snapshot. Dataset
checks are not an OS-level immutable snapshot; source hashes are not an archive of
dirty source contents; preserve those contents/dependency inventory for replay.
No MLflow/W&B service, remote registry, shadow deployment or signed provenance is
claimed. Exact dependency closure and broader lineage remain E7/E8 follow-ups.

## Usage and Compatibility

Example commands below are usage guidance, not operational training performed
against the owner's database. Run from the repository with the environment active:

```powershell
python -m src.retrain --registry models/registry --train data/KDDTrain+.txt --test data/KDDTest+.txt
python -m src.model_registry --registry models/registry list
python -m src.model_registry --registry models/registry promote <candidate-id> --expected-generation <generation> --operator <name>
python -m src.model_registry --registry models/registry rollback --expected-generation <generation> --operator <name>
```

The old retraining CLI's `--output` and `--report` flags are intentionally replaced
by `--registry`: callers cannot accidentally point the default CLI at the active
artifact. Each report is in its candidate directory; standard output includes the
candidate decision and report. `--legacy-model` identifies an existing trusted
artifact for comparison/first rollback snapshot. The console defaults to
`models/registry`; `SOC_MODEL_REGISTRY` overrides its root for isolated deployment.
The old `state/retrain_report.json` remains untouched but is no longer auto-displayed
as a current candidate. Legacy model support does not retroactively certify that report.

`retrain_from_feedback(output_model=...)` remains a low-level library fitter for
compatibility and isolated training tests, not the governed selection API. It can
replace an explicitly supplied path; never point it at an active/registered bundle.
The governed entry points are `ModelRegistry.create_candidate`, the retraining CLI
and the console. This is an application boundary, not filesystem write protection.

## Verification and Executed Commands

- Full unit/AppTest: **278 passed** locally, **82.85%** coverage, and in clean export
  `3d0b4a5`, **82.57%** coverage. Both include 2,542 production statements with the
  unchanged 70% floor, rather than the preceding 2,229-statement runtime footprint.
  52 cases added; app/registry 96%, retraining 98% local / 95% clean, model-store
  100% statement coverage. 924 dependency warnings remain.
- Initial focused training/registry/feedback/app run: 111 passed. Expanded
  registry/app check: 89 passed. Focused actual desktop/mobile candidate browser
  flows: two passed. Full clean-export Chromium: **23 passed in 430.66 seconds**,
  zero failures/errors/skips. All 20 console reports contain no page errors or
  blocked external HTTP. Candidate lifecycle/geometry screenshots were inspected
  at 1440/1024/390/320 px; no clipped/overlapping controls or document overflow.
  Suite includes 16 prior console workflows, four new actual candidate workflows
  and three readiness-only checks. All owned test server sessions finished.
- Black checks all 44 Python files; flake8, pip check and whitespace checks pass.
- Initial full unit run caught a CLI fixture using the local default legacy path;
  it was isolated before rerunning the entire suite. Initial candidate browser
  cases timed out clicking Streamlit's hidden checkbox input; native keyboard
  Space/visible-label interaction and checked-state assertions fixed the tests.
  No forced clicks, sleep/retry workaround or weakened assertion was introduced.
- Local Windows Python 3.13.14 is outside the supported 3.10-3.12 CI matrix.
  This new branch still needs its own remote CI before merging. Owner confirmation
  of the preceding merged PR's CI is not clearance for this branch.

Production datasets/reviews/models were not retrained or overwritten: fitted models,
candidates and review writes use temporary synthetic workspaces. No Figma nodes,
dependency pins or CI gate policies were changed. Evolution PDF preserves its
dated 155-test checkpoint; the current status record and this guide supersede its
earlier "promotion absent" implementation snapshot.

The following commands were executed from the repository using PowerShell:

```powershell
git status --short --branch
git log -8 --oneline --decorate
git switch -c codex/candidate-governance
& '.\.venv\Scripts\python.exe' 'C:\Users\ojasp\.agents\skills\developing-with-streamlit\scripts\discover.py' --project-dir 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant'
& '.\.venv\Scripts\python.exe' -m black streamlit_app.py src tests browser_tests scripts
& '.\.venv\Scripts\python.exe' -m pytest tests/test_model_registry.py tests/test_retrain.py tests/test_feedback.py tests/test_streamlit_app.py --cov --cov-report=term-missing --cov-report=json:outputs/candidate-focused-coverage.json
& '.\.venv\Scripts\python.exe' -m pytest tests/test_model_registry.py tests/test_streamlit_app.py
& '.\.venv\Scripts\python.exe' -m pytest --cov --cov-report=term-missing --cov-report=json:outputs/candidate-unit-final-coverage.json --cov-fail-under=70
& '.\.venv\Scripts\python.exe' -m pytest browser_tests/test_candidates.py --browser chromium -k '1440 or 320' --tracing retain-on-failure --screenshot only-on-failure --output outputs/candidate-browser-interactions --junitxml outputs/candidate-browser-interactions/junit.xml
& '.\.venv\Scripts\python.exe' -m black --check streamlit_app.py src tests browser_tests scripts
& '.\.venv\Scripts\python.exe' -m flake8 streamlit_app.py src tests browser_tests scripts
& '.\.venv\Scripts\python.exe' -m pip check
git diff --check
git add -- src/feedback.py src/retrain.py src/model_registry.py streamlit_app.py tests/test_retrain.py tests/test_model_registry.py tests/test_streamlit_app.py browser_tests/conftest.py browser_tests/support.py browser_tests/test_console.py browser_tests/test_candidates.py pyproject.toml
git diff --cached --check
git commit -m "feat(retrain): stage governed candidates with promotion and rollback"
git archive --format=zip --output=outputs/candidate-governance-clean.zip HEAD
if (Test-Path -LiteralPath 'outputs\candidate-governance-clean') { throw 'Clean-export destination already exists.' }; Expand-Archive -LiteralPath 'outputs\candidate-governance-clean.zip' -DestinationPath 'outputs\candidate-governance-clean'
```

Clean-export commands used working directory `outputs/candidate-governance-clean`
(selected by the command tool, without changing the owner's checkout):

```powershell
& 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\.venv\Scripts\python.exe' -m pytest --cov --cov-report=term-missing --cov-report=json:'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\outputs\candidate-clean-unit-coverage.json' --cov-fail-under=70
& 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\.venv\Scripts\python.exe' -m pytest browser_tests --browser chromium --tracing retain-on-failure --screenshot only-on-failure --output 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\outputs\candidate-clean-browser' --junitxml 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\outputs\candidate-clean-browser\junit.xml'
```

Manual source/tracker edits used `apply_patch`; parent trackers are outside Git.
Tests launch owned hidden/isolated servers and stop them on teardown; no user server
is stopped. Browser screenshots/geometry, JUnit, fixture hashes and server diagnostics
are under ignored `outputs/`; no fixture metrics replace published dataset scores.

An intentional preview remains running at `http://localhost:8501/`, launcher PID
12072 / Python child 20216. It uses the actual repository in default Baseline mode;
no training/review action was performed there. Health endpoint returned HTTP 200
with `ok`. The launch checked occupied ports and used a hidden background window:

```powershell
$port = 8501; $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue); while ($listeners.LocalPort -contains $port) { $port++ }; $process = Start-Process -FilePath (Resolve-Path '.\.venv\Scripts\python.exe').Path -ArgumentList '-m','streamlit','run','streamlit_app.py','--server.port',"$port",'--server.address','127.0.0.1','--server.headless','true','--browser.gatherUsageStats','false' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -RedirectStandardOutput 'outputs\candidate-preview.stdout.log' -RedirectStandardError 'outputs\candidate-preview.stderr.log' -PassThru; [pscustomobject]@{LauncherPID=$process.Id; URL="http://127.0.0.1:$port"} | ConvertTo-Json
Invoke-WebRequest -Uri 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing | Select-Object StatusCode, Content
Get-CimInstance Win32_Process -Filter 'ParentProcessId=12072' | Select-Object ProcessId, ParentProcessId, CommandLine | ConvertTo-Json
git add -- README.md docs/candidate_governance.md docs/browser_testing.md docs/project_status.md
git commit -m "docs: record candidate governance verification and remaining quality gaps"
```
