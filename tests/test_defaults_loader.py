"""PD-11: the defaults loader accepts documented parameters and rejects everything else."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from engine.config.defaults import (
    DEFAULTS_PATH,
    ParamError,
    ParamErrorCode,
    load_defaults,
    resolve_params,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SECTION = "detectors.rvol_climax_v5"

# Spec section 6.3, in full. If this test fails, either the spec or the config moved.
SPEC_PARAMS: dict[str, Any] = {
    "vol_len": 20,
    "climax_mult": 3.0,
    "top_mult": 2.0,
    "range_len": 20,
    "trend_len": 5,
    "close_pos_lo": 0.30,
    "close_pos_hi": 0.70,
    "regime_len": 100,
    "bounce_len": 10,
    "bounce_atr": 1.5,
    "wick_min": 0.40,
    "cooldown": 5,
    "atr_len": 14,
    "fail_mode": "failed_bounce",
    "lookback_bars": 100,
}


def test_config_file_is_where_the_spec_says() -> None:
    assert Path("config") / "defaults.yaml" == DEFAULTS_PATH
    assert (REPO_ROOT / "config" / "defaults.yaml").is_file()


def test_every_spec_parameter_is_documented() -> None:
    defaults = load_defaults(REPO_ROOT / "config" / "defaults.yaml")
    resolved = resolve_params(defaults, SECTION)
    assert resolved == SPEC_PARAMS


def test_defaults_are_read_from_the_repo_root_path() -> None:
    # The API resolves against the working directory; tests run from the repo root.
    assert load_defaults() == load_defaults(REPO_ROOT / "config" / "defaults.yaml")


def test_documented_overrides_win() -> None:
    defaults = load_defaults(REPO_ROOT / "config" / "defaults.yaml")
    resolved = resolve_params(defaults, SECTION, {"cooldown": 7, "fail_mode": "legacy_v1"})
    assert resolved["cooldown"] == 7
    assert resolved["fail_mode"] == "legacy_v1"
    assert resolved["vol_len"] == 20


def test_undocumented_parameter_is_rejected() -> None:
    defaults = load_defaults(REPO_ROOT / "config" / "defaults.yaml")
    with pytest.raises(ParamError) as exc:
        resolve_params(defaults, SECTION, {"climax_magic": 1})
    assert exc.value.code is ParamErrorCode.UNDOCUMENTED_PARAM
    assert exc.value.detail["param"] == "climax_magic"


def test_missing_parameter_without_default_is_rejected() -> None:
    defaults = {"section": {"documented": 1, "required": None}}
    with pytest.raises(ParamError) as exc:
        resolve_params(defaults, "section")
    assert exc.value.code is ParamErrorCode.MISSING_PARAM


def test_type_mismatch_is_rejected() -> None:
    defaults = {"section": {"cooldown": 5}}
    with pytest.raises(ParamError) as exc:
        resolve_params(defaults, "section", {"cooldown": "five"})
    assert exc.value.code is ParamErrorCode.PARAM_TYPE_MISMATCH


def test_int_is_accepted_where_a_float_is_documented() -> None:
    defaults = {"section": {"climax_mult": 3.0}}
    assert resolve_params(defaults, "section", {"climax_mult": 4})["climax_mult"] == 4


def test_unknown_section_is_rejected() -> None:
    defaults = load_defaults(REPO_ROOT / "config" / "defaults.yaml")
    with pytest.raises(ParamError) as exc:
        resolve_params(defaults, "detectors.not_a_detector")
    assert exc.value.code is ParamErrorCode.SECTION_NOT_FOUND


def test_null_override_is_rejected_rather_than_defaulting() -> None:
    defaults = {"section": {"cooldown": 5}}
    with pytest.raises(ParamError) as exc:
        resolve_params(defaults, "section", {"cooldown": None})
    assert exc.value.code is ParamErrorCode.MISSING_PARAM
