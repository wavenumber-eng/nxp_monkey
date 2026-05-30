"""L99: every public function and class in nxp_monkey carries a docstring."""
from __future__ import annotations

import importlib
import inspect
import pkgutil

import nxp_monkey


def _walk_public_symbols():
    for module_info in pkgutil.iter_modules(nxp_monkey.__path__, "nxp_monkey."):
        name = module_info.name
        # Skip private modules and CLI orchestrator (it imports the world).
        leaf = name.rsplit(".", 1)[-1]
        if leaf.startswith("_"):
            continue
        module = importlib.import_module(name)
        for sym_name, sym in vars(module).items():
            if sym_name.startswith("_"):
                continue
            if getattr(sym, "__module__", None) != name:
                continue
            if inspect.isfunction(sym) or inspect.isclass(sym):
                yield name, sym_name, sym


def test_every_public_symbol_has_docstring():
    """Every public function and class defined in a public module has a docstring."""
    missing: list[str] = []
    for module_name, sym_name, sym in _walk_public_symbols():
        doc = inspect.getdoc(sym)
        if not doc or not doc.strip():
            missing.append(f"{module_name}.{sym_name}")
    assert not missing, "symbols without docstrings: " + ", ".join(missing)


def test_every_public_module_has_docstring():
    """Every public top-level module has a module-level docstring."""
    import pkgutil

    missing: list[str] = []
    for module_info in pkgutil.iter_modules(nxp_monkey.__path__, "nxp_monkey."):
        leaf = module_info.name.rsplit(".", 1)[-1]
        if leaf.startswith("_"):
            continue
        module = importlib.import_module(module_info.name)
        if not (module.__doc__ and module.__doc__.strip()):
            missing.append(module_info.name)
    assert not missing, "modules without docstrings: " + ", ".join(missing)
