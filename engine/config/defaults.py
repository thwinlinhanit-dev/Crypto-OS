"""PD-11: parameters resolve only from the request or config/defaults.yaml.

Nothing here invents a value. A parameter that is neither documented in
``config/defaults.yaml`` nor present in the request raises :class:`ParamError`,
which the API edge maps to HTTP 422 ``VALIDATION_ERROR``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

DEFAULTS_PATH = Path("config") / "defaults.yaml"


class ParamErrorCode(StrEnum):
    """Why a parameter failed to resolve."""

    SECTION_NOT_FOUND = "SECTION_NOT_FOUND"
    UNDOCUMENTED_PARAM = "UNDOCUMENTED_PARAM"
    MISSING_PARAM = "MISSING_PARAM"
    PARAM_TYPE_MISMATCH = "PARAM_TYPE_MISMATCH"


class ParamError(Exception):
    """A parameter could not be resolved under PD-11."""

    def __init__(self, code: ParamErrorCode, param: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.param = param
        self.message = message

    @property
    def detail(self) -> dict[str, str]:
        return {"param": self.param, "reason": self.code.value}


def load_defaults(path: Path = DEFAULTS_PATH) -> dict[str, Any]:
    """Read ``config/defaults.yaml``. The file is the only source of defaults."""
    if not path.is_file():
        raise FileNotFoundError(f"config/defaults.yaml not found at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return raw


def _section(defaults: Mapping[str, Any], section: str) -> Mapping[str, Any]:
    node: Any = defaults
    for part in section.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise ParamError(
                ParamErrorCode.SECTION_NOT_FOUND,
                part,
                f"section '{section}' is not documented in config/defaults.yaml",
            )
        node = node[part]
    if not isinstance(node, Mapping):
        raise ParamError(
            ParamErrorCode.SECTION_NOT_FOUND,
            section,
            f"section '{section}' does not hold parameters",
        )
    return node


def _type_ok(documented: Any, provided: Any) -> bool:
    if isinstance(documented, bool):
        return isinstance(provided, bool)
    if isinstance(documented, int) and not isinstance(documented, bool):
        return isinstance(provided, int) and not isinstance(provided, bool)
    if isinstance(documented, float):
        return isinstance(provided, (int, float)) and not isinstance(provided, bool)
    if isinstance(documented, str):
        return isinstance(provided, str)
    if isinstance(documented, list):
        return isinstance(provided, list)
    if isinstance(documented, Mapping):
        return isinstance(provided, Mapping)
    return type(provided) is type(documented)


def resolve_params(
    defaults: Mapping[str, Any],
    section: str,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge request parameters over documented defaults.

    Raises :class:`ParamError` when a parameter is undocumented, missing a
    documented default, or type-incompatible with the documented value.
    The returned dict is what results store as ``resolved_params`` (PD-6).
    """
    documented = _section(defaults, section)
    supplied = dict(overrides or {})
    resolved: dict[str, Any] = {}

    for key in supplied:
        if key not in documented:
            raise ParamError(
                ParamErrorCode.UNDOCUMENTED_PARAM,
                key,
                f"parameter '{key}' is not documented in config/defaults.yaml[{section}]",
            )

    for key, default in documented.items():
        if key in supplied:
            value = supplied[key]
            if value is None:
                raise ParamError(
                    ParamErrorCode.MISSING_PARAM,
                    key,
                    f"parameter '{key}' was supplied without a value",
                )
            if not _type_ok(default, value):
                raise ParamError(
                    ParamErrorCode.PARAM_TYPE_MISMATCH,
                    key,
                    f"parameter '{key}' expects {type(default).__name__}, "
                    f"got {type(value).__name__}",
                )
            resolved[key] = value
        elif default is None:
            raise ParamError(
                ParamErrorCode.MISSING_PARAM,
                key,
                f"parameter '{key}' has no documented default and was not supplied",
            )
        else:
            resolved[key] = default

    return resolved
