from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .adapter import DEFAULT_REVISION, EpiBenchAdapter, LatchDataSource, download_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the seven public LatchBio EpiBench examples into Harbor format."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("datasets/epibench"),
        help="Generated Harbor dataset directory (default: datasets/epibench).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Generate the first N tasks.")
    parser.add_argument(
        "--task-ids",
        nargs="+",
        default=None,
        help="Generate only the listed public EpiBench eval IDs.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace task directories that already exist.",
    )
    parser.add_argument(
        "--revision",
        default=DEFAULT_REVISION,
        help="Pinned upstream Git commit.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Use a local EpiBench checkout instead of downloading eval definitions.",
    )
    parser.add_argument(
        "--source-cache-dir",
        type=Path,
        default=None,
        help="Optional cache directory for the pinned GitHub source archive.",
    )
    parser.add_argument(
        "--data-cache-dir",
        type=Path,
        default=None,
        help="Optional cache directory for authenticated Latch data downloads.",
    )
    parser.add_argument(
        "--latch-cli",
        default=None,
        help="Optional Latch CLI executable; otherwise use pinned latch 2.76.10 through uvx.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Load LATCH_BIO_API_KEY from this dotenv file (default: .env).",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_dotenv(args.env_file, override=False)
    source_dir = args.source_dir or download_source(
        revision=args.revision,
        cache_dir=args.source_cache_dir,
    )
    source_revision = "local" if args.source_dir is not None else args.revision
    data_source = LatchDataSource(
        token=os.environ.get("LATCH_BIO_API_KEY"),
        cache_dir=args.data_cache_dir,
        latch_cli=args.latch_cli,
    )
    generated = EpiBenchAdapter(
        source_dir=source_dir,
        data_source=data_source,
        output_dir=args.output_dir,
        source_revision=source_revision,
        limit=args.limit,
        overwrite=args.overwrite,
        task_ids=args.task_ids,
    ).run()
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
