# NSL-KDD dataset

Place these two raw files in this directory before running the pipeline:

- `KDDTrain+.txt` (125,973 records)
- `KDDTest+.txt` (22,544 records)

Obtain NSL-KDD through the [University of New Brunswick dataset page](https://www.unb.ca/cic/datasets/nsl.html). Review the dataset's terms and citation guidance there. The raw files are intentionally excluded from Git and the Docker build context.

## Verify provenance and integrity

Record the source URL and download date for every copy. The files used to validate this project had these SHA-256 checksums:

```text
KDDTrain+.txt  1b86d2f957b33082081bba410fe129b475efebcc13c9014c3f447c8271aadf95
KDDTest+.txt   fa46b0935342616aa83b7c2578db355b6a7aabbc492248172c7a1e8b7ab8f84
```

Verify them on macOS or Linux:

```bash
sha256sum data/KDDTrain+.txt data/KDDTest+.txt
```

Or in PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 data/KDDTrain+.txt, data/KDDTest+.txt
```

If a trusted provider publishes different files or checksums, do not silently replace these reference values. Record the new source, version, checksums, and row counts with the evaluation results so comparisons remain auditable.
