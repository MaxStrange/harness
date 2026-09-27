"""A minimal JSON-schema argument check: enough to give the model a precise error."""

from __future__ import annotations

from typing import Any

_TYPES = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    """Return a list of problems (empty when the arguments fit the schema)."""
    problems: list[str] = []
    if not isinstance(args, dict):
        return ["arguments must be a JSON object"]
    properties = schema.get("properties", {})
    for required in schema.get("required", []):
        if required not in args:
            problems.append(f"missing required argument {required!r}")
    for key, value in args.items():
        spec = properties.get(key)
        if spec is None:
            if schema.get("additionalProperties") is False:
                problems.append(f"unknown argument {key!r}")
            continue
        expected = spec.get("type")
        if expected is None or value is None and "null" in _as_list(expected):
            continue
        allowed = tuple(t for name in _as_list(expected) for t in _TYPES.get(name, ()))
        if allowed and not isinstance(value, allowed):
            problems.append(f"argument {key!r} should be {expected}, got {type(value).__name__}")
            continue
        if (
            isinstance(value, bool)
            and "integer" in _as_list(expected)
            and "boolean" not in _as_list(expected)
        ):
            problems.append(f"argument {key!r} should be an integer, got boolean")
        if "enum" in spec and value not in spec["enum"]:
            problems.append(f"argument {key!r} must be one of {spec['enum']}, got {value!r}")
    return problems


def _as_list(value: Any) -> list[str]:
    return value if isinstance(value, list) else [value]
