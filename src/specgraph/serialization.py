"""Canonical scalar-aware JSON without loading parsers or project configuration."""
from __future__ import annotations
from datetime import date, datetime
import json
from typing import Any

def json_default(value: Any) -> str:
    """Encode the small set of rich scalar types admitted by YAML inputs.

    PyYAML decodes unquoted ISO dates as ``datetime.date``.  Keeping the
    conversion explicit avoids both a platform traceback and the overly broad
    ``default=str`` behaviour, which could silently stringify an unsupported
    graph object.
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

def canonical_json(v: Any) -> str:
    """Return deterministically sorted compact JSON string."""
    def stable(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: stable(x[k]) for k in sorted(x)}
        if isinstance(x, (list, tuple)):
            return [stable(i) for i in x]
        return x
    return json.dumps(
        stable(v),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )

