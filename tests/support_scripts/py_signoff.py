"""AST-based Python hygiene signoff with a ratcheting legacy baseline."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src" / "py" / "nxp_monkey"
BASELINE = Path(__file__).with_name("py_signoff_baseline.json")
BRANCH_NODES: tuple[type[ast.AST], ...] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.ExceptHandler,
    ast.IfExp,
    ast.Match,
)


@dataclass(frozen=True, slots=True)
class FunctionRecord:
    """A function-level AST metric record."""

    key: str
    line: int
    complexity: int
    line_count: int
    missing_annotations: tuple[str, ...]


def main() -> int:
    """Run the Python signoff checks."""
    baseline = _load_baseline()
    records, any_count = _scan_source()
    violations: list[str] = []
    violations.extend(_check_complexity(records, baseline))
    violations.extend(_check_function_lines(records, baseline))
    violations.extend(_check_annotations(records, baseline))
    violations.extend(_check_any_count(any_count, baseline))

    if violations:
        print("\n".join(violations))
        return 1
    print("Python signoff passed")
    return 0


def _load_baseline() -> dict[str, object]:
    return cast(dict[str, object], json.loads(BASELINE.read_text(encoding="utf-8")))


def _int_field(payload: dict[str, object], key: str) -> int:
    value = payload[key]
    if isinstance(value, int | float | str):
        return int(value)
    raise TypeError(f"baseline field {key!r} must be numeric")


def _scan_source() -> tuple[list[FunctionRecord], int]:
    records: list[FunctionRecord] = []
    any_count = 0
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if _is_any_reference(node):
                any_count += 1
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                records.append(_function_record(relative, node))
    return records, any_count


def _function_record(
    relative_path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> FunctionRecord:
    missing: list[str] = []
    for arg in _function_args(node):
        if arg.arg in {"self", "cls"}:
            continue
        if arg.annotation is None:
            missing.append(f"{relative_path}::{node.name}:{arg.arg}")
    if node.returns is None:
        missing.append(f"{relative_path}::{node.name}:return")
    return FunctionRecord(
        key=f"{relative_path}::{node.name}",
        line=node.lineno,
        complexity=_complexity(node),
        line_count=_line_count(node),
        missing_annotations=tuple(missing),
    )


def _function_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.arg, ...]:
    args = node.args
    return (
        *args.posonlyargs,
        *args.args,
        *args.kwonlyargs,
        *([args.vararg] if args.vararg is not None else []),
        *([args.kwarg] if args.kwarg is not None else []),
    )


def _check_complexity(
    records: list[FunctionRecord],
    baseline: dict[str, object],
) -> list[str]:
    max_new = _int_field(baseline, "max_new_code_complexity")
    accepted = cast(dict[str, int], baseline["complexity_offenders"])
    violations: list[str] = []
    for record in records:
        if record.complexity <= max_new:
            continue
        accepted_value = accepted.get(record.key)
        if accepted_value is None:
            violations.append(
                f"{record.key}:{record.line}: complexity {record.complexity} exceeds {max_new}"
            )
        elif record.complexity > accepted_value:
            violations.append(
                f"{record.key}:{record.line}: complexity grew "
                f"from {accepted_value} to {record.complexity}"
            )
    return violations


def _check_function_lines(
    records: list[FunctionRecord],
    baseline: dict[str, object],
) -> list[str]:
    max_lines = _int_field(baseline, "max_function_lines")
    accepted = cast(dict[str, int], baseline["function_line_offenders"])
    violations: list[str] = []
    for record in records:
        if record.line_count <= max_lines:
            continue
        accepted_value = accepted.get(record.key)
        if accepted_value is None:
            violations.append(
                f"{record.key}:{record.line}: function has {record.line_count} lines; "
                f"max is {max_lines}"
            )
        elif record.line_count > accepted_value:
            violations.append(
                f"{record.key}:{record.line}: function grew from "
                f"{accepted_value} to {record.line_count} lines"
            )
    return violations


def _check_annotations(
    records: list[FunctionRecord],
    baseline: dict[str, object],
) -> list[str]:
    accepted = set(cast(list[str], baseline["annotation_missing_offenders"]))
    current = {
        missing for record in records for missing in record.missing_annotations
    }
    new_missing = sorted(current - accepted)
    return [f"{item}: missing annotation" for item in new_missing]


def _check_any_count(any_count: int, baseline: dict[str, object]) -> list[str]:
    accepted = _int_field(baseline, "any_count_total")
    if any_count <= accepted:
        return []
    return [f"typing.Any count grew from {accepted} to {any_count}"]


def _is_any_reference(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "Any"
    if isinstance(node, ast.Attribute):
        return node.attr == "Any"
    return False


def _complexity(node: ast.AST) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(child, BRANCH_NODES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += max(0, len(child.values) - 1)
    return score


def _line_count(node: ast.AST) -> int:
    end_line = getattr(node, "end_lineno", None)
    start_line = getattr(node, "lineno", None)
    if isinstance(end_line, int) and isinstance(start_line, int):
        return end_line - start_line + 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
