# ADR-002: Feedback Weight Caps (G2)

**Status:** Proposed. These are options for the owner's decision; nothing is
implemented, and today's caps stay in force. The chosen option lands as its own PR
before the E3 recipe is frozen for the pilot
([protocol §9.3](e3_protocol.md)).
**Date:** 28 November 2026
**Deciders:** repository owner; reviewed under `AGENT-PROTOCOL.md`

## Context

**Today's caps** (`weight_plan` in `src/feedback_policy.py`):

- Each consensus correction gets the requested weight (1-60, default 25).
- The total explicit weight must stay within 10% of B, the training rows after the
  optional random oversampling.
- Each reviewer label's credited share must stay within 5% of B.
- If either cap is exceeded, all correction weights are scaled down by one factor.

**What the Random Forest does with those weights:**

- `class_weight="balanced"` multiplies every explicit weight by n / (5 × family rows),
  computed from unweighted row counts.
- A correction's class-weighted mass is therefore its weight × its target family's
  class weight, and today's caps never see that factor.

**Default mode, with oversampling.** Oversampling, the default for governed
retraining, gives every family the majority family's row count.

- Class weights are about 1.
- 10% of B equals 50% of any one family's rows.
- At production size (full KDDTrain+), scaling starts at 1,347 corrections at
  weight 25 (562 at weight 60).

**Without oversampling** (`--no-smote`, which governed retraining also accepts), class
weights run from 0.37 for normal to 484 for U2R.

- Five U2R corrections at weight 25 then carry 70.6% of the U2R family's final
  class-weighted mass and 30.9% of all training mass.
- Today's cap first scales a cohort only at 504 corrections.

Every cap here bounds a training quantity, the weight mass. None of them guarantees a
bound on any prediction metric.

## Terms

- **Baseline family rows**, n_t: the training rows of family t before corrections are
  added, after the optional oversampling. With oversampling every family has the
  majority's count; without it, its own count.
