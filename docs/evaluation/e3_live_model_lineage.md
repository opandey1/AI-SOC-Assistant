# E3 Live-Model Lineage Recovery: Evidence for Review

**Status:** lineage recovery is **not established**, and the "Before the pilot"
prerequisite ([protocol §9.3](../e3_protocol.md)) stays open. No route has been
adopted, and nothing here changes the live model, the registry or rule v1.

- Two Codex reviews (`HANDOFF.md`, 2026-11-27T14:07Z and 16:02Z) found that the
  records make `data/KDDTrain+.txt` the likely base file but do not show it was
  configured for the August run.
- The exact refit cannot stand in for that independent evidence.
- Unless a record written at the time of the run names its training input, the route
  is the separately reviewed, provenance-complete replacement under rule v1. That
  replacement must be live before H is used; see [Route](#route).

**Gathered:** 27 November 2026, as a time-boxed investigation with one isolated refit.

Protocol §8.3 accepts lineage recovery for a live model without provenance only if a
reviewed record shows both of these:

- independent evidence for each claimed input, recorded no later than training;
- a refit from those inputs, with the named code, that equals the model on everything
  that affects inference.

## The Live Model

| Item | Value |
|---|---|
| File | `models/soc_model.joblib` (local, not in Git) |
| SHA-256 | `816359aac3ca1c01a3aae2d2cfcb5ce883b376c7840ba9154372a2741aefb1f0` |
| Model version | `feedback-20260811T100236Z` |
| `trained_at` | 2026-08-11T10:02:36Z |
| Metadata | 1 feedback example, ticket IDs `[1]`, weight 25; no provenance block |

## Claimed Inputs

- **Base file:** `data/KDDTrain+.txt`, SHA-256
  `1b86d2f957b33082081bba410fe129b475efebcc13c9014c3f447c8271aadf95`. The test file
  used for the report was `data/KDDTest+.txt`, SHA-256
  `fa46b0935342616aa83b7c2578db355b6a7aaabbc492248172c7a1e8b7ab8f84`.
- **Feedback row:** ticket 1, reviewed as a false positive and corrected to normal, at
  weight 25.
  - The model had predicted it as probe.
  - Its 41 inputs (input hash
    `94056ed64a359b522b80c416a2c3b7c077eed1a4ae76b41c32a0a1c3576fdf3e`) equal those of
    KDDTest+ data row 33 (0-based), true label normal. The ticket's source is the
    validation replay.
- **Code:** commit `cf79cd6` (2026-08-11T11:44:44Z), the first commit containing
  `src/retrain.py` and `src/feedback.py`.
- **Packages:** NumPy 1.26.4, pandas 2.2.3, scikit-learn 1.5.2 and imbalanced-learn
  0.12.4.

## Records Found

Each record was made no later than training and none was derived from the refit. The
"Shows" column says how far each one goes.

| Input | Record | Recorded | Shows |
|---|---|---|---|
| Base file | `data/README.md` at commit `a09e1cf` lists the KDDTrain+ SHA-256 above as the file used to validate the project | 2026-08-10T17:16:50Z | which file the project documented, not which file the run read |
| Base file | `data/KDDTrain+.txt` was last written then | 2026-04-18T18:04:44Z (filesystem modification time) | that the file at the default path had today's content on 11 August, not that the run used the default |
| Feedback row | `state/soc_feedback.db` (local; SHA-256 `5f72d543a4127f5a4a543be3fb54f6e60ea30f6daa620320363d48be2772c8f2` today) holds ticket 1 and one review | ticket and review both 2026-08-11T10:01:49Z | the feedback input, named by the model's own metadata (next row) |
| Feedback row | The model's own metadata lists ticket IDs `[1]`, one example, weight 25 | at training | which ticket the run used |
| Packages | `requirements.txt` at `dc1c558`, the last commit before training, pins the four versions above | 2026-08-11T06:49:34Z | the pinned environment |
| Packages | The model file's pickle records scikit-learn 1.5.2 | at training | the scikit-learn version the run used |

### What Is Missing: the Run's Configuration

The feedback row and the packages are tied to the run by the model's own records. The
base file is not. These records corroborate `data/KDDTrain+.txt` as the likely base file:

1. **Output location.** The run's report, `state/retrain_report.json` (written
   2026-08-11T10:02:37Z; local; SHA-256
   `d9449d1c3c3e20471894ba75f9bf9c995320a9756301b201c54dd21ec7a702a3`), names the
   same model version. It records the resolved output path, this repository's
   `models\soc_model.joblib`. It does not record the working directory, the effective
   `--train` or any input digest.
2. **Default lookup.** Given no `--train`, the loader at `cf79cd6`
   (`resolve_dataset_paths`) looks for `KDDTrain+.txt` first in the working directory
   and then in its `data/` folder; the project root is searched the same way when
   passed in. There is no root-level copy today, and none is recorded.
3. **Unchanged file.** `data/KDDTrain+.txt` has not been rewritten since 2026-04-18. Its
   SHA-256 is the one `data/README.md` recorded the day before training. Its
   metadata-change time is 2026-08-30, after training; that records a change to file
   metadata, not a content write, and its cause is unknown.
4. **Supporting only:** the report's baseline accuracy 0.7439673527324343 and macro F1
   0.5149050224812864 on 22,544 rows are reproduced exactly by training on this file.
   So is every other report field except the version, the time and the output path.

