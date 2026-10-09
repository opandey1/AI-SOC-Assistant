# Portable Browser Regression Tests

Verified **9 October 2026**, implemented for **PENDING_ITEMS E6** after merged
Triage baseline `98889ef`. Implementation commit: `aa3f636`
(`test(browser): add portable isolated console checks and CI`).
The browser suite tests the real Streamlit app without requiring downloaded
datasets, a running user server, cloud credentials or a saved model artifact.

## Tasks and Design

| Task | Files / implementation | Why |
|---|---|---|
| Isolated application startup | `browser_tests/support.py` copies the unchanged app, Python modules, config and fonts into a temporary workspace. It generates 80 synthetic training rows across five classes and a DoS/DoS/NORMAL three-row test file. | The original app's defaults stay intact. Local datasets, analyst state, artifacts and secrets are never copied or used. |
| Safe server lifecycle | Every case starts its own loopback server on an available port with a unique static ownership marker. Startup requires that marker, not just a generic health response. Teardown terminates only the process tree/session created by that fixture. | Port collisions cannot silently attach the suite to a user's app; failures clean up the owned server. Windows launchers remain hidden. |
| Real browser workflows | `browser_tests/test_console.py`: four workflows at 1440, 1024, 390 and 320 px, using official Playwright fixtures and native interactions. Actual preprocessing, RF/ISO inference, SHAP, template rendering and SQLite operations execute. | No scorer/store mocks or fake reviews in the browser UI. Temporary placeholder data makes functional checks repeatable without claiming model quality. |
| Diagnostics and evidence | `browser_tests/conftest.py` captures page errors and rejects external browser HTTP requests. Each case records screenshots, geometry, source/dataset hashes and server logs; Playwright retains failure traces/screenshots. | Failures have inspectable artifacts. Only synthetic inputs/reviewer names reach these files, not operational incident data. |
| Harness safety tests | `tests/test_browser_harness.py` verifies source identity, copy allowlist, fixture schema/cohort, environment filtering and fail-closed ownership/startup. | Safety properties are checked by the ordinary unit suite without requiring Playwright or an installed browser. |
| Blocking browser CI | `.github/workflows/ci.yml` adds a separate Python 3.12/Chromium job, installs browser system dependencies and uploads evidence even on failure. Direct test dependencies are pinned in `requirements-browser.txt`. | Browser failures are blocking; the existing Python 3.10-3.12 unit/coverage matrix and container build remain separate. Optional tools do not enter the application image. |

## Coverage of the Browser Suite

**16 parameterized cases**: four workflows at four widths, each with a fresh app,
database and browser context.

1. **Triage / JSON:** initial icon, dataset clear with no ticket, malformed/non-object/
   missing-field JSON, finite-value validation, normal and DoS JSON, RF-labelled SHAP,
   real Markdown download and submitted-JSON retention across modes.
2. **Replay:** three-event feed, exactly two alert tickets and a last NORMAL result,
   retained table across modes, then an end-of-file run processing one of five requested
   events without adding a NORMAL ticket.
3. **Review / cohort:** empty queue, two real synthetic alert tickets, explicit
   disposition, blank-analyst rejection, keyboard correction selection, false-positive
   save, cleared selection, another ticket's empty draft, confirmed-attack save without
   a correction, filtered-empty state and an eligible Model Operations cohort.
4. **Model Operations:** disabled retrain without feedback, three published protocol
   cards, proportional accuracy tracks, loaded local fonts and responsive geometry.

The suite checks document/main overflow, header containment, score/evidence/pill/card
fit and an expanded desktop sidebar. It never clicks **Retrain Random Forest**.
Database assertions use read-only connections; reviews are created by explicit UI
actions only. The browser tests are outside `testpaths = ["tests"]`, so the ordinary
unit command does not silently collect or skip them. Coverage excludes both test
directories, not additional production modules.

## Reproduce

From a checkout with the normal `requirements.txt` installed, use the project's
supported Python range, **3.10-3.12**:

```powershell
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python -m pytest browser_tests --browser chromium --tracing retain-on-failure --screenshot only-on-failure --output outputs/browser-qa --junitxml outputs/browser-qa/junit.xml
```

On Linux, install the browser's OS dependencies too, as CI does:

```bash
python -m playwright install --with-deps chromium
```

The suite starts/stops its own temporary servers. Do **not** start the normal app
or point this harness at an operational database. Tests always use the URL of their
owned temporary server, not a user-supplied endpoint. Existing user servers need
not be stopped.

Run unit tests and code checks separately:

```powershell
python -m pytest --cov --cov-report=term-missing --cov-fail-under=70
python -m black --check streamlit_app.py src tests browser_tests scripts
python -m flake8 streamlit_app.py src tests browser_tests scripts
python -m pip check
```

Outputs are ignored under `outputs/browser-qa/`: per-case screenshots, geometry,
fixture hashes, browser diagnostics and logs; JUnit results; and Playwright failure
artifacts. The plugin replaces its output directory on each run, so use a different
`--output` path to preserve an earlier run. CI uploads the directory as the
`browser-regression` artifact with seven-day retention. Inspect a failure trace with:

```powershell
python -m playwright show-trace outputs/browser-qa/<failed-case>/trace.zip
```

