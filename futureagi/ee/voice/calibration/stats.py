"""Pure numeric helpers for the VQI calibration study (numpy only, deterministic)."""

import math

import numpy as np


def pava(x, y, weights=None):
    """Non-decreasing isotonic regression of y on x (pool adjacent violators).

    Returns ``(ux, fitted)``: the sorted unique x values and the fitted,
    non-decreasing value at each (ties in x are pooled to their weighted mean).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    w = np.ones_like(y) if weights is None else np.asarray(weights, dtype=float)
    order = np.argsort(x, kind="mergesort")
    xs, ys, ws = x[order], y[order], w[order]
    # Pool exact ties in x so the fit is a function of x.
    ux, inverse = np.unique(xs, return_inverse=True)
    sw = np.bincount(inverse, weights=ws)
    sy = np.bincount(inverse, weights=ws * ys) / sw
    blocks = []  # [value, weight, count]
    for value, weight in zip(sy, sw, strict=True):
        blocks.append([value, weight, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    fitted_unique = np.concatenate([[v] * c for v, _, c in blocks])
    return ux, fitted_unique


def monotone_knots(x, y, n_knots):
    """Knots for a monotone PCHIP: x at calibration quantiles, y from the isotonic fit.

    The first/last knots sit at the calibration minimum/maximum, so the
    supported interval is exactly the observed calibration range. Knot y is
    clipped to the MOS anchors [1, 5] and made non-decreasing; knot x is made
    strictly increasing (duplicates dropped).
    """
    ux, fitted = pava(x, y)
    xs = np.sort(np.asarray(x, dtype=float))
    qs = np.linspace(0.0, 1.0, n_knots)
    kx = np.quantile(xs, qs, method="linear")
    kx = np.unique(kx)
    # Isotonic fit evaluated by linear interpolation between pooled points.
    ky = np.interp(kx, ux, fitted)
    ky = np.clip(np.maximum.accumulate(ky), 1.0, 5.0)
    return [float(v) for v in kx], [float(v) for v in ky]


def pchip(knots_x, knots_y):
    from scipy.interpolate import PchipInterpolator

    return PchipInterpolator(knots_x, knots_y, extrapolate=False)


def rankdata(a):
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    # average ties
    sorted_a = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return ranks


def pearson(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def spearman(a, b):
    return pearson(rankdata(a), rankdata(b))


def group_bootstrap_spearman_lb(pred, mos, groups, *, n_boot=2000, seed=2094):
    """2.5th percentile of Spearman over bootstrap resamples of whole groups."""
    rng = np.random.default_rng(seed)
    pred = np.asarray(pred, dtype=float)
    mos = np.asarray(mos, dtype=float)
    groups = np.asarray(groups)
    unique = np.unique(groups)
    if not len(unique):
        return float("nan"), 0
    index = {g: np.flatnonzero(groups == g) for g in unique}
    values = []
    for _ in range(n_boot):
        pick = rng.choice(unique, size=len(unique), replace=True)
        idx = np.concatenate([index[g] for g in pick])
        r = spearman(pred[idx], mos[idx])
        if math.isfinite(r):
            values.append(r)
    if not values:
        return float("nan"), 0
    return float(np.percentile(values, 2.5)), len(values)


def material_pairs(mos, mos_ci95, *, min_delta=1.0):
    """Preregistered substitute for listener-confirmed paired degradations.

    All clip pairs whose human MOS differ by >= ``min_delta`` and whose
    per-clip 95% confidence intervals do not overlap.
    """
    mos = np.asarray(mos, dtype=float)
    ci = np.asarray(mos_ci95, dtype=float)
    pairs = []
    for i in range(len(mos)):
        for j in range(i + 1, len(mos)):
            if abs(mos[i] - mos[j]) < min_delta:
                continue
            lo_hi = (mos[i] - ci[i], mos[i] + ci[i]), (mos[j] - ci[j], mos[j] + ci[j])
            if lo_hi[0][1] < lo_hi[1][0] or lo_hi[1][1] < lo_hi[0][0]:
                pairs.append((i, j))
    return pairs


def pair_concordance(pred, mos, pairs):
    """Fraction of pairs ordered like the human MOS; prediction ties count as wrong."""
    if not pairs:
        return float("nan")
    correct = 0
    for i, j in pairs:
        if (pred[i] - pred[j]) * (mos[i] - mos[j]) > 0:
            correct += 1
    return correct / len(pairs)


def error_summary(pred, mos):
    pred = np.asarray(pred, dtype=float)
    mos = np.asarray(mos, dtype=float)
    err = pred - mos
    return {
        "n": int(len(err)),
        "rmse_mos": float(np.sqrt(np.mean(err**2))) if len(err) else float("nan"),
        "mae_mos": float(np.mean(np.abs(err))) if len(err) else float("nan"),
        "bias_mos": float(np.mean(err)) if len(err) else float("nan"),
        "abs_error_quantiles_mos": {
            q: float(np.quantile(np.abs(err), float(q))) if len(err) else None
            for q in ("0.5", "0.9", "0.95")
        },
        # The product exposes VQI = 5 * (MOS - 1) / 4, so index errors are 1.25x.
        "rmse_vqi_index": float(1.25 * np.sqrt(np.mean(err**2)))
        if len(err)
        else float("nan"),
        "pearson": pearson(pred, mos),
        "spearman": spearman(pred, mos),
    }