None of these shows that the run was configured with this file. The default may not
have been used: an explicit `--train`, or a different working directory, cannot be
excluded. The exact refit below shows equivalence for the claimed inputs, not which
inputs the run used. §8.3 therefore lacks independent evidence for the base file. What
would close the gap is a record made at the time of the run that names its training
input or its digest, such as the command line or a log.

## Exact-Match Refit

One refit was run on 27 November in an isolated directory, on Linux with Python 3.10.12
and the package versions above:

- the code from `git archive cf79cd6`;
- a copy of the feedback database;
- the two data files.

This is §8.3's equivalence half only. It shows that the claimed inputs and `cf79cd6`
reproduce the live model. It does not show that the August run used those inputs (see
above).

`scripts/compare_model_artifacts.py` then compared the live model with the refit within
its stated scope (`SCOPE` in the script). It found them equal on all of it:

- the artifact's feature names, isolation calibration, threshold and format version;
- every parameter and fitted attribute of the encoder and the scaler;
- every parameter and attribute of both forests and of each tree;
- each tree's structure, split features, thresholds, leaf values and node counts;
- end-to-end scores for 300 synthetic raw records through the application's own
  `transform_connections` and `score_connection`, single-threaded. Those records
  include an unseen category.

The comparator has its own tests on small synthetic artifacts
(`tests/test_compare_model_artifacts.py`). Each of these makes the comparison fail:

- changing only the scaler's `with_mean` (both the scaler check and the end-to-end
  scores fail);
- the encoder's handling of unknown categories;
- one tree threshold;
- the feature order;
- the isolation calibration or threshold.

On the real refit, turning off the scaler's centring also fails it, as do changing one
tree threshold by 1e-9 or one scaler mean by 1e-12.

Differences outside the scope, none of which affect inference:

- `classes_` is stored as int32 in the live model and int64 in the refit. The values and
  order are equal. NumPy 1.x defaults to 32-bit integers on Windows, where the live
  model was trained (its report names a Windows path).
- Node impurity differs in the last bit in one node of each of 8 of the Isolation
  Forest's 200 trees (at most 1.1e-16). Impurity is a training statistic that
  prediction never reads. The Random Forest's impurities are identical.
- The model version and `trained_at` differ, as expected.
- Multi-threaded prediction can differ by one unit in the last place even between two
  calls on the same model, because threads add tree outputs in varying order. So the
  comparison runs single-threaded.

## Gaps

- **Configuration:** no record names the run's training input (see above). This is
  what keeps lineage recovery unestablished.
- **Code:** the retraining code had not been committed when the model was trained. The
  refit used the first commit that contains it, made 1 h 42 min later. The exact match
  shows that this code reproduces the model from the claimed inputs. It does not prove
  that the same file ran in August. Protocol §8.3 asks for independent evidence of the
  inputs and only names the code.
- **Python version:** not recorded at training.
- **Local records:** the feedback database, the report and the filesystem times are
  local and mutable. Nothing makes them tamper-evident. This is the same trusted local
  workflow as the rest of the registry.
- **The live model has seen one evaluation row.** It was retrained with KDDTest+ row 33
  at weight 25.
  - That retraining changed the whole forest, so its effect on the live-model check
    (§5, check 5) and on the pilot's Target A is not confined to that row's score. It
    is not measured here.
  - E stays the approved KDDTest+. Removing the row would be a reviewed protocol change
    with fresh identities and calibration.
  - For N, the existing disjointness rule (§8.3) already removes rows equal to the live
    model's feedback rows.

## Route

The prerequisite stays open, with two ways forward:

1. **Lineage recovery reopens** only if a record made at the time of the 11 August run
   names its training input or digest. Examples are the command line in terminal
   history, or a log written by the run. The owner would add it, and it would be
   reviewed with this record.
2. **Otherwise, a separately reviewed, provenance-complete replacement** of the live
   model, made under rule v1. It must be live before H is used.

An open question for the owner and review: how a replacement would pass v1.

- **The rule:** v1 promotes a candidate only if no metric falls, even slightly, against
  the fresh baseline and, when one exists, the live model.
- **The record so far:** every real-data candidate tried (ADR-001) failed that check.
  That does not show that every possible replacement would.
- **The cohort:** today's governed retraining also needs at least five eligible
  consensus corrections, and the feedback database holds one review.

Any other path would need its own reviewed decision. This record does not propose
bypassing or weakening v1.

## Related Fix

`data/README.md` gave KDDTest+'s SHA-256 with 63 hex digits, one `a` short. This change
corrects it to the file's actual SHA-256, which the E3 measurement record
(`docs/evaluation/acceptance_variability/results.json`) also lists. KDDTrain+'s
checksum, the one used as evidence above, was already correct.

## Reproduce

From the repository root, with `requirements.txt` installed. Run the comparator only on
trusted local artifacts, because joblib files are executable pickles.

```bash
REPO="$(pwd)"
mkdir -p /tmp/refit/code
git archive cf79cd6 | tar -x -C /tmp/refit/code
cp state/soc_feedback.db /tmp/refit/feedback_copy.db
cd /tmp/refit/code
python -m src.retrain --database /tmp/refit/feedback_copy.db \
  --output /tmp/refit/soc_model.joblib --report /tmp/refit/retrain_report.json \
  --train "$REPO/data/KDDTrain+.txt" --test "$REPO/data/KDDTest+.txt" --feedback-weight 25
cd "$REPO"
python -m scripts.compare_model_artifacts models/soc_model.joblib /tmp/refit/soc_model.joblib
```

The comparison prints JSON and exits 0 only when the two artifacts are equal within its
scope. The refit takes about a minute on two cores.
