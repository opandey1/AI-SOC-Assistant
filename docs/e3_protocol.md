# E3 Candidate Acceptance Protocol (Draft 8)

**Status:** Draft for review. Not implemented. The current zero-tolerance gate (rule v1)
stays in force until the activation conditions in §9 are met and recorded.
**Decision record:** [ADR-001](e3_acceptance_rule.md). Drafts 1-7 are superseded; they
are in Git history and `HANDOFF.md`.

## 1. Owner Decisions (27 November 2026)

1. Option C: a five-seed paired average plus checks on the deployed model.
2. Deployed model: seed 42, checked against the fresh seed-42 baseline and the live
   model, with single-retrain allowances.
3. Allowances are the measured seed variation capped by fixed maximum losses:
   min(measured, cap) (§4).
4. U2R: the deployed model must still catch every U2R row that the fresh baseline and
   the live model catch, compared row by row. "Catch" means the Random Forest predicts
   the U2R family. The five-seed average is not used for U2R.
5. Before switching the rule on: checker fixtures (§8.1), an in-distribution pilot on
   the hold-out (§8.2) and a confirmatory run on data not used so far (§8.3), which
   depends on the H1 dataset-mapping work.
6. Pilot thresholds (§8.2):
   - The average check must reject every candidate whose ten-seed average loss
     exceeds a cap.
   - At most a quarter of acceptable candidates may fail to pass.
   - The minimum qualifying counts stay, and errors or inconclusive runs never count
     as rejections.
   - A miss is a pilot failure to investigate, not proof of a checker defect.
   - A pilot pass does not replace confirmation on new data.

## 2. Definitions

- **Datasets:**
  - **T** is the rows models are fitted on before feedback rows are added: all of
    KDDTrain+ in production and in the confirmatory run, D in the pilot.
  - **E** is the rows a gate decision is scored on: KDDTest+ in production and in the
    confirmatory run, the pilot rows of H in the pilot. E keeps one fixed row order,
    and every E row must carry one of the five family labels (§5, check 1).
  - **N** is the confirmatory oracle dataset (§8.3). It is never used for gate
    decisions.
- **Canonical JSON**, used for every identity and record: UTF-8, keys sorted, separators
  `(",", ":")`, Python's shortest round-trip form for floats, no NaN or infinity. A
  record's own identity field is left out of the payload that produces it.
- **Identities:**
  - A row's input hash is the SHA-256 of the canonical JSON array of its 41 input
    values, as loaded by `src/ingest.py` and in dataset column order. Its row hash
    covers the same array followed by its family label.
  - A dataset's identity (`train_id` for T, `eval_id` for E, `n_id` for N) is the
    SHA-256 of the canonical JSON array of its row hashes, in order.
  - Rows have "equal inputs" when their input hashes are equal.
- **Metrics:** `accuracy`, `macro_f1` over the fixed five families, and `recall_normal`,
  `recall_dos`, `recall_probe` and `recall_r2l`. They are computed as in
  `classification_metrics` (`src/retrain.py`), with the labels fixed to the five families
  and `zero_division=0`. U2R is handled only by check 6 in §5. Values are proportions in
  [0, 1]; percentage points appear only in displays.
- **Change:** Δ = candidate metric − reference metric.
- **Limit test:** a change passes allowance A ≥ 0 when Δ ≥ −A − 1e-12. Rule v1 uses the
  same tolerance.
- **References:**
  - B(s) is the baseline Random Forest fitted on T with seed s.
  - L is the live model: the registry-selected artifact or, at registry generation 0,
    the legacy artifact. It is scored on E with its own saved preprocessing.
- **Outcomes:** `pass`, `reject`, `inconclusive` or `error`. Only `pass` permits
  promotion.

## 3. Fixed Recipe and Identities

- **Preprocessing:** `preprocess_dataset(use_smote=True, random_state=42)` (random
  oversampling), with the encoder and scaler fitted on T only.
- **Random Forest:** 200 trees, max depth 25, minimum leaf 2, `max_features="sqrt"`,
  bootstrap on, Gini criterion, `class_weight="balanced"`.
- **Feedback:** cohorts and weights follow the current feedback policy (`weight_plan`),
  unchanged.
- **Seeds:**
  - decision S = {42, 2001, 2002, 2003, 2004};
  - deployment seed 42;
  - calibration C = 3001-3010;
  - oracle O = 4001-4010.

  No seed is added, dropped or replaced after any result is seen.
- **Recipe identity (`recipe_id`):** the SHA-256 of the canonical JSON of:
  - these settings;
  - the SHA-256 of `src/ingest.py`, `src/preprocess.py`, `src/train.py`,
    `src/retrain.py` and `src/feedback_policy.py`;
  - the versions of Python, NumPy, pandas, scikit-learn and imbalanced-learn.
