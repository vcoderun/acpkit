from __future__ import annotations as _annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

MIN_LINE_COVERAGE: Final[float] = 97.0
MIN_BRANCH_COVERAGE: Final[float] = 95.0


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverageSummary:
    line_coverage: float
    covered_lines: int
    num_statements: int
    branch_coverage: float
    covered_branches: int
    num_branches: int


def _require_json_object(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{context} must be a JSON object"
        raise TypeError(msg)

    json_object: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            msg = f"{context} keys must be strings"
            raise TypeError(msg)
        json_object[key] = item
    return json_object


def _require_number(mapping: Mapping[str, object], key: str) -> int | float:
    value = mapping.get(key)
    if not isinstance(value, int | float):
        msg = f"coverage.json field {key!r} must be numeric"
        raise TypeError(msg)
    return value


def _parse_summary(coverage_data: dict[str, object]) -> CoverageSummary:
    totals = _require_json_object(coverage_data.get("totals"), context="coverage.json totals")
    covered_lines = int(_require_number(totals, "covered_lines"))
    num_statements = int(_require_number(totals, "num_statements"))
    line_coverage = float(_require_number(totals, "percent_covered"))
    covered_branches = int(_require_number(totals, "covered_branches"))
    num_branches = int(_require_number(totals, "num_branches"))
    missing_branches = int(_require_number(totals, "missing_branches"))

    if num_branches != covered_branches + missing_branches:
        msg = "coverage.json branch totals are inconsistent"
        raise ValueError(msg)

    if num_branches < 0 or covered_branches < 0:
        msg = "coverage.json branch totals must be non-negative"
        raise ValueError(msg)

    branch_coverage = (covered_branches / num_branches * 100.0) if num_branches else 100.0
    return CoverageSummary(
        line_coverage=line_coverage,
        covered_lines=covered_lines,
        num_statements=num_statements,
        branch_coverage=branch_coverage,
        covered_branches=covered_branches,
        num_branches=num_branches,
    )


def _format_summary(summary: CoverageSummary) -> str:
    return (
        f"Line coverage: {summary.line_coverage:.2f}% ({summary.covered_lines} / {summary.num_statements})\n"
        f"Branch coverage: {summary.branch_coverage:.2f}% ({summary.covered_branches} / {summary.num_branches})\n"
    )


def _validate_thresholds(summary: CoverageSummary) -> None:
    failures: list[str] = []
    if summary.line_coverage < MIN_LINE_COVERAGE:
        failures.append(
            f"line coverage {summary.line_coverage:.2f}% is below required {MIN_LINE_COVERAGE:.2f}%"
        )
    if summary.branch_coverage < MIN_BRANCH_COVERAGE:
        failures.append(
            f"branch coverage {summary.branch_coverage:.2f}% is below required {MIN_BRANCH_COVERAGE:.2f}%"
        )
    if failures:
        raise SystemExit("Coverage thresholds failed:\n- " + "\n- ".join(failures))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate ACP Kit coverage thresholds and optionally write COVERAGE summary."
    )
    parser.add_argument(
        "--input",
        dest="input_path",
        default="coverage.json",
        help="Path to the coverage JSON report.",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        default="COVERAGE",
        help="Path to the human-readable coverage summary file.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate thresholds without writing the summary file.",
    )
    return parser.parse_args()


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    args = _parse_args()
    coverage_json_path = (repo_root / args.input_path).resolve()
    coverage_summary_path = (repo_root / args.output_path).resolve()

    coverage_data = _require_json_object(
        json.loads(coverage_json_path.read_text(encoding="utf-8")),
        context="coverage.json",
    )
    summary = _parse_summary(coverage_data)
    if not args.check_only:
        coverage_summary_path.write_text(_format_summary(summary), encoding="utf-8")
    _validate_thresholds(summary)


if __name__ == "__main__":
    main()
