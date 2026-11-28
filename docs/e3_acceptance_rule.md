# ADR-001: Candidate Acceptance Rule (E3)

**Status:** Accepted by the owner on 27 November 2026: Option C with a deployed-model
check (see [Decisions](#decisions-owner-27-november-2026)). The protocol,
[`e3_protocol.md`](e3_protocol.md) (draft 8), was approved and merged in PR #11. Its
text will not be edited, so this record holds its status from now on (see
[Protocol](#protocol)). The protocol's operational freeze of the checker, runner,
fixture and recipe identities (§9.3) starts only at the pilot; until then
implementation and G2 can still change them. An offline checker is in review.
Switching the rule on still requires the remaining fixtures, a pilot on the hold-out
and a confirmatory run on new data (H1). Today's zero-tolerance gate stays in force.
**Date:** 26 November 2026 (measurements); 27 November 2026 (decisions and protocol)
**Deciders:** repository owner; reviewed under `AGENT-PROTOCOL.md`

## Context

A retrained candidate can be promoted only if it does not lower Random Forest accuracy,
fixed-five-class macro F1 or any of the five class recalls on the evaluation file
(KDDTest+). It is compared with a fresh baseline fitted with seed 42 and, when present,
with the active model (`acceptance_reasons` in `src/model_registry.py`). Any drop,
however small, rejects the candidate. In November two real-data candidates were
rejected this way while their macro F1 rose ([evidence](candidate_governance.md#real-data-review-evidence)).

Before choosing a different rule, this record measures how much the same metrics move
between comparable fits. With the seed fixed, a refit on identical data is
deterministic, so what is measured here is sensitivity to the Random Forest seed and to
small data changes on KDDTest+. It is not independent model-quality validation.

## Measurements

Matched controls on the real KDDTrain+ (125,973 rows; 336,715 after oversampling) and
KDDTest+ (22,544 rows), using the production preprocessing, Random Forest settings,
metrics and acceptance check. Raw results, including the validated baseline and
configuration, are in
[`evaluation/acceptance_variability/results.json`](evaluation/acceptance_variability/results.json).

| Arm | What differs from the seed-42 baseline |
|---|---|
| Seed only | Same data; Random Forest seeds 1000-1007 instead of 42 (8 fits) |
| Correction, 5 normal rows, weight 1.0 | Five correctly labelled normal training rows appended at weight 1; seed 42 (8 cohorts) |
| Correction, 5 normal rows, weight 25.0 | The same eight cohorts at the default feedback weight of 25; seed 42 |
| Same cohort, same seed | Cohorts 3 and 4 at weight 25, fitted with seeds 1000-1003 and compared with the seed-only fit of the same seed (8 comparisons) |

### Range of change against the seed-42 baseline (percentage points)

| Arm (vs seed-42 baseline) | Runs | accuracy | macro_f1 | normal | dos | probe | r2l | u2r |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Seed only | 8 | -0.35 to +0.34 | -0.93 to +1.04 | -0.07 to +0.07 | -0.20 to +0.32 | -0.37 to +4.75 | -2.01 to +1.14 | +0.00 to +1.49 |
| Correction, 5 normal rows, weight 1.0 | 8 | -0.51 to +0.12 | -0.70 to +1.28 | -0.05 to +0.09 | -0.75 to +0.31 | -0.99 to +1.28 | -1.98 to +1.04 | -1.49 to +2.99 |
| Correction, 5 normal rows, weight 25.0 | 8 | -0.55 to +0.01 | -0.42 to +0.57 | -0.08 to +0.10 | -1.01 to +0.17 | -0.95 to +1.28 | -2.22 to +0.55 | +0.00 to +2.99 |

The five class columns are recall. U2R has 67 evaluation rows and the baseline detects
6 of them, so one row is 1.49 pp and a sixth of the detections.

### How each rule would have decided

| Arm | Own predictions changed | Current rule | Not flagged by McNemar | Seed-range bands |
|---|---:|---:|---:|---:|
| Seed only | - | 0/8 pass | 6/8 | 8/8 pass |
| Correction, 5 normal rows, weight 1.0 | 0 | 0/8 pass | 3/8 | 1/8 pass |
| Correction, 5 normal rows, weight 25.0 | 0 | 0/8 pass | 1/8 | 0/8 pass |

"Own predictions changed" counts corrected rows whose predicted class changed, summed
over the arm. "Not flagged by McNemar" counts runs where a one-sided exact test on the
rows a candidate broke versus fixed gave p ≥ 0.05. That test looks for a net accuracy
drop; it does not show non-inferiority or protect each class. Weight-1 cohort 1, for
example, was not flagged (p = 0.0549) although it lost one of the six U2R detections.
Seed-range bands allow each metric to drop by up to the largest drop seen in the
seed-only arm (accuracy 0.35, macro F1 0.93, normal 0.07, DoS 0.20, probe 0.37, R2L
2.01, U2R 0.00 pp), so the seed-only arm passes them by construction.

### Same cohort, compared with a baseline of the same seed (pp)

Five normal rows at weight 25.

| Cohort | RF seed | accuracy | macro F1 | dos | r2l | Current rule |
|---:|---:|---:|---:|---:|---:|---|
| 3 | 1000 | +0.32 | +0.95 | +0.01 | +0.97 | pass |
| 3 | 1001 | -0.20 | +0.12 | -0.34 | -0.69 | reject |
| 3 | 1002 | -0.03 | -0.03 | -0.29 | -0.10 | reject |
| 3 | 1003 | +0.54 | +1.43 | +0.67 | +1.35 | pass |
| 4 | 1000 | +0.30 | +0.66 | +0.17 | +1.42 | pass |
| 4 | 1001 | -0.27 | -0.28 | -0.64 | -0.24 | reject |
| 4 | 1002 | -0.00 | -1.01 | -0.05 | -0.42 | reject |
| 4 | 1003 | +0.35 | +0.95 | -0.17 | +1.84 | reject |

## What the Measurements Show

1. Changing only the Random Forest seed moved accuracy by -0.35 to +0.34 pp and R2L
   recall by -2.01 to +1.14 pp. The current rule rejected all eight seed-only fits.
2. In all 16 seed-42 correction runs, none of the corrected rows changed prediction,
   and the current rule rejected every candidate. DoS recall fell in 7 of 8 cohorts at
   each weight.
3. Against a baseline of the same seed, the same two cohorts passed the current rule in
   3 of 8 comparisons, with accuracy changes from -0.27 to +0.54 pp and DoS changes of
   both signs. In these sampled cohorts the verdict changed with the seed. This design
   does not separate how much of each change comes from the seed and how much from the
   corrections.
4. The one-sided McNemar test flagged 2 of the 8 seed-only fits as significantly worse,
   so a paired test on a single pair of fits does not remove the seed sensitivity.
5. A seed average can hide what the deployed model does. For cohort 3, the five-seed
   mean change (seeds 42 and 1000-1003) is +0.10 pp accuracy, -0.06 pp DoS and +0.01 pp
   R2L, yet its seed-42 model, the one that would be deployed, loses 0.36 pp DoS and
   1.49 pp R2L recall against the seed-42 baseline.

**Limits.** Eight seeds and eight cohorts from one family (normal) at one size (five
rows), on one evaluation file. The ranges are indicative, not precise estimates. The
16 seed-42 correction runs share a seed, so their results are correlated with each
other. These results do not estimate a general promotion rate.

## Options Considered

### Option A: Keep zero tolerance

| Dimension | Assessment |
|---|---|
| Complexity | None; current code |
| Compute | One candidate fit |
| Measured behaviour | Rejected 8/8 seed-only fits and 16/16 correction candidates |
| Explainability | Simple to state |

**Pros:** maximally conservative; no new code.
**Cons:** every candidate in these runs was rejected, including refits that changed
only the seed.

### Option B: Tolerance bands calibrated from seed variation

| Dimension | Assessment |
|---|---|
| Complexity | Low: calibrate once per dataset or parameter change (N seed-only fits), then compare against bands |
| Compute | One candidate fit, plus calibration |
| Measured behaviour | Correction candidates at seed 42: 1/8 (weight 1) and 0/8 (weight 25) pass, mostly failing on DoS |
| Explainability | Good: "no metric fell by more than seed changes alone moved it" |

**Pros:** cheap at promotion time; easy to explain; judges the deployed fit directly.
**Cons:** bands from a handful of seeds are rough; small classes move in whole-row
steps; bands describe variability, not an acceptable loss.

### Option C: Seed-averaged paired comparison plus an artifact-level guard

Fit the baseline and the candidate with the same predeclared K seeds and average the
per-seed changes. Separately, the deployed fit (a predeclared seed, such as 42) must
stay within artifact-level limits against both the fresh same-seed baseline and the
active model. The deployment seed is never chosen by looking at evaluation results.

| Dimension | Assessment |
|---|---|
| Complexity | Medium: K fits per candidate, cached baseline fits keyed by dataset digests, parameters and seeds, two sets of limits |
| Compute | K candidate fits instead of one; at about 30 s per fit on two cores, K = 5 takes about 2.5 minutes once baseline fits are cached |
| Measured behaviour | Not measured as a rule. On cohort 3 the averages would pass while the seed-42 fit exceeds the exploratory DoS and R2L settings below, so the artifact guard would decide |
| Explainability | Medium: report each per-seed change, the average and the deployed fit |

Exploratory settings, not validated margins: two standard errors of a five-seed mean
of the seed-only differences give accuracy 0.23, macro F1 0.57, normal 0.05, DoS 0.20,
probe 1.43, R2L 1.03 and U2R 0.47 pp. They describe baseline variability. They are not
the uncertainty of a correction's effect, and they are not a judgement of how much
loss is acceptable. Acceptable losses, especially for small classes such as U2R where
one row is a sixth of the detections, have to be set as policy before evaluation.

**Pros:** looks past which forest a single seed produced, while the guard still bounds
the model that is actually deployed.
**Cons:** K times the training time; two sets of limits to define and calibrate; limits
need recalibrating when data or parameters change.

### Option D: Paired McNemar test on one fit

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Compute | One candidate fit |
| Measured behaviour | Flagged 2/8 seed-only fits as worse; did not flag 3/8 (weight 1) and 1/8 (weight 25) correction candidates |
| Explainability | Medium: rows broken versus fixed |

**Pros:** cheap; uses row-level evidence.
**Cons:** treats one forest as fixed, so it still reacts to the seed; it tests net
accuracy only and does not protect individual classes.

## Trade-off Analysis

A, B and D judge a single fit. The measurements show single-fit comparisons move with
the seed: A rejected everything and D flagged a quarter of the seed-only refits. B is
cheap and judges the deployed fit, but its bands only describe variability. C adds an
estimate that is less tied to one seed, but an average alone could approve a deployed
model with a larger class loss (point 5), so it is only safe with the artifact guard.

## Recommendation

If the owner wants seed-robust decisions, Option C with the artifact-level guard: K = 5
predeclared seeds, a predeclared deployment seed, and limits for the deployed fit
against both the fresh baseline and the active model. Before enabling it:

1. Set class-specific acceptable-loss limits as policy, both for the seed-averaged
   changes and for the deployed fit.
2. Calibrate seed sensitivity with fresh seeds. That measures robustness to
   initialization on the same rows, not model quality.
3. Freeze the protocol (seeds, limits, data and metrics), then run the final check on
   evaluation data that played no part in designing it. KDDTest+ has informed this
   record on every row, so neither it nor a split of it can be that untouched test.

Enable C only when both the predeclared mean limits and the deployed-artifact guard
are in place. The numbers in this record are exploratory starting points, not limits.

## Consequences

- **Easier:** decisions can show per-seed changes, and an average no longer depends on
  one forest.
- **Harder:** retraining takes longer; baseline fits need a cache; two sets of limits
  must be defined, calibrated and shown in Model Operations.
- **Revisit:** limits after more seeds and cohorts; rules for tiny classes; how the
  active model, which is one saved fit, enters the averaged part of the comparison.

## Decisions (owner, 27 November 2026)

1. **Rule:** Option C, the five-seed paired average plus a check on the deployed model.
2. **Class losses:** size-scaled allowances from seed calibration, capped by fixed
   maximum losses: accuracy 1 point; macro F1 and normal, DoS and probe recall 2 points;
   R2L recall 3 points.
3. **U2R:** row by row for the deployed model. It must still catch every U2R row that the
   fresh baseline and the live model catch, where "catch" means the Random Forest
   predicts U2R. The five-seed average is not used for U2R.
4. **Deployed model:** seed 42, checked against both the fresh seed-42 baseline and the
   live model, with single-retrain allowances.
5. **Evidence before switching on:** a fixed, stratified 20% hold-out of KDDTrain+,
   set aside on 27 November 2026, for an in-distribution pilot, and then a confirmatory
   run on a dataset not used so far, which waits for the H1 mapping work.
6. **Pilot thresholds:** the average check must reject every candidate whose ten-seed
   average loss exceeds a cap, and at most a quarter of acceptable candidates may fail
   to pass. The minimum qualifying counts stay, and errors or inconclusive runs never
   count as rejections. A miss is a pilot failure, not necessarily a code defect: the
   oracle labels candidates with ten seeds while the gate decides with five different
   ones, and an allowance below the cap guarantees rejection only when the same
   measured loss is compared with both. If the two averages disagree, that is
   investigated rather than assuming the checker is broken. A pilot pass does not
   replace confirmation on new data.

Decisions 2, 3 and 5 were refined the same day after Codex's review of the first
protocol draft found the U2R wording, the role of the hold-out and the limits'
meaning underspecified. Decision 6 answers Codex's request, in the review of draft 3,
to confirm the pilot's pass thresholds with the owner.

## Protocol

The protocol for the chosen rule is [`e3_protocol.md`](e3_protocol.md), draft 8,
approved and frozen (below). Draft 1 was a "Frozen Protocol" section in this record.
Codex's review of it found four problems:

- its control candidates were labelled by the rule under test;
- the hold-out can only give pilot evidence;
- the U2R wording allowed hidden losses;
- the build contract was incomplete.

Draft 2 addressed those. Codex's review of draft 2 found five more:

- the pilot's harm labels reused the gate's allowances and never checked the deployed
  model against its two references;
- its pass criteria could be met with no valid decisions;
- a one-row allowance floor loosened the owner's min(measured, cap) rule;
- skipped checks, outcome precedence, live-only U2R detections and the promotion
  recheck were still unspecified;
- the confirmatory run mixed the gate's dataset with the new dataset.

Draft 3 addressed those. Codex's review of draft 3 found three more and asked for
decision 6:

- the confirmatory run could pass with no R2L or U2R evidence on the new data, and its
  disjointness check was too narrow;
- switching the rule on checked only two identities, and decisions were not bound to
  the approved calibration;
- the evaluation labels themselves were never validated.

Draft 4 addressed those. Codex's review of draft 4 found three more:

- the activation evidence was not joined to the reports: a passing confirmatory report
  could have tested a different calibration from the one switched on, and the reports
  had no defined contents or frozen protocol version;
- the new-data exclusions assumed the live model's training inputs were known;
- the input checks could compare identities before confirming the inputs existed, and
  calibration limits were identified by hash but never validated.

Draft 5 addressed those. Codex's review of draft 5 found two more:

- switching the rule on replayed summaries (metric values, loss counts, file hashes)
  rather than the labelled rows and per-row predictions they came from, so it could
  not establish measured spreads or U2R coverage;
- the fixture report and the confirmatory plan were not bound to the activation by
  explicit identities.

It also asked for the live model's training-input route to be settled before the
hold-out is used. Draft 6 addressed all three. Codex's review of draft 6 found three
more:

- prediction files were not joined to one fitted artifact per fit, recorded before
  scoring;
- the new dataset's exclusions could not prove that every source row was accounted
  for;
- an exact refit alone could not establish the live model's historical inputs, and an
  approved but unfinished replacement could not unblock confirmation.

Draft 7 addressed those. Codex's review of draft 7 found one more: the owner-committed
pre-score anchor was required but neither checked by the stage runner nor replayed at
activation, and the fit manifest's training identities and cohorts were stored
without being checked against the frozen design. Draft 8 addressed it. Earlier drafts
remain in Git history and `HANDOFF.md`.

### Approval (27 November 2026)

Codex approved draft 8 with no findings (`HANDOFF.md`, 2026-11-27T12:28Z), and the
owner merged it in PR #11 (`99a7a4a`). Under protocol §3 the document is not edited
again before activation, so its own header still reads "Draft for review". Its status
is kept here instead.

The approved text is not yet the §9.3 freeze. That freeze fixes `protocol_id`,
`fixtures_id`, `recipe_id` and `checker_id` from the pilot until activation.
`protocol_id` also covers a stage configuration and runner modules that do not exist
yet, and the checker, fixtures and recipe will keep changing with the implementation
and with G2 until the pilot starts.

The approving review added two readings for the build:

- "In the repository's history" means reachable from the approved branch tip, not
  merely an existing Git object.
- The anchor enforces the order of a trusted local workflow. It proves neither Git
  authorship nor time.

### Implementation Progress

- **Offline checker** (protocol §5): `src/acceptance_v2.py`, with synthetic-prediction
  fixtures in `tests/test_acceptance_v2.py`. In review.
  - The module is pure: it fits nothing, loads no model and touches neither training,
    promotion nor the registry.
  - Its file SHA-256 is the protocol's `checker_id`, which is not frozen until the
    pilot.
  - Evaluation rows must have exactly 41 scalar, finite inputs and a family label
    before anything is hashed.
- **Still to build before the pilot:**
  - the §8.2 labelling-rule fixtures;
  - the stage runner with its anchor check;
  - the §9 evidence records and activation replay;
  - the registry's rule state.

## Reproduce

From the repository root, with `requirements.txt` installed and the NSL-KDD files in
`data/`. Run as a module so `src` imports resolve. Each run appends to the output file.
Every resume and summary first validates the whole record: schema version and unique
experiment keys, plus, on resume, the dataset digests, baseline metrics, ordered
baseline prediction digest and preprocessing and Random Forest settings against a fresh
refit (`--runs 0` performs only that check and writes nothing). A record from before
schema 2 must be upgraded explicitly with `--migrate`, which runs the same validation
first. The script always writes LF line endings.

```bash
python -m scripts.measure_retraining_variability --arm seed --runs 8 --output results.json
python -m scripts.measure_retraining_variability --arm correction --weight 1 --runs 8 --output results.json
python -m scripts.measure_retraining_variability --arm correction --weight 25 --runs 8 --output results.json
for seed in 1000 1001 1002 1003; do
  python -m scripts.measure_retraining_variability --arm correction --weight 25 \
    --rf-seed "$seed" --start 3 --runs 2 --output results.json
done
python -m scripts.measure_retraining_variability --summarize --output results.json
```

The first two tables are the last command's output on the committed results; the
same-seed table abbreviates the listed rejection reasons to "reject". These runs used
Linux, Python 3.10.12 and two CPU cores (about 30 s per fit).

The hold-out file records its source digest, method, scikit-learn version, class
counts and the SHA-256 of its index list. The committed indices are authoritative; the
method is recorded so the selection can be audited, not regenerated on a different
library version.
