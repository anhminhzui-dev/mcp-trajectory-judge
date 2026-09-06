"""Command line. Exit 0 = GO, 2 = HOLD, 1 = bad usage or a crash.

A crash is not a pass: an unhandled exception still prints a HOLD line before it leaves.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .judge import (
    DEFAULT_CHECKS,
    BatchResult,
    judge_batch,
    merge_judgments,
    render,
    render_ranking,
    summary_document,
    trace_row,
)
from .model import HaltError, load_task

EXIT_GO = 0
EXIT_USAGE = 1
EXIT_HOLD = 2


def write_receipt(out_dir: str, result: BatchResult) -> None:
    """summary.json plus trace.jsonl. Both are content-only: no timestamp, no host, no path of
    the machine that ran them, so two runs over the same inputs are byte-identical."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = summary_document(result, __version__)
    with (out / "summary.json").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    with (out / "trace.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for judgment in result.judgments:
            handle.write(json.dumps(trace_row(judgment), sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-trajectory-judge",
        description="Replay MCP tool-call trajectories against a deterministic sandbox and judge "
        "each one PASS, FAIL with named codes, or ABSTAIN.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    verbs = parser.add_subparsers(dest="verb", required=True)
    judge_verb = verbs.add_parser("judge", help="judge one trajectory file against one task")
    judge_verb.add_argument("--task", required=True)
    judge_verb.add_argument("--trajectories", required=True)
    judge_verb.add_argument("--out", default=None, help="directory for summary.json + trace.jsonl")
    compare_verb = verbs.add_parser("compare", help="rank two trajectory files on the same task")
    compare_verb.add_argument("--task", required=True)
    compare_verb.add_argument("--a", required=True)
    compare_verb.add_argument("--b", required=True)
    for verb in (judge_verb, compare_verb):
        verb.add_argument(
            "--allow-nonsynthetic",
            action="store_true",
            help="permit rows without the synthetic marker (off by default, on purpose)",
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_GO if exc.code in (0, None) else EXIT_USAGE
    try:
        task = load_task(args.task, args.allow_nonsynthetic)
        if args.verb == "judge":
            result = judge_batch(
                task, args.trajectories, DEFAULT_CHECKS, args.allow_nonsynthetic
            )
            print(render(result))
            if args.out:
                write_receipt(args.out, result)
            return EXIT_GO if result.verdict == "GO" else EXIT_HOLD
        left = judge_batch(task, args.a, DEFAULT_CHECKS, args.allow_nonsynthetic)
        right = judge_batch(task, args.b, DEFAULT_CHECKS, args.allow_nonsynthetic)
        judgments = merge_judgments(left, right)
        print(render_ranking(judgments))
        return EXIT_GO if all(j.verdict == "PASS" for j in judgments) else EXIT_HOLD
    except HaltError as halt:
        print(f"HALT: {halt}")
        print(f"VERDICT: HOLD (halt: {halt.code})")
        return EXIT_HOLD
    except Exception as exc:  # a crash must refuse out loud, never pass quietly
        print(f"VERDICT: HOLD (crash: {type(exc).__name__})")
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
