"""JSON serialization helpers for nxp_monkey dataclasses.

The library returns plain ``@dataclass`` values that are easy to serialize
with :func:`dataclasses.asdict`, except they may contain
:class:`pathlib.Path` instances (which ``json`` cannot encode natively) and
tuples (which round-trip as lists). :func:`to_jsonable` is a tiny recursive
converter that handles those cases without pulling in a heavier
dependency. :func:`write_json` writes the result to disk with stable,
pretty formatting.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Recursively convert ``value`` to a JSON-encodable structure.

    Dataclasses become dicts; tuples become lists; :class:`pathlib.Path`
    values become their ``str`` form. Anything else is passed through.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_jsonable(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, payload: Any) -> Path:
    """Write ``payload`` as pretty JSON to ``path`` and return ``path``.

    ``payload`` is run through :func:`to_jsonable` first. Parent directories
    are created if missing. Existing files are overwritten.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_jsonable(payload), indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return path