- **Checker identity (`checker_id`):** the SHA-256 of the module that implements §5.
- **Protocol identity (`protocol_id`):** the SHA-256 of the canonical JSON of the
  SHA-256 of:
  - this document as approved;
  - the stage configuration file: the §8.2 seeds, cohorts, labelling rules, criteria
    and coverage, the §9.1 record schemas and the hold-out manifest's SHA-256;
  - the modules that run the stages and assign oracle labels.

  It is fixed when this protocol is approved. After approval, this document is not
  edited until activation; status updates go to ADR-001 and the trackers.
- **Fixture identity (`fixtures_id`):** the SHA-256 of the canonical JSON of the §8.1
  fixture cases (inputs, expected outcomes and reason codes) and the registry-test IDs.

## 4. Allowances and Caps

For each metric m on a given (T, E), SD_m is the sample standard deviation of m over the
ten calibration baselines B(c), c ∈ C, scored on E. It is computed as
`numpy.std(values, ddof=1)` on the ten float64 values in seed order, so a replay
(§9.2) reproduces it exactly.

| Allowance | Formula |
|---|---|
| Average | A_avg(m) = min(2·SD_m/√5, cap_m) |
| Deployed | A_dep(m) = min(2·SD_m, cap_m) |

- **Caps (owner policy):** accuracy 0.010; macro F1 0.020; normal, DoS and probe recall
  0.020; R2L recall 0.030.
- **Zero spread:** if SD_m is 0, the allowance is 0, so any loss in that metric
  rejects. The calibration record lists such metrics. There is no floor.
- **Minimum support:** normal, DoS, probe and R2L each need at least 30 rows in E. At
  low support a single row can exceed a cap (with 30 R2L rows one row is 3.3 points),
  so a single lost row rejects.
- **Calibration record** (`schema` `"e3-calibration/1"`):
  - fields: `train_id`, `eval_id`, `recipe_id`, `seeds`, `sd`, `caps`,
    `allowance_average`, `allowance_deployed`, `zero_spread` (sorted metric keys),
    `created_at` (UTC, ISO 8601) and `calibration_id`;
  - `calibration_id` is the SHA-256 of the record's canonical JSON without
    `calibration_id`. It identifies the record; it does not make the limits valid.
  - **Valid** means all of these hold:
    - `calibration_id` recomputes;
    - `seeds` is exactly C;
    - `sd`, `caps`, `allowance_average` and `allowance_deployed` each have exactly
      the six metric keys;
    - every SD is finite and at least 0;
    - every cap equals the policy value above;
    - every allowance equals its formula, recomputed from that SD and cap;
    - `zero_spread` lists exactly the metrics whose SD is 0.
  - **Current** means its `train_id`, `eval_id` and `recipe_id` equal the current ones.
    A record that is not current is stale.

## 5. Decision Checks (Rule v2)

**Inputs:** E's rows (41 inputs and family label, in E's order), the calibration record,
the per-row predictions on E of B(s) and candidate(s) for every s in S, the per-row
predictions of L when it exists, and the SHA-256 of every prediction set and reference
artifact.

1. **Preconditions.** These steps run in order. The first step that fails gives
   `error` with its reason codes; no later step runs, and neither do checks 2-6.
   - **Presence:** E's rows (`missing_evaluation_rows`), the calibration record
     (`calibration_missing`) and every prediction set, for each seed's fits and for L
     when it exists (`missing_predictions`), are present. Every missing input is
     reported.
   - **Evaluation population:**
     - E has at least one row.
     - Every label is one of the five families. A row labelled `unknown`, or anything
       else, fails (`invalid_evaluation_label`).
     - The per-family counts add up to E's row count
       (`invalid_evaluation_population`).
   - **Calibration validity:** the record is valid under §4 (`calibration_invalid`).
   - **Currency:** the record is current, with `eval_id` recomputed from E's rows
     (`calibration_stale`).
   - **Bindings:** every prediction set has E's length, matches its stored SHA-256
     and is bound to the recomputed `eval_id` (`evidence_mismatch`).
   - **Values:** every prediction is one of the five families (`invalid_prediction`),
     and every metric is finite and in [0, 1] (`invalid_metric`).

   This keeps rule v1's known-family population guard. A family with no rows still
   blocks promotion, through check 2 or, for U2R, check 6.
2. **Support.** A class below the minimum has its recall checks in 3-5 skipped and
   records `insufficient_support_<class>`. The other checks still run.
3. **Average:** for each metric that is not skipped, the mean over s ∈ S of
   candidate(s) − B(s) passes A_avg(m) (`average_regressed_<metric>`).
4. **Deployed versus fresh baseline:** candidate(42) − B(42) passes A_dep(m) for each
   metric that is not skipped (`deployed_regressed_<metric>`).
