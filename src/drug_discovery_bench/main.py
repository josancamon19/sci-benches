from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .adapter import (
    DEFAULT_HF_REVISION,
    DEFAULT_REVISION,
    RubricSource,
    download_source,
    import_tasks,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Scale AI's official DrugDiscoveryBench Harbor tasks."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("datasets/drug-discovery-bench"),
        help="Generated Harbor dataset directory (default: datasets/drug-discovery-bench).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Generate the first N tasks.")
    parser.add_argument(
        "--task-ids",
        nargs="+",
        default=None,
        help="Generate only the listed upstream task IDs.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace task directories that already exist.",
    )
    parser.add_argument(
        "--revision",
        default=DEFAULT_REVISION,
        help="Pinned upstream GitHub commit.",
    )
    parser.add_argument(
        "--hf-revision",
        default=DEFAULT_HF_REVISION,
        help="Pinned gated Hugging Face rubric dataset revision.",
    )
    parser.add_argument(
        "--source-cache-dir",
        type=Path,
        default=None,
        help="Optional cache directory for the pinned GitHub source.",
    )
    parser.add_argument(
        "--hf-cache-dir",
        type=Path,
        default=None,
        help="Optional Hugging Face cache directory.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Use a local DrugDiscoveryBench checkout instead of downloading GitHub source.",
    )
    parser.add_argument(
        "--rubrics-file",
        type=Path,
        default=None,
        help="Use a local official tasks.jsonl rubric bundle instead of Hugging Face.",
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
    source_dir = args.source_dir or download_source(
        revision=args.revision,
        cache_dir=args.source_cache_dir,
    )
    source_revision = "local" if args.source_dir is not None else args.revision
    rubric_source = RubricSource(
        revision=args.hf_revision,
        cache_dir=args.hf_cache_dir,
        source_file=args.rubrics_file,
        token=os.environ.get("HF_TOKEN"),
    )
    generated = import_tasks(
        source_dir=source_dir,
        rubric_source=rubric_source,
        output_dir=args.output_dir,
        source_revision=source_revision,
        limit=args.limit,
        overwrite=args.overwrite,
        task_ids=args.task_ids,
    )
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
