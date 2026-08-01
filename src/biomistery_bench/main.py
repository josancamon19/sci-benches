from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .adapter import (
    DEFAULT_DATASET,
    DEFAULT_MAX_ARCHIVE_BYTES,
    RELEASES,
    BioMysteryBenchAdapter,
    DatasetSource,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Anthropic BioMysteryBench into Harbor task format."
    )
    parser.add_argument(
        "--dataset",
        choices=sorted(RELEASES),
        default=DEFAULT_DATASET,
        help="Export the gated 90-task full release (default) or five-task preview.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Generated Harbor dataset directory (default depends on --dataset).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Generate the first N tasks.")
    parser.add_argument(
        "--task-ids",
        nargs="+",
        default=None,
        help="Generate only the listed BioMysteryBench problem IDs.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace task directories that already exist.",
    )
    parser.add_argument(
        "--revision",
        default=None,
        help="Override the pinned Hugging Face commit for the chosen release.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional Hugging Face cache directory.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Load HF_TOKEN from this dotenv file (default: .env).",
    )
    parser.add_argument(
        "--max-archive-size-gb",
        type=float,
        default=DEFAULT_MAX_ARCHIVE_BYTES / 1_000_000_000,
        help="Skip task archives larger than this decimal-GB size (default: 1).",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Use a local dataset checkout instead of Hugging Face.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.max_archive_size_gb <= 0:
        parser.error("--max-archive-size-gb must be greater than zero")
    load_dotenv(args.env_file, override=False)
    token = os.environ.get("HF_TOKEN")
    if args.source_dir is None and not token:
        parser.error(f"HF_TOKEN is required for the gated dataset; add it to {args.env_file}")
    release = RELEASES[args.dataset]
    source = DatasetSource(
        release,
        revision=args.revision,
        cache_dir=args.cache_dir,
        source_dir=args.source_dir,
        token=token,
    )
    output_dir = args.output_dir or Path(f"datasets/biomystery-bench-{args.dataset}")
    generated = BioMysteryBenchAdapter(
        source=source,
        output_dir=output_dir,
        limit=args.limit,
        overwrite=args.overwrite,
        task_ids=args.task_ids,
        max_archive_bytes=int(args.max_archive_size_gb * 1_000_000_000),
    ).run()
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