- **Final family mass:** the family's class-weighted mass after its corrections are
  added, class weight × (n_t + the corrections' explicit weight).
- **Share of final mass and share of baseline rows:** a cap of f of the final family
  mass is the same as a cap of f / (1 − f) of the baseline family rows. For example,
  5% of the final mass is about 5.26% of the baseline rows.

## Measurements (Development Data Only)

The script is `scripts/measure_weight_caps.py`, and the record is
[`evaluation/g2_weight_caps/results.json`](evaluation/g2_weight_caps/results.json).

- **Data:** D is KDDTrain+ without the E3 hold-out (100,778 rows). It is split once,
  stratified and with seed 20261128, into 80,622 fit rows and 20,156 check rows.
- **Data boundary:** the script hashes the KDDTrain+ file's bytes to check them against
  the hold-out manifest, then parses the file with the hold-out lines skipped. So no
  hold-out row is parsed, mapped, split, fitted or scored. KDDTest+ is not opened.
- **Fits:** the production RF recipe. Seed 42 for every cohort fit; one baseline uses
  seed 2001 for comparison.
- **Cohorts:** one cohort sample per point, drawn from fit rows in a seeded order and
  skipping any row whose inputs equal a check row.
- **Seed comparison:** refitting the baseline with seed 2001 instead of 42 changed 3
  check predictions.
- **Baseline:** check accuracy is 99.89%, since these are in-distribution rows.

### DoS rows relabelled as normal, with oversampling

Share below is the cohort's mass as a share of one family's baseline rows: 43,099 in the
fit split after oversampling.

| Cohort | Weight per row | Mass (share of baseline rows) | Check predictions changed | DoS recall | Accuracy |
|---|---|---|---|---|---|
| 250 | 60 | 15,000 (35%) | 6 | −0.07 pt | −0.03 pt |
| 1,000 | 21.55, today's cap | 21,550 (50%) | 14 | −0.15 pt | −0.05 pt |
| 2,500 | 8.62, today's cap | 21,550 (50%) | 20 | −0.22 pt | −0.07 pt |
| 1,000 | 25 | 25,000 (58%) | 18 | −0.20 pt | −0.06 pt |
| 1,000 | 35 | 35,000 (81%) | 36 | −0.44 pt | −0.15 pt |
| 1,000 | 45 | 45,000 (104%) | 186 | −2.46 pt | −0.89 pt |
| 1,000 | 60 | 60,000 (139%) | 1,486 | −20.17 pt | −7.36 pt |
| 2,500 | 60 | 150,000 (348%) | 6,066 | −82.50 pt | −30.06 pt |

Smaller cohorts (mass up to 6,250) changed at most 5 check predictions, against 3 in
the seed comparison. The record has every run.

### Normal rows relabelled as U2R: where class balancing matters

| Mode | Cohort | Weight | Check predictions changed | Normal recall | Check rows predicted U2R |
|---|---|---|---|---|---|
| Oversampled | 20 | 25 | 2 | 0.00 pt | 7 (baseline 7) |
| Without oversampling | 5 | 1 | 2 | 0.00 pt | 7 |
| Without oversampling | 5 | 25 | 3 | 0.00 pt | 7 |
| Without oversampling | 20 | 25 | 206 | −1.90 pt | 211 |

Duplicating normal fit rows with their own label, up to 1,000 rows at weight 60,
changed at most 4 check predictions. The selection did not check whether the baseline
already predicted those rows correctly.

## Findings

These describe one split, seed 42, one cohort sample per point and two family pairs.
They are observations, not general thresholds or tolerances.

1. **Today's cap kept the measured flips small in the default mode.** At today's cap
   (50% of a family's baseline rows), DoS recall moved by at most 0.22 pt. The 1,000-row
   flip lost 0.44 pt at 81% of the baseline rows and 2.46 pt at 104%.
2. **Without oversampling, today's cap does not account for class weights.** Twenty U2R
   corrections at weight 25 changed 206 check predictions. At production size today's
   cap would not scale that cohort.
3. **Influence grew with mass relative to one family.** In these runs, influence grew
   with the cohort's mass relative to one family's baseline rows, and sharply once it
   approached that family's size.

## Options

"First scaled" is the smallest cohort, all aimed at one family, whose weights the
option scales, at production size, given as weight 25 / weight 60. All numbers are in
the record.

| Option | What it bounds | First scaled: normal, with oversampling | Normal, without | U2R, with oversampling | U2R, without |
|---|---|---|---|---|---|
| **O0** Today | explicit weight ≤ 10% of base rows (global) | 1,347 / 562 | 504 / 210 | 1,347 / 562 | 504 / 210 |
| **O1** Class-weighted total | class-weighted corrections ≤ 10% of all class-weighted mass (global) | 1,524 / 629 | 1,524 / 629 | 1,524 / 629 | 2 / 1 |
| **O2** Family share, f = 5% | class-weighted corrections ≤ f of the target family's final mass (per family) | 142 / 60 | 142 / 60 | 142 / 60 | 1 / 1 |
| **O2** Family share, f = 1% | as above | 28 / 12 | 28 / 12 | 28 / 12 | 1 / 1 |
| **O3** Original-count family share, f = 5% | as O2, with the family's rows before oversampling as n_t | 142 / 60 | 142 / 60 | 1 / 1 | 1 / 1 |
| **O4** Always oversample, global 1% | explicit weight ≤ 1% of base rows (global), and governed retraining always oversamples | 135 / 57 | not applicable | 135 / 57 | not applicable |

- **O0, today:** the simplest. It ignores class weights. With oversampling it allows
  50% of a family's baseline rows.
- **O1, class-weighted total:** accounts for class weights (finding 2). It leaves
  today's 50%-of-a-family concentration in the default mode.
- **O2, family share:** a per-family budget of f / (1 − f) × n_t in explicit weight.
  - The class weight cancels within a family, but n_t does not: it depends on the mode.
  - So rare families get very different budgets with and without oversampling. U2R is
    first scaled at 142 corrections with oversampling and at the first one without.
  - Without oversampling, rare-family corrections become nearly inert (U2R: about 0.005
    effective weight each at 500 corrections).
- **O3, original-count family share:** like O2, but n_t counts rows before
  oversampling. Rare families get tiny budgets in both modes.
- **O4, always oversample with a global 1%:** one budget shared by every target family.
  For a cohort aimed at one family it is numerically close to O2 at 5%. For a mixed
  cohort it is not, because O2 gives each family its own budget. It also gives up
  `--no-smote` for governed retraining.

### Choices to Fix Before Implementing O2 or O3

The calculator models one target family at a time, so mixed cohorts are not measured.
These rules have to be chosen:

1. **Scaling across families:**
   - Give each target family its own scale factor (proposed).
   - Or use one factor for the whole cohort, so the most constrained family throttles
     the rest, as today's caps do.
2. **Reviewer cap:**
   - Credit each reviewer label against half of each family's budget, splitting weight
     equally between endorsers as today (proposed).
   - Or use one per-reviewer budget across families.
3. **Global budget:** keep today's 10% total as well, or drop it. Under O2 the family
   budgets sum to at most f / (1 − f) of the base rows, which is 5.26% at f = 5%, so a
   10% explicit-weight total could never bind. It would only be defence in depth.

## Interaction with E3

- **Criterion (b) coverage is a credible risk, not a predicted outcome.** The pilot's
  flip cohorts (protocol §8.2: 50 to 2,500 DoS rows relabelled as normal, requested
  weight 60) follow the current policy.
  - Under today's caps, the 1,000- and 2,500-row flips get 50% of a family's baseline
    rows. On development data, with one seed, those moved DoS recall by 0.15 and
    0.22 pt.
  - The pilot's oracle differs: it uses ten paired seeds on the pilot's E and counts a
    loss beyond the cap on any metric. So it may still find `harmful_B` candidates.
  - A smaller permitted weight does not guarantee a smaller loss on every metric.
  - So criterion (b), which needs at least four `harmful_B` candidates, may lack
    coverage. Whether and how to address that is for a reviewed protocol change, before
    H is used.
- **G2 changes hashed recipe files.** It edits `src/feedback_policy.py`, and possibly
  `src/retrain.py`, so it must land before the pilot.
- **Pilot settings change only through protocol review.** That includes §8.2's
  statement that the weight plan scales the two largest sizes; under O2 at 5%, all four
  sizes would be scaled. Any such change must be reconciled by an explicit protocol
  review before H is used. An ADR-001 status note alone must not change the approved
  design.

## Provisional Proposal

O2, the class-weighted family share, at f = 5%, with per-family scaling, the reviewer
cap at half of each family's budget, and no separate global cap.

This is a policy trade-off, not an empirically validated protection.

- **For it:** it bounds, per family, the mass that grew with influence in these runs,
  and it accounts for class weights in both modes.
- **Against it:** large cohorts are scaled, and rare-family corrections without
  oversampling have almost no effect.
- **The fraction is the owner's call.** These measurements do not show that 5% is
  better than 1% or any other value.

If simplicity matters more, O4 behaves similarly for single-family cohorts in the
default mode.

## Decisions Needed

1. Which option, and its fraction.
2. For O2 or O3, the three implementation rules above.
3. Whether governed retraining keeps `--no-smote`.
4. Whether to change the pilot for criterion (b). That would be a reviewed protocol
   change before H is used.

## Reproduce

From the repository root, with `requirements.txt` installed and `data/KDDTrain+.txt`
present.

- The analysis takes seconds and uses only family counts: D's and the hold-out's as
  recorded in the manifest.
- The influence part fits 29 models and resumes from the record; each fit takes about
  20 seconds on two cores.

```bash
python -m scripts.measure_weight_caps --part analyse --output results.json
python -m scripts.measure_weight_caps --part influence --output results.json --limit 12
python -m scripts.measure_weight_caps --part influence --output results.json
```

On every resume, the influence part refits the seed-42 baseline of any mode with runs
still to do, and stops if its prediction digest differs from the recorded one.