5. **Deployed versus live:** if L exists, candidate(42) − L passes A_dep(m) for each
   metric that is not skipped (`live_regressed_<metric>`). Without L the check is
   skipped and `no_live_reference` is recorded; that alone is not inconclusive.
6. **U2R rows:** for each reference R in {B(42), L if it exists}, the caught set is the
   E rows labelled U2R that R predicts as U2R.
   - Every row in a non-empty caught set must also be predicted U2R by candidate(42)
     (`u2r_detection_lost_baseline`, `u2r_detection_lost_live`, with row counts).
   - A reference with an empty caught set contributes nothing.
   - If every available reference has an empty caught set, record
     `u2r_no_reference_detections`. If only L catches U2R rows, L's set is enforced
     and the outcome is not inconclusive on U2R grounds.

**Outcome:**

- `error` if check 1 fails;
- otherwise `reject` if any check in 3-6 fails;
- otherwise `inconclusive` if any support check was skipped or
  `u2r_no_reference_detections` was recorded;
- otherwise `pass`.

All reason codes are reported, in check order. Existing guards stay as they are:
bundle integrity, policy version, review freshness, cohort minimum, generation and
reference digests.

## 6. Versioning, Promotion, Activation and Revert

- **Rule versions:** v1 is today's zero-tolerance `acceptance_reasons`; v2 is this
  protocol. Under v2 the v1 metric checks are replaced; every other guard remains.
- **Rule state:** the registry stores `rule_version` (default v1), `rule_generation`
  (starting at 0) and, under v2, the activation record (§9).
  - Only `set-rule` changes rule state.
  - Each run either completes in one SQLite transaction or changes nothing.
  - A completed run increments `rule_generation` and writes a history entry with the
    operator, the old and new versions and, for v2, the `activation_id`.
- **`set-rule v2`** takes the §9.2 inputs and refuses, changing nothing, unless every
  item of the §9.2 replay holds.
- **`set-rule v1`** is always allowed. The activation record stays in the history.
- **Activation binding:** under v2, creation and promotion stop with
  `activation_mismatch`, naming the fields, unless:
  - the calibration used is exactly the activated one (same `calibration_id`);
  - the current `train_id`, `eval_id`, `recipe_id` and `checker_id` equal the
    activation record's.

  A current calibration that is not the activated one is not enough. After any of these
  identities changes, v2 decisions stay blocked until `set-rule v1` or a new
  activation.
- **Legacy candidates:** a candidate without rule fields counts as v1, rule generation 0.
- **Candidate evidence:** each candidate bundle stores:
  - the per-row predictions on E of its five fits;
  - E's `eval_id`;
  - the calibration record;
  - the checker output.

  A shared baseline cache stores the per-row predictions of B(s), keyed by `train_id`,
  `eval_id`, `recipe_id` and seed. The bundle manifest binds every file it uses by
  SHA-256.
- **At creation:** the candidate is decided under the active rule. Its report
  (`schema` `"e3-decision/1"` under v2) records:
  - the rule version and generation;
  - `calibration_id`, `recipe_id`, `checker_id` and `eval_id`;
  - the reference SHA-256s;
  - per-seed metrics, changes and allowances;
  - the outcome and reason codes.
- **At promotion under v2,** the registry:
  - verifies every stored SHA-256;
  - rescores the current L on E;
  - reruns the §5 checker on the stored and rescored predictions;
  - requires `pass`.

  It also requires the candidate's rule version and generation to equal the current
  ones, together with today's review-snapshot, policy, generation and reference-digest
  checks. Matching versions and calibration only allow this rerun; they never replace
  it.
- **Cross-version:** under either rule, a candidate whose rule version or generation
  differs from the current ones cannot be promoted and must be retrained.
- **Under v1:** the metric checks at promotion are unchanged.
- **Rollback:** unchanged. It restores the previous selection and runs neither rule.

## 7. Data Handling for the Pilot

- T = D (100,778 KDDTrain+ rows). E = the pilot rows of H: 25,058 rows after removing
  the 137 hold-out rows whose 41 inputs exactly equal a D row (4) or a KDDTest+ row
  (134; one row matches both). The excluded indices are listed in
  [`evaluation/e3_holdout/holdout_rows.json`](evaluation/e3_holdout/holdout_rows.json).
