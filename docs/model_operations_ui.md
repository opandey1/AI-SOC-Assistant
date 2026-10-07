# Model Operations: Figma implementation

Design reference: [refined Model Operations screen](https://www.figma.com/design/qh0Rkefos51ldMTGsL6FN0?node-id=12-4).

## Implemented in the running app

- `streamlit_app.py`: native, bordered feedback/evaluation panels with the
  design's 520:850 desktop proportions. The retrain button stays inside its panel.
- `src/ui.py`: 26 px page margins, 18 px panel spacing, 140 px minimum protocol
  cards, 14 px protocol headings, 13 px dataset/metric context, and 7 px tracks.
  Longer content grows naturally instead of being clipped.
- The evaluation cards retain the published baseline order: UNSW-NB15 transfer,
  KDDTest+ cross-distribution, then stratified NSL-KDD hold-out. Their bars scale
  with the card width, using the actual reported accuracy ratios.
- The reviewed-cohort table displays up to six latest false-positive reviews,
  including ticket/event identifiers and distinct predicted/corrected classes.
  Every database-derived value is HTML-escaped.
- The feedback-weight slider defaults to the existing backend weight of 25.0,
  offers 1.0-60.0 in 0.5 steps, and passes its value to `retrain_from_feedback`.
  Adjusting it alone does not train a model. Both controls are disabled without
  eligible feedback.
- Model metadata tiles show the selected model, eligible feedback count,
  artifact availability/path, and the current artifact writer schema version.
- Inter and JetBrains Mono are bundled with their licenses and served locally.
  `Dockerfile` includes these assets. No remote font service is required.
- At viewport widths of 1100 px or less, panels stack and lose the desktop
  minimum height; protocol headings/metrics wrap on narrow displays. The Model
  workspace's shared header also wraps below 480 px to keep its model identifier
  inside the page. Long metric values can wrap rather than overflow their tiles.

## Deliberate differences and limits

The working sidebar and native workspace navigation remain. Figma's shared
top bar does not yet represent the app's session controls, so this is not a
pixel-identical replacement of the entire application shell. Native Streamlit
slider/button rendering is retained for keyboard and widget behaviour.

The baseline is trained from NSL-KDD, not loaded from the retrained artifact;
its tile says so. The format tile describes the writer schema, not validation of
an existing artifact's contents. The existing latest retraining report stays
below the panels.

No new candidate validation or promotion gate was added. Retraining still
overwrites the single artifact, and the warning remains when Retrained is
selected. The published protocol cards are not live candidate evaluations.

## Verification commands

Run from the repository in PowerShell with the existing virtual environment:

```powershell
& '.\.venv\Scripts\python.exe' -m black --check streamlit_app.py src tests scripts
& '.\.venv\Scripts\python.exe' -m flake8 streamlit_app.py src tests scripts
& '.\.venv\Scripts\python.exe' -m pytest tests\test_ui.py tests\test_streamlit_app.py
& '.\.venv\Scripts\python.exe' -m pytest --cov --cov-report=term-missing --cov-fail-under=70
git diff --check
& '.\.venv\Scripts\python.exe' -m streamlit run streamlit_app.py
```

The targeted suite covers safe table rendering, disabled empty-state controls,
selected-weight propagation through a mocked retraining call, and the existing
overwrite warning. Those interaction tests do not write to the local model or
review database.

Browser QA uses headless Edge through Playwright at 1440, 1024, 390, and 320 px, with
screenshots stored locally under ignored `outputs/visual-qa/`. Checks cover loaded
fonts, panel stacking, unclipped protocol text, proportional tracks, and absence
of Streamlit exceptions. Visual checks do not click the real retrain button.

Verified on 2026-10-07 with the local Python 3.13.14 environment: 155 tests
passed, 73.63% total coverage, and clean Black/flake8/diff checks. The dependency
deprecation warnings remain. A Docker build was not run because Docker is not
available on this machine; the asset copy was checked in the Dockerfile only.
