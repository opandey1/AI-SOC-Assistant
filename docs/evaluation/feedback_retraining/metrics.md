# Analyst-Feedback Retraining Validation

This focused validation used a real `KDDTest+` row whose ground-truth family is `normal` but whose baseline fused verdict was `probe` at 78.72% confidence. The generated ticket was stored in SQLite, reviewed as a false positive, and supplied to `src.retrain` with the default feedback weight of 25.

| Measure | Before review update | After review update |
|---|---:|---:|
| Prediction for corrected row | `probe` | `normal` |
| Probability assigned to `normal` | 2.83% | 50.22% |
| `KDDTest+` accuracy | 74.40% | 74.01% |
| `KDDTest+` macro F1 | 51.49% | 51.42% |

The single correction changed the intended row while slightly reducing aggregate cross-distribution performance. This is evidence that the loop updates model behavior, not evidence that one review improves global quality. Production use would require a larger reviewed cohort, a held-out acceptance gate, rollback/version promotion, and drift monitoring.

Validation details:

- Test row: 33
- Stored ticket: 1
- Corrected class: `normal`
- Feedback examples: 1
- Random Forest updated: yes
- Isolation Forest updated: no
- Model artifact: `models/soc_model.joblib` (generated locally and gitignored)
