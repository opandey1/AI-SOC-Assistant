# UNSW-NB15 Transfer Validation

A Random Forest was trained only on NSL-KDD using the seven shared or closest-compatible flow fields listed below. No UNSW-NB15 labels were used for training, feature selection, threshold selection, or tuning.

| Evaluation | Rows | Accuracy | Balanced accuracy | Macro F1 | Binary attack F1 |
|---|---:|---:|---:|---:|---:|
| NSL-KDD stratified hold-out | 25,195 | 97.05% | 95.49% | 87.97% | 99.13% |
| UNSW-NB15 zero-tuning transfer | 63,461 | 58.89% | 20.64% | 16.02% | 2.77% |

## Transfer Per-Class Metrics

| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| normal | 58.65% | 100.00% | 73.93% | 37,000 |
| dos | 50.00% | 0.05% | 0.10% | 4,133 |
| probe | 0.00% | 0.00% | 0.00% | 10,235 |
| r2l | 100.00% | 3.14% | 6.09% | 11,715 |
| u2r | 0.00% | 0.00% | 0.00% | 378 |

## Harmonized Feature Contract

| NSL-KDD field | UNSW-NB15 field | Compatibility |
|---|---|---|
| `duration` | `dur` | Direct flow-duration measure |
| `protocol_type` | `proto` | Direct protocol category |
| `service` | `service` | Direct service category |
| `flag` | `state` | Approximate connection-state proxy |
| `src_bytes` | `sbytes` | Direct source-to-destination bytes |
| `dst_bytes` | `dbytes` | Direct destination-to-source bytes |
| `land` | `is_sm_ips_ports` | Closest same-endpoint indicator |

## Label Mapping

- Normal -> normal
- DoS and Worms -> dos
- Reconnaissance, Analysis, and Fuzzers -> probe
- Exploits and Backdoor -> r2l
- Shellcode -> u2r
- Generic -> excluded because the NSL-KDD five-family taxonomy has no defensible equivalent

Excluded rows: 18,871 ({"Generic": 18871}).

## Interpretation

The transfer score is expected to be materially lower than the in-domain hold-out. That gap measures dataset, capture, feature-definition, and attack-taxonomy shift; it is not presented as a production deployment score. The result establishes a reproducible external baseline and identifies where a live Zeek/SIEM feature adapter and modern training data are required.

Dataset: [UNSW-NB15, UNSW Canberra](https://research.unsw.edu.au/projects/unsw-nb15-dataset).
