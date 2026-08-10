## Results (train on KDDTrain+, test on KDDTest+ (cross-distribution))

- **Accuracy:** 74.40%
- **Macro F1:** 0.5149
- **Weighted F1:** 0.7033

| Attack family | Precision | Recall | F1-score | Support |
| --- | --- | --- | --- | --- |
| `normal` | 0.642 | 0.973 | 0.773 | 9,711 |
| `dos` | 0.961 | 0.766 | 0.853 | 7,460 |
| `probe` | 0.849 | 0.608 | 0.709 | 2,421 |
| `r2l` | 0.951 | 0.047 | 0.090 | 2,885 |
| `u2r` | 0.462 | 0.090 | 0.150 | 67 |