- Pilot E class counts: normal 13,444; DoS 9,101; probe 2,304; R2L 199; U2R 10.
- Preprocessing and every fit (baselines, candidates, calibration, oracle and the
  pilot's live reference) use D only.
- The pilot's L is a D-trained seed-42 model with one fixed neutral cohort: five normal
  rows, sample seed 100, weight 25.
- **Cohort rows:**
  - They are taken from D in the order of a seeded permutation
    (`numpy.random.default_rng(seed)`) of the eligible rows.
  - Any row whose inputs equal an E row is skipped.
  - Each row carries two synthetic reviewer labels, so the policy's weight plan
    applies.
- H is used for this pilot once and is then recorded as consumed. It is never used to
  choose, tune or select anything.

## 8. Verification Stages

Each stage is recorded and reviewed before the next one starts.

### 8.1 Checker Fixtures and Registry Tests

Implement §5 as one pure function over E's rows, prediction arrays and a calibration
record, with no model fitting. Test it on pre-scored fixtures:

- unchanged;
- just inside and just outside each allowance;
- zero spread;
- average-only, deployed-only and live-only failures;
- a U2R swap (equal recall, one previously caught row lost) against each reference;
- only L catching U2R rows;
- no reference catching any U2R row;
- no live model;
- each input missing on its own, with no dependent step run;
- mismatched evidence;
- missing or stale calibration;
- calibration records whose `calibration_id` recomputes but whose seeds, metric keys,
  caps, allowances or zero-spread list are wrong;
- invalid predictions or metrics;
- valid prediction arrays with invalid truth: an `unknown` label, a label outside the
  five families, an empty E, family counts that do not add up, and rows whose
  recomputed `eval_id` differs from the record's;
- support below the minimum;
- each combination in the outcome order.

The §8.2 labelling rules are tested the same way, on pre-scored known-loss cases at,
just inside and just outside each cap and half cap, against each reference.

Registry tests cover §6 and §9.2:

- legacy and cross-version candidates are refused;
- `set-rule v2` is refused, with no state change, for each failed §9.2 item. The
  cases include:
  - a confirmatory report that tested a different calibration from the one supplied;
  - reports that disagree on `protocol_id`, `fixture_report_id`, `recipe_id` or
    `checker_id`;
  - a plan or N whose recomputed `plan_id` or `n_id` differs;
  - a missing or misbound prediction file;
  - a prediction file whose artifact SHA-256 is not the fit manifest's for its key, two
    artifacts for one key, a fit whose E and N files name different artifacts, and L
    files naming an artifact other than the one whose training inputs were shown;
  - an N ledger that omits or repeats a source row, or gives a reason that does not
    recompute;
  - calibration SDs that the C-seed predictions do not reproduce;
  - a report showing zero U2R losses where the derived caught sets are empty;
  - any stored value that differs from the derived one;
  - a fixture case whose rerun differs from the manifest;
  - a registry test missing or skipped in the JUnit XML;
  - a missing anchor commit, or an anchor entry naming a different stage,
    `protocol_id` or `fits_id`, for either stage;
  - a fit-manifest entry whose `train_id` or cohort row hashes differ from the frozen
    design;
- `set-rule v1` is always allowed;
- `activation_mismatch` blocks creation and promotion when the calibration is current
  but not the activated one, and when any bound identity changes;
- promotion fails when a stored prediction file is altered or E changes.

Stage-runner tests: the runner refuses to score when the anchor commit is missing, is
not in the repository's history, or has an anchor entry that differs in stage,
`protocol_id` or `fits_id`. It also refuses when its own fit manifest differs from the
frozen design. The fixture manifest names these test modules like the registry
tests.

### 8.2 Pilot on the Hold-out (In-Distribution, Not Confirmatory)

**Candidates (20, fixed before any fit):**

- eight correction cohorts: five correctly labelled normal rows, weight 25, sample
  seeds 0-7;
- twelve label-flip cohorts: DoS rows relabelled as normal, requested weight 60, three
  at each size, each with its own sample seed: 50 rows (seeds 8-10), 250 (11-13),
  1,000 (14-16) and 2,500 (17-19). The weight plan scales the weights of the two
  largest sizes.

The flips exist to give (b) its coverage. They are not evidence about behaviour near
the limits, which the §8.1 known-loss fixtures test. Separate samples from one pool can
still share rows, and every candidate shares E, the baselines and L. The criteria are
counts over this fixed design, not independent trials, and no rate or tolerance is
inferred from them.

**Oracle labels.** The labelling rules are fixed before scoring. They use only the
owner's policy (caps and the U2R row rule), never the calibrated allowances or any gate
output.

- **Target A, the deployed artifact:** compare candidate(42) exactly with B(42) and
  with L on E.
  - `harmful_A` if any metric fails the limit test at its cap against either reference,
    or any U2R row caught by either reference is lost.
  - `acceptable_A` otherwise.
- **Target B, the expected effect:** the mean over seeds O of candidate(o) − B(o) on E.
  - `harmful_B` if any metric's mean fails the limit test at its cap.
  - `acceptable_B` if every metric's mean passes it at half its cap.
  - `ambiguous_B` otherwise.

**Criteria (owner thresholds, §1):**

| Criterion | Requirement | Coverage needed |
|---|---|---|
| (a) Safety | every `harmful_A` candidate ends in `reject` with a reason from check 4, 5 or 6 | at least 1 `harmful_A` |
| (b) Average sensitivity | every `harmful_B` candidate ends in `reject` with a reason from check 3 | at least 4 `harmful_B` |
| (c) Usefulness | at most ⌊n/4⌋ of the n candidates that are both `acceptable_A` and `acceptable_B` fail to pass | n ≥ 4 |
| (d) Validity | every candidate has oracle labels and ends in `pass` or `reject` | all 20 |

- **Denominators** come from the oracle labels alone; a gate outcome never removes a
  candidate from one.
  - An `error` or `inconclusive` candidate counts against (a) and (b) as not rejected,
    and against (c) as not passed.
  - `ambiguous_B` candidates are reported but not counted in (b) or (c).
- **Targets:** disagreement between the two targets is not a gate error. A candidate
  that is `acceptable_B` but `harmful_A` should be rejected by checks 4-6, and it is
  outside (c).
- **What (a) tests:** on these rows, (a) holds by construction when checks 4-6 are built
  as specified, because A_dep ≤ cap and check 6 is the oracle's U2R rule. It tests the
  implementation end to end, not discrimination. (b) and (c) are the empirical tests
  here; in §8.3, (a) is empirical too.
- **A (b) miss** is a pilot `fail`, not proof of a checker defect.
  - The gate averages five seeds (S) and the oracle ten others (O). An allowance below
    the cap guarantees rejection only when the same measured loss is compared with
    both.
  - The report gives both averages for each metric. A miss is investigated as a
    disagreement between them before anyone concludes the checker is wrong.
  - H is not rescored.
- **Stage result:**
  - `fail` if (a), (b) or (c) has its coverage and is false;
  - otherwise `inconclusive` if (d) is false or any criterion lacks coverage;
  - otherwise `pass`.

  A pilot `pass` does not replace the §8.3 confirmation.
- **Order:**
  - The pilot starts only once §9.3's "Before the pilot" condition is met.
  - All fits finish before anything is scored on E. The stage runner then writes the
    stage's fit manifest (§9.1), with one artifact SHA-256 per fit, and checks it
    against the frozen design: every entry's `train_id` and every candidate's cohort
    row hashes.
  - **The anchor:** the owner commits an ADR-001 entry containing one line of the form
    `E3 anchor: stage=<stage> protocol_id=<id> fits_id=<id>`.
    - The runner refuses to score until that commit is in the repository's history
      and its line matches the stage, `protocol_id` and `fits_id` exactly.
    - The commit ID (`anchor_commit`) goes into the stage report.
    - Git dates alone do not prove when anything happened. The anchor records an
      ordering that the runner enforces in a trusted local workflow.
  - Every per-row prediction is kept in the stage's evidence package.
  - A fit failure before the first score on E may be fixed and rerun with the same
    settings, before the fit manifest is written.
  - After the first score the stage is final, and H is consumed.
  - No cohort, seed, labelling rule or criterion changes after any fit or score,
    including after a `fail` or `inconclusive` result.
- **Reported** (as the stage report in a §9.1 evidence package):
  - the size of every oracle group (`harmful_A`, `acceptable_A`, `harmful_B`,
    `acceptable_B`, `ambiguous_B` and the (c) set);
  - for each criterion, the candidates counted and its result;
  - per-metric changes against each reference, for both targets and for the gate;
  - reason-code counts, with errors listed separately;
  - the pass rate of the correction cohorts;
  - the number of candidates that are `harmful_A` only through U2R rows.
- **Cost:** 326 fits: 25 baselines, the pilot's L, and 15 fits for each of the 20
  candidates. At the roughly 30 s per fit measured for ADR-001 on the full training
  file (D is smaller), that is under three hours on two cores.

### 8.3 Confirmatory Run on New Data (Depends on H1)

- **Gate side (KDDTest+):** the production configuration.
  - T is all of KDDTrain+ and E is KDDTest+.
  - Allowances come from a calibration on (KDDTrain+, KDDTest+) with seeds C.
  - L is the live model at the time of the run; its SHA-256 is recorded.
  - Each candidate gets the §5 decision.
- **L's training inputs:** if L exists, the run must show everything it was trained on,
  in one of two ways:
  - **From provenance:** its base training file, identified by the dataset SHA-256s in
    L's provenance, and every feedback row in its lineage, rebuilt from the feedback
    database by the recorded review IDs and checked against the recorded
    `feedback_sha256`.
  - **By lineage recovery,** for an artifact without that provenance, a reviewed
    record needs both of these:
    - **Independent evidence** for each claimed input, recorded no later than L's
      training time and not derived from any refit.
      - For feedback rows: the ticket and review records, whose IDs match L's
        recorded ticket IDs and whose timestamps precede L's `trained_at`.
      - For the base file: evidence that it was the configured training file then,
        such as its SHA-256 in the repository at the commit used.
      - Matching report metrics only support this evidence; they never replace it.
    - **An exact match:** a refit from those inputs, with the named code and package
      versions, equals L on everything that affects inference:
      - the preprocessing parameters (encoder categories, scaler statistics);
      - the feature and class order;
      - the Random Forest's parameters and every tree's structure, split features,
        thresholds and leaf values.

    A matching refit alone does not establish the historical inputs.

  If L exists and neither way works, the stage is `inconclusive`. That covers:
  - no provenance;
  - a missing or changed file;
  - feedback rows that cannot be rebuilt;
  - missing independent evidence;
  - a refit that differs.

  An existing L is never treated as absent, a fresh baseline never stands in for it,
  and no unrelated artifact is loaded in its place. Replacing L with a model that has
  complete provenance is a separate, reviewed change under a valid policy. This
  protocol neither performs it nor bypasses or weakens rule v1 for it.
- **N:** a dataset not used in this policy's selection or fitting, mapped to the 41
  inputs and the five families (H1). Before any model is scored on it, every source row
  is either kept or removed for one of these reasons:
  - the mapping leaves it without one of the five families;
  - it is in the mapping-design partition, the source rows used to design or check
    the mapping;
  - its inputs equal a row of KDDTrain+ (which contains H), KDDTest+, L's base
    training file or L's feedback rows.

  What remains must pass the evaluation-population step of §5, check 1. Its identity is
  `n_id`.
- **N's ledger:** one entry per source row, giving the row ID and either its position
  in N or its removal reason. It must account for the whole source manifest (below),
  every row exactly once, so a curated N cannot pass as the full population. The source
  files stay local and are referenced by SHA-256, not copied.
- **Required evidence:** N must have at least 30 rows each of normal, DoS, probe and
  R2L, and at least one U2R row caught by B(42) or L. Without it the stage cannot pass:
  a `fail` stays `fail`, and any other result becomes `inconclusive`. With all five
  families present, the metrics on N are exactly those in §2, so no family is left out
  of accuracy or macro F1.
- **Oracle side:**
  - Target A compares candidate(42) with B(42) and L on N.
  - Target B averages candidate(o) − B(o) over O on N.
  - Labels follow §8.2.
- **Criteria:** (a)-(d), their coverage and the stage result as in §8.2, except that in
  (a) any rejection counts, since harm on N need not show up in the same check on
  KDDTest+.
- **Order:** as in §8.2, including the runner-checked anchor (`stage=confirmation`).
  All fits finish, and the anchor is verified, before anything is scored on KDDTest+
  or N. A fit's predictions on both
  come from the same artifact.
- **Confirmatory plan:** before any model is scored on N, a plan record fixes:
  - the source manifest: each source file's SHA-256 and the ordered source row IDs
    (file and row number);
  - the approved H1 mapping (its code SHA-256 and parameters) and the mapping-design
    partition (by row ID);
  - the exclusions above, and the resulting `n_id`;
  - the candidate cohorts, drawn from KDDTrain+ and skipping rows whose inputs equal a
    KDDTest+ or N row.

  The plan is reviewed. Its identity (`plan_id`) and its `n_id` go into the
  confirmatory report and the activation record. No confirmation is claimed before
  then.

## 9. Evidence and Activation

### 9.1 Evidence Packages and Records

Every record is canonical JSON with a `schema` and a self-excluding SHA-256 identity.
The record schemas are part of the stage configuration, so `protocol_id` freezes them
together with the bindings below. A stored summary is never evidence by itself: §9.2
derives every result from the rows and predictions in the packages.

- **Fixture manifest** (frozen by `fixtures_id`): every checker fixture case with its
  inputs and its expected outcome and reason codes. It also names each registry-test
  module, by path and SHA-256, and each registry-test ID.
- **Fixture package:**
  - the fixture report (`"e3-fixture-report/1"`): `checker_id`, `fixtures_id`, each
    case's and test's ID and actual result, and `junit_sha256`;
  - the JUnit XML file from running exactly the registry-test modules the manifest
    names, whose SHA-256 is `junit_sha256`.
- **Stage evidence package** (`"e3-evidence/1"`), one for the pilot and one for the
  confirmation: an immutable set of files. Its manifest lists each file's SHA-256, and
  its identity is `package_id`. It holds:
  - the stage report (below);
  - the fit manifest (below);
  - E's labelled rows, as a source file referenced by SHA-256 plus the row selection:
    KDDTrain+ and the hold-out manifest for the pilot, KDDTest+ for the
    confirmation's gate side;
  - for the confirmation:
    - N's mapped, labelled rows;
    - N's ledger (§8.3), with the input hash behind each removal by equality;
    - L's training-input evidence (§8.3);
  - one per-row prediction file for every fit the stage used:
    - on E: B(s) and candidate(s) for s ∈ S, B(c) for c ∈ C, B(o) and candidate(o)
      for o ∈ O, and L;
    - for the confirmation, also on N: candidate(42), B(42), L, and B(o) and
      candidate(o) for o ∈ O.

    Each file states its role, seed, candidate ID, the SHA-256 of the artifact that
    produced it, the dataset identity it was scored on and its row count.
  - the calibration record.
- **Fit manifest** (`"e3-fits/1"`), one per stage, written after every fit and before
  the first score (§8.2):
  - one entry per fit, keyed by role (baseline, candidate or live), seed and candidate
    ID, and no key twice;
  - for each entry, the artifact's SHA-256, its `train_id` and, for a candidate, its
    cohort row hashes;
  - for L, its artifact SHA-256 and a reference to its training-input evidence. The
    pilot's L also lists its neutral cohort's row hashes.

  Its identity is `fits_id`. The expected values come from the frozen design:
  - baselines and candidates have the stage's T as `train_id`;
  - each candidate's cohort rows are those §7's seeded permutation and skip rule give
    for its cohort in the stage configuration (pilot) or the plan (confirmation);
  - the pilot's L has D as `train_id` and the neutral cohort of §7;
  - the confirmation's L has the base file shown in its training-input evidence.
- **Stage report** (`"e3-stage-report/1"`): the stage's summary.
  - `stage`, `protocol_id`, `fixture_report_id`, `fits_id`, `anchor_commit`,
    `recipe_id` and `checker_id`, plus `plan_id` and `n_id` for the confirmation;
  - the gate's `train_id`, `eval_id`, per-family row counts and calibration record;
  - each candidate's cohort (type, size, weight, sample seed and row hashes), gate
    values, §5 outcome and reason codes, oracle values and oracle labels;
  - each criterion's counted candidates, coverage and result, and the stage result.
- **Confirmatory plan:** the source manifest, the approved mapping and its design
  partition, the exclusions, the resulting `n_id` and the cohorts (§8.3). Its identity
  is `plan_id`.
- **Activation record** (`"e3-activation/1"`):
  - `fixture_report_id`, the two stage `package_id`s, and their `fits_id`s and
    `anchor_commit`s;
  - `protocol_id`, `fixtures_id`, `plan_id`, `n_id`, `recipe_id` and `checker_id`;
  - the production `train_id`, `eval_id` and `calibration_id`;
  - the owner-decision fields: `owner`, `decided_on`, and `decision_reference` (the
    ADR-001 entry and its commit). Like every registry action, they are recorded, not
    authenticated;
  - `activation_id`.

### 9.2 Activation Replay

`set-rule v2` takes:

- the activation record;
- the fixture manifest and fixture package;
- the stage configuration;
- both stage evidence packages;
- the confirmatory plan;
- the production calibration record.

It loads no model, fits nothing and makes no new prediction. It replays the recorded
predictions against the recorded rows, so H is not rescored. It refuses, changing
nothing, unless all of these hold:

1. **Records and files:**
   - every record matches its schema, and every self-excluding identity recomputes;
   - every package file matches its manifest's SHA-256;
   - the fixture report's ID and the package IDs equal the activation record's.
2. **Fixtures:**
   - `fixtures_id`, recomputed from the supplied fixture manifest, equals the fixture
     report's and the activation record's.
   - The fixture report's `checker_id` equals the activation record's and the current
     one.
   - Every checker case in the manifest, rerun against the current checker module,
     gives exactly the manifest's expected outcome and reason codes. The manifest is
     the reference for what was expected; the report is not.
   - The JUnit XML matches `junit_sha256`. Every registry test the manifest names
     appears in it as passed, none missing or skipped, and the named modules' SHA-256
     equal the current files'.
3. **Rows:**
   - E's labelled rows are rebuilt from their source files, each SHA-256 verified, and
     `eval_id` recomputes.
   - The pilot's `train_id` and `eval_id` equal those of D and the pilot rows, built
     from KDDTrain+ and the hold-out manifest (§7).
   - For the confirmation, N's source files are read locally and each SHA-256 is
     verified against the plan's source manifest. The approved mapping is reapplied to
     every source row; it is deterministic code, not scoring. Then:
     - the ledger accounts for every source row in the manifest exactly once;
     - every removal reason recomputes (unmapped, in the design partition, or inputs
       equal to a row of KDDTrain+, KDDTest+, L's base file or L's feedback rows);
     - no kept row matches any of those;
     - the kept rows, in order, are N's rows, so `n_id` recomputes;
     - N's rows pass the evaluation-population step of §5, check 1.
4. **Predictions:**
   - Every prediction file the frozen design requires is present. Each is bound to the
     right dataset identity, role, seed and candidate, and has that dataset's row
     count.
   - The stage's fit manifest recomputes to its `fits_id`, which equals the stage
     report's and the activation record's, and has no key twice.
   - Every entry's `train_id`, and every candidate's and the pilot L's cohort row
     hashes, equal the values the frozen design gives (§9.1); they are recomputed, not
     taken from the manifest.
   - Each file's artifact SHA-256 equals the fit manifest's entry for its key. A fit's
     files on E and on N name the same artifact.
   - L's files name the artifact whose training inputs §8.3 showed. That artifact is
     the live model recorded for the run.
   - **Anchors:** for each stage, `anchor_commit` (equal in the stage report and the
     activation record) is in the repository's history. Its ADR-001 holds the line
     `E3 anchor: stage=<stage> protocol_id=<id> fits_id=<id>` with that stage's name,
     `protocol_id` and `fits_id`. A missing or mismatched anchor for either stage
     refuses activation.
5. **Derivation:** from the rows and predictions alone, the replay derives:
   - the calibration SDs from the C-seed baselines, then the allowances. They must
     equal the stage's calibration record and, for the confirmation, the supplied
     production calibration record;
   - every metric and every reference's U2R caught set;
   - each candidate's §5 outcome and reason codes;
   - the oracle labels, the criteria and their coverage, and the §8.3 required
     evidence;
   - the stage result.

   Every derived value must equal the stage report's, and each stage result must be
   `pass`. A value that cannot be derived this way does not count, and a summary-only
   replay never authorizes activation.
6. **Common identities:**
   - both stage reports cite the fixture report's `report_id`;
   - both carry the activation record's `protocol_id`, `recipe_id` and `checker_id`,
     and these equal the current ones;
   - `protocol_id` recomputes from this document, the supplied stage configuration and
     the current runner and labelling modules.
7. **Cohorts:** the pilot's cohorts match the stage configuration, and the
   confirmation's match the plan.
8. **Confirmation join:**
   - `plan_id`, recomputed from the supplied plan, equals the confirmatory report's and
     the activation record's.
   - `n_id`, recomputed from N's rows, equals the plan's, the report's and the
     activation record's.
   - The confirmatory report's `train_id`, `eval_id` and calibration `calibration_id`
     equal the activation record's. They also equal the current KDDTrain+ `train_id`,
     the current KDDTest+ `eval_id` and the supplied production calibration record,
     which is current.
   - L's training-input evidence is complete under §8.3 and names the same artifact
     as the fit manifest's L entry.
9. **Owner decision:** the owner-decision fields are filled in.

The rule change, the stored activation record and the history entry are written in one
SQLite transaction.

### 9.3 Conditions and Freeze

Switching to rule v2 requires:

1. the §8.1 fixtures and registry tests pass;
2. the §8.2 pilot result is `pass`;
3. the §8.3 confirmatory result is `pass`;
4. the owner explicitly decides to activate, and ADR-001 records the decision;
5. the §9.2 replay passes.

Until then rule v1 remains, and promotion on the real data stays effectively blocked,
as measured in ADR-001.

**Freeze.** `protocol_id`, `fixtures_id`, `recipe_id` and `checker_id` must not change
from the pilot until activation. That covers:

- this document;
- the stage configuration and the stage-runner and labelling modules;
- the fixture set;
- the five recipe files and the checker module;
- the Python, NumPy, pandas, scikit-learn and imbalanced-learn versions.

H cannot be reused, and rerunning it cannot repair a changed recipe. Any planned change
to these files, such as G2's weight caps or the E3 integration itself, must therefore
be finished before the pilot.

**Before the pilot:** the pilot consumes H, so it does not start while the §8.3 run
could only end `inconclusive` for want of L's training inputs. Before it starts, one of
these is completed, verified and recorded in ADR-001:

- the current L's training inputs are shown by either way in §8.3;
- L has been replaced, through a separately reviewed change under a valid policy, by a
  model with complete provenance. The replacement must be live; approving a
  replacement plan is not enough, and rule v1 is not bypassed or weakened for it.

Dropping L, weakening rule v1 or loading an unrelated artifact in its place is not a
route.

## 10. Still Open

- The confirmatory dataset N and its feature and label mapping (H1).
- The confirmatory plan, fixed and reviewed before any model is scored on N.
- The route for L, needed before the pilot (§9.3): a bounded lineage-recovery attempt
  or a reviewed replacement. Today's legacy artifact (`models/soc_model.joblib`, model
  version `feedback-20260811T100236Z`) has no provenance in its metadata, only
  feedback ticket IDs.
  - The local feedback database still holds ticket 1 and its review, both timestamped
    before the model's `trained_at`.
  - `state/retrain_report.json` matches that model version and reports KDDTest+
    evaluation, but records no dataset hash.

  Lineage recovery (§8.3) has not been tried. It also needs independent evidence for
  the base file and the August code and package versions, which the artifact does not
  record.
- What evidence a new activation needs if the recipe or checker changes after
  activation.
