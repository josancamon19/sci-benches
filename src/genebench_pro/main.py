from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .adapter import DEFAULT_REVISION, GeneBenchProAdapter, download_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export GeneBench-Pro from Hugging Face into Harbor task format."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("datasets/genebench-pro"),
        help="Directory that will contain the generated Harbor tasks.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Generate the first N tasks.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace task directories that already exist.",
    )
    parser.add_argument(
        "--task-ids",
        nargs="+",
        default=None,
        help="Generate only the listed upstream eval IDs.",
    )
    parser.add_argument(
        "--revision",
        default=DEFAULT_REVISION,
        help="Hugging Face dataset revision or commit to download.",
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
        help="Use an existing public-package checkout instead of downloading it.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    source_dir = args.source_dir or download_source(
        revision=args.revision,
        cache_dir=args.cache_dir,
    )
    revision = "local" if args.source_dir is not None else args.revision
    adapter = GeneBenchProAdapter(
        source_dir=source_dir,
        output_dir=args.output_dir,
        source_revision=revision,
        limit=args.limit,
        overwrite=args.overwrite,
        task_ids=args.task_ids,
    )
    generated = adapter.run()
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