## Verification Record

- Original workspace: **198 unit/AppTest tests passed, 77.88% coverage**.
- Clean Git-exported `aa3f636`, without ignored datasets/models/state:
  **198 unit/AppTest tests passed, 77.70% coverage**, enforcing the same 70% floor.
  Absent local state changes a few covered branches; the production denominator
  remains **2,229 statements**. Browser subprocess execution is not added to this
  unit coverage percentage.
- That clean export also passed **all 16 Chromium browser cases** in 136.37 seconds.
  All 16 per-case reports contain no JavaScript page errors or external browser
  HTTP requests. Actual SQLite assertions check ticket counts/dispositions and
  ensure cleared connections do not create tickets. Sample narrow screenshots
  for evidence, replay, review controls and protocol cards were visually inspected.
- Black: 39 files clean; flake8 and `pip check` passed. CI YAML was parsed and
  its blocking test/always-upload configuration checked locally, not executed
  through GitHub Actions. Unit runs retain 924 dependency warnings.
- Local interpreter: Python **3.13.14**, pytest **8.3.4**; Playwright **1.63.0** /
  pytest-playwright **0.10.0**, downloaded Chromium build **1243**. This local
  interpreter is outside supported CI 3.10-3.12; it does not certify that version.

Main commands executed from the original repository:

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-browser.txt
& '.\.venv\Scripts\python.exe' -m playwright install chromium
& '.\.venv\Scripts\python.exe' -m pytest tests/test_browser_harness.py
& '.\.venv\Scripts\python.exe' -m pytest --cov --cov-report=term-missing --cov-report=json:outputs/browser-unit-coverage.json --cov-fail-under=70
& '.\.venv\Scripts\python.exe' -m black --check streamlit_app.py src tests browser_tests scripts
& '.\.venv\Scripts\python.exe' -m flake8 streamlit_app.py src tests browser_tests scripts
& '.\.venv\Scripts\python.exe' -m pip check
git diff --check
git add -- .github/workflows/ci.yml pyproject.toml requirements-browser.txt browser_tests tests/test_browser_harness.py
git commit -m "test(browser): add portable isolated console checks and CI"
git archive --format=zip --output=outputs/browser-clean-aa3f636.zip aa3f636
Expand-Archive -LiteralPath outputs/browser-clean-aa3f636.zip -DestinationPath outputs/browser-clean-aa3f636
```

Then, with the tool's working directory set to `outputs/browser-clean-aa3f636`,
the following invocations ran. Absolute interpreter/output paths are recorded
because this exported checkout has no virtual environment:

```powershell
& 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\.venv\Scripts\python.exe' -m pytest browser_tests --browser chromium --tracing retain-on-failure --screenshot only-on-failure --output 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\outputs\browser-qa-clean' --junitxml 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\outputs\browser-qa-clean\junit.xml'
& 'C:\Users\ojasp\Desktop\project- AI SOC Assistant\AI-SOC-Assistant\.venv\Scripts\python.exe' -m pytest --cov --cov-report=term-missing --cov-report=json:../browser-clean-unit-coverage.json --cov-fail-under=70
```

Focused first runs found test-side selector differences (native combobox values,
icon/caption accessibility names, lowercase cohort text) and a transient toast
wait. They were corrected without changing production controls or forcing clicks;
the final run above exercises durable state instead. Code/docs were edited with
`apply_patch`; Black handled formatting. Artifacts from the clean run remain in
ignored `outputs/browser-qa-clean/`.

See [project_status.md](project_status.md) and the append-only workspace `HANDOFF.md`.
At this implementation checkpoint, the commits were unpushed and no passing remote
job was claimed. Subsequent verification of merged `82c652a` confirmed the Chromium
job and artifact through the public GitHub API; see [the dated retraining record](retraining_testing.md).
New changes still need their own remote run after publication.

## Limits

- The synthetic rows deliberately separate classes. Their predictions are a
  **functional fixture, not a detection benchmark**, a real retraining regression
  gate (E3), or evidence of generalisation to modern traffic.
- This does not cover Kafka/Redpanda, real feedback retraining, model promotion,
  cloud/Ollama providers, every failure/device state or Docker runtime (E5).
- Browser HTTP requests are restricted to the owned loopback origin. The server
  runs template mode with threat-intelligence disabled and provider keys removed;
  this is not an OS-level network sandbox or a complete environment-secret scrub.
- Screenshots are diagnostic artifacts, **not pixel-diff baselines**. Geometry and
  workflow assertions block regressions; exact cross-OS rendering and accessibility
  certification are not claimed. The canvas-grid row click is tied to pinned
  Streamlit 1.60's row pitch; update it deliberately when upgrading Streamlit.
- Native forms still do not autosave unsubmitted drafts. No production behaviour,
  Figma nodes or published evaluation figures changed. Dependency advisories remain
  untriaged; this pass does not claim remediation or a fresh vulnerability audit.
- The first merged Chromium CI run on Linux was subsequently verified successful;
  that result does not certify later unpushed changes.

Reference guidance: [official Playwright pytest fixtures and artifact options](https://playwright.dev/python/docs/test-runners)
and [official CI/browser-dependency setup](https://playwright.dev/python/docs/ci).
