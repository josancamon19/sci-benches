from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .adapter import DEFAULT_REVISION, CompBioBenchAdapter, DatasetSource


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Genentech CompBioBench v1 into Harbor task format."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("datasets/compbiobench"),
        help="Generated Harbor dataset directory (default: datasets/compbiobench).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Generate the first N tasks.")
    parser.add_argument(
        "--task-ids",
        nargs="+",
        default=None,
        help="Generate only the listed upstream question IDs.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace task directories that already exist.",
    )
    parser.add_argument(
        "--revision",
        default=DEFAULT_REVISION,
        help="Pinned Hugging Face dataset revision.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional Hugging Face cache directory.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Use a local CompBioBench dataset checkout instead of Hugging Face.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Load optional HF_TOKEN from this dotenv file (default: .env).",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_dotenv(args.env_file, override=False)
    source = DatasetSource(
        revision=args.revision,
        cache_dir=args.cache_dir,
        source_dir=args.source_dir,
        token=os.environ.get("HF_TOKEN"),
    )
    generated = CompBioBenchAdapter(
        source=source,
        output_dir=args.output_dir,
        limit=args.limit,
        overwrite=args.overwrite,
        task_ids=args.task_ids,
    ).run()
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
