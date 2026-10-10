"""Operator-installed, digest-pinned calibration; no fitting or extrapolation."""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    POLYNOMIAL_VERSION,
    SCALE_ID,
    VQI_PREPROCESSING_VERSION,
    WINDOW_POLICY_VERSION,
)
from .errors import AudioDeterministicError


def normalize_mos(mos):
    return None if mos is None else 5 * (mos - 1) / 4


@dataclass(frozen=True)
class Calibration:
    calibration_id: str
    model_sha256: str
    detector_id: str
    cohorts: frozenset[str]
    x_min: float
    x_max: float
    interpolator: Any
    scale_id: str = SCALE_ID

    def apply(self, x):
        if x is None:
            return None
        if not math.isfinite(x):
            raise AudioDeterministicError("nonfinite_output")
        if not self.x_min <= x <= self.x_max:
            raise AudioDeterministicError("out_of_domain")
        mos = float(self.interpolator(x))
        if not math.isfinite(mos) or not 1 <= mos <= 5:
            raise AudioDeterministicError("nonfinite_output")
        return normalize_mos(mos)


def _number(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError("nonfinite calibration")
    return value


def load_calibration(path, sha256, *, model_sha256, detector_id) -> Calibration:
    """Validate the exact pipeline, monotone mapping and declared held-out gates."""
    from scipy.interpolate import PchipInterpolator

    try:
        if not path or not sha256:
            raise ValueError("missing calibration")
        data = Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("digest mismatch")
        raw = json.loads(data)
        versions = {
            "model_sha256": model_sha256,
            "polynomial_version": POLYNOMIAL_VERSION,
            "preprocessing_version": VQI_PREPROCESSING_VERSION,
            "detector_id": detector_id,
            "window_policy_version": WINDOW_POLICY_VERSION,
            "scale_id": SCALE_ID,
        }
        if any(raw.get(key) != expected for key, expected in versions.items()):
            raise ValueError("version mismatch")
        if not isinstance(raw["calibration_id"], str) or not raw["calibration_id"]:
            raise ValueError("missing calibration identity")
        manifest_digest = raw["dataset_manifest_sha256"]
        if len(manifest_digest) != 64 or any(
            c not in "0123456789abcdef" for c in manifest_digest
        ):
            raise ValueError("invalid dataset identity")
        g = raw["g"]
        x, y = [_number(v) for v in g["knots_x"]], [_number(v) for v in g["knots_y"]]
        if g["type"] != "monotone_pchip" or len(x) < 2 or len(x) != len(y):
            raise ValueError("invalid mapping")
        if any(b <= a for a, b in zip(x, x[1:], strict=False)) or any(
            b < a for a, b in zip(y, y[1:], strict=False)
        ):
            raise ValueError("nonmonotone mapping")
        lo, hi = _number(raw["support"]["x_min"]), _number(raw["support"]["x_max"])
        if not x[0] <= lo < hi <= x[-1] or not 1 <= min(y) <= max(y) <= 5:
            raise ValueError("invalid support")
        gates = raw["gates"]
        if not (
            0 <= _number(gates["rmse"]) <= 0.5
            and 0.7 <= _number(gates["spearman"]) <= 1
            and 0.6 <= _number(gates["spearman_lb95"]) <= 1
            and 0.9 <= _number(gates["pair_rank_acc"]) <= 1
        ):
            raise ValueError("validation gates failed")
        cohorts = set()
        for cohort in raw["cohorts"]:
            if (
                not isinstance(cohort["tag"], str)
                or not cohort["tag"]
                or _number(cohort["n_heldout"]) < 50
                or abs(_number(cohort["bias"])) > 0.25
                or not 0 <= _number(cohort["rmse"]) <= 0.65
            ):
                raise ValueError("cohort gates failed")
            cohorts.add(cohort["tag"])
        if not cohorts:
            raise ValueError("no admitted cohorts")
        return Calibration(
            raw["calibration_id"],
            model_sha256,
            detector_id,
            frozenset(cohorts),
            lo,
            hi,
            PchipInterpolator(x, y, extrapolate=False),
        )
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise AudioDeterministicError("not_validated") from None
