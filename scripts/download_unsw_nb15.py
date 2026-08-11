"""Download the UNSW-NB15 test partition from a checksum-verified mirror."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import requests

UNSW_TEST_URL = (
    "https://huggingface.co/datasets/Mouwiya/UNSW-NB15-small/resolve/main/"
    "UNSW_NB15_testing-set.csv?download=true"
)
UNSW_TEST_SHA256 = "734fe6642edf758f7c94d7d9149426b49d202fe8e7bf0bef47392489c3c0a559"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_dataset(output_path: str | Path, *, force: bool = False) -> Path:
    """Download atomically and reject any payload with an unexpected checksum."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        if sha256_file(output) == UNSW_TEST_SHA256:
            return output
        raise ValueError(
            f"Existing file has the wrong checksum: {output}. Use --force to replace it."
        )

    temporary = output.with_name(f".{output.name}.part")
    try:
        with requests.get(UNSW_TEST_URL, stream=True, timeout=(10, 120)) as response:
            response.raise_for_status()
            with temporary.open("wb") as destination:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        destination.write(chunk)
        actual_checksum = sha256_file(temporary)
        if actual_checksum != UNSW_TEST_SHA256:
            raise ValueError(
                "Downloaded UNSW-NB15 file failed checksum verification: "
                f"expected {UNSW_TEST_SHA256}, received {actual_checksum}."
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download the UNSW-NB15 test partition.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "UNSW_NB15_testing-set.csv",
    )
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output = download_dataset(args.output, force=args.force)
    print(f"Verified UNSW-NB15 test set: {output.resolve()}")
    print(f"SHA256: {sha256_file(output)}")


if __name__ == "__main__":
    main()
