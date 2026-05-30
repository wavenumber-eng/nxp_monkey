"""L99: CLI command modules are thin wrappers.

The CLI layer must not contain business logic. Each
``nxp_monkey_cmd_<name>.py`` module is allowed to import from the standard
library, the ``rich`` / ``rich_argparse`` presentation libraries, and the
local package's library modules. Any other import indicates business logic
has leaked into the CLI layer.
"""
from __future__ import annotations

import ast
import pathlib

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "py" / "nxp_monkey"

ALLOWED_TOP_LEVEL = {
    "argparse",
    "json",
    "re",
    "sys",
    "os",
    "pathlib",
    "rich",
    "rich_argparse",
    "nxp_monkey",
    "__future__",
}


def _command_modules() -> list[pathlib.Path]:
    return sorted(PACKAGE_ROOT.glob("nxp_monkey_cmd_*.py"))


def _collect_top_level_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                found.add("nxp_monkey")  # treat relative imports as local
            elif node.module:
                found.add(node.module.split(".", 1)[0])
    return found


def test_command_modules_import_only_allowed_top_level():
    """Each command module imports only allowed top-level packages."""
    violations: list[str] = []
    for module_path in _command_modules():
        imports = _collect_top_level_imports(module_path)
        forbidden = imports - ALLOWED_TOP_LEVEL
        if forbidden:
            violations.append(f"{module_path.name}: {sorted(forbidden)}")
    assert not violations, (
        "CLI command modules pulled in non-allowed top-level imports:\n  "
        + "\n  ".join(violations)
    )


def test_command_modules_have_register_and_run():
    """Each command module exposes a ``register`` callable."""
    import importlib

    for module_path in _command_modules():
        mod_name = f"nxp_monkey.{module_path.stem}"
        module = importlib.import_module(mod_name)
        assert hasattr(module, "register"), f"{mod_name}.register missing"
