"""TH-2094 VQI calibration study against public, rights-cleared human MOS labels.

Two stages, run in an authorized cloud sandbox (never in request/worker code):

  stage1  Download only the cleared NISQA subsets, decode and qualify every clip
          through the product decode + speech-detector + 9.01 s window path,
          apply the preregistered rating/eligibility rules and freeze the
          speaker-grouped splits. No DNSMOS output exists at this point.
  stage2  Verify the frozen manifest, pipeline files, clip bytes and decoded
          samples, compute the calibration input x with the product
          ``mean_adjusted_ovrl``, fit a monotone PCHIP on the calibration split,
          choose its knot count on the selection split, evaluate the locked
          test split once, and write a report. Passing statistics produce only
          a research candidate; public data cannot satisfy release qualifications.

See ``PREREGISTRATION.md`` next to this file for the protocol. Usage:

  python -m ee.voice.calibration.vqi_study stage1 --out DIR --created-at ISO
  python -m ee.voice.calibration.vqi_study stage2 --manifest DIR/manifest.json \
      --manifest-sha256 HEX --model PATH --model-sha256 HEX --out DIR
"""

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import struct
import sys
import tempfile
import time
import zlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

NISQA_RECORD = "https://zenodo.org/records/4728081"
NISQA_URL = "https://zenodo.org/records/4728081/files/NISQA_Corpus.zip?download=1"
NISQA_ZIP_BYTES = 15945499507
# Only subsets whose own readme grants commercial use (LIVE) or CC BY 4.0 (LIVETALK).
SUBSETS = {
    "NISQA_TRAIN_LIVE": "en",
    "NISQA_VAL_LIVE": "en",
    "NISQA_TEST_LIVETALK": "de",
}
LICENSE_FILES = [
    "NISQA_Corpus/NISQA_TRAIN_LIVE/readme.txt",
    "NISQA_Corpus/NISQA_VAL_LIVE/readme.txt",
    "NISQA_Corpus/NISQA_TEST_LIVETALK/readme.txt",
    "NISQA_Corpus/NISQA_TEST_LIVETALK/CC_BY_license.txt",
    "NISQA_Corpus/readme.txt",
]
# Product cohort tag for speech received after a real telephone/VoIP transmission
# and recorded at the receiving end (see PREREGISTRATION.md, "Cohort mapping").
TRANSPORT = "sip"
CAPTURE_ORIGIN = "provider_received_track"
SEED = 2094
TEST_TRAIN_READER_FRACTION = 0.35
CALIBRATION_SHARE_OF_REST = 2 / 3
MIN_VOTES = 5
KNOT_CANDIDATES = (4, 6, 8, 10, 12)
PAIR_MIN_DELTA_MOS = 1.0
GATES = {
    "rmse_max": 0.5,
    "spearman_min": 0.70,
    "spearman_lb95_min": 0.60,
    "pair_rank_acc_min": 0.90,
    "cohort_n_heldout_min": 50,
    "cohort_abs_bias_max": 0.25,
    "cohort_rmse_max": 0.65,
}
PIPELINE_FILES = (
    "ee/voice/services/audio_analysis/vqi.py",
    "ee/voice/services/audio_analysis/vad.py",
    "ee/voice/services/audio_analysis/calibration.py",
    "ee/voice/services/audio_analysis/constants.py",
    "ee/voice/calibration/stats.py",
    "ee/voice/calibration/vqi_study.py",
)
PACKAGES = (
    "numpy",
    "scipy",
    "librosa",
    "soxr",
    "soundfile",
    "onnxruntime",
    "torch",
    "silero-vad",
)
ROOT = Path(__file__).resolve().parents[3]  # futureagi/


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _pkg(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def pipeline_fingerprint():
    """Source digests of every function/class on the x and eligibility path.

    Function-level (not file-level) so unrelated edits elsewhere in the same
    modules do not invalidate a frozen stage-1 manifest; decode changes are
    additionally caught by the per-clip decoded-sample digests.
    """
    import inspect

    from ee.voice.services.audio_analysis import decode, pitch_snr, vad, vqi

    from . import stats

    objects = {
        "vqi.resample_for_vqi": vqi.resample_for_vqi,
        "vqi._intersections": vqi._intersections,
        "vqi.prepare_vqi_windows": vqi.prepare_vqi_windows,
        "vqi.mean_adjusted_ovrl": vqi.mean_adjusted_ovrl,
        "vqi.analyze_vqi": vqi.analyze_vqi,
        "pitch_snr.interval_union": pitch_snr.interval_union,
        "pitch_snr.interval_seconds": pitch_snr.interval_seconds,
        "vad.SileroVad": vad.SileroVad,
        "decode.decode_audio": decode.decode_audio,
        "stats": stats,
        "study.assign_splits": assign_splits,
        "study._qualify": _qualify,
        "study._fit_and_select": _fit_and_select,
        "study._evaluate": _evaluate,
        "study._gates": _gates,
    }
    return {
        name: _sha256(inspect.getsource(obj).encode()) for name, obj in objects.items()
    }


def _pipeline_constants(detector_id):
    from ee.voice.services.audio_analysis import constants as c

    return {
        "polynomial_version": c.POLYNOMIAL_VERSION,
        "preprocessing_version": c.VQI_PREPROCESSING_VERSION,
        "window_policy_version": c.WINDOW_POLICY_VERSION,
        "scale_id": c.SCALE_ID,
        "vqi_algorithm_version": c.VQI_ALGORITHM_VERSION,
        "detector_id": detector_id,
    }


# --------------------------------------------------------------------------- zip I/O


class ZipRanges:
    """Read selected members of the remote corpus zip with HTTP range requests."""

    def __init__(self, url=NISQA_URL, cache=None):
        import remotezip  # study-only tooling, installed in the sandbox

        self.url = url
        self.zip = remotezip.RemoteZip(url)
        self.infos = {i.filename: i for i in self.zip.infolist()}
        self.cache = Path(cache) if cache else None

    def read_small(self, name):
        return self.zip.read(name)

    def members(self, subset):
        prefix = f"NISQA_Corpus/{subset}/deg/"
        return sorted(
            (
                i
                for n, i in self.infos.items()
                if n.startswith(prefix) and n.endswith(".wav")
            ),
            key=lambda i: i.header_offset,
        )

    def fetch_subset(self, subset):
        """One ranged download per subset, then local extraction with CRC checks."""
        import requests

        infos = self.members(subset)
        start = infos[0].header_offset
        end = max(
            i.header_offset + 30 + len(i.filename.encode()) + 1024 + i.compress_size
            for i in infos
        )
        if end - start > 3 * sum(i.compress_size for i in infos) + (1 << 24):
            # Members are not contiguous: read them one by one instead.
            return {i.filename.split("/")[-1]: self.zip.read(i.filename) for i in infos}

        def extract(blob):
            if len(blob) != end - start + 1:
                raise RuntimeError("range size mismatch")
            out = {}
            for info in infos:
                off = info.header_offset - start
                (sig,) = struct.unpack_from("<I", blob, off)
                if sig != 0x04034B50:
                    raise RuntimeError(f"bad local header for {info.filename}")
                name_len, extra_len = struct.unpack_from("<HH", blob, off + 26)
                data_start = off + 30 + name_len + extra_len
                raw = blob[data_start : data_start + info.compress_size]
                if len(raw) != info.compress_size:
                    raise RuntimeError(f"compressed size mismatch for {info.filename}")
                if info.compress_type == 8:
                    decoder = zlib.decompressobj(-15)
                    data = decoder.decompress(raw)
                    if not decoder.eof or decoder.unused_data:
                        raise RuntimeError(
                            f"incomplete deflate stream for {info.filename}"
                        )
                elif info.compress_type == 0:
                    data = raw
                else:
                    raise RuntimeError(f"unsupported compression {info.compress_type}")
                if (zlib.crc32(data) & 0xFFFFFFFF) != info.CRC or len(
                    data
                ) != info.file_size:
                    raise RuntimeError(f"CRC/size mismatch for {info.filename}")
                out[info.filename.split("/")[-1]] = data
            return out

        target = self.cache / f"{subset}.range" if self.cache else None
        if target is not None and target.exists():
            try:
                return extract(target.read_bytes())
            except (RuntimeError, struct.error, zlib.error):
                target.unlink()

        temporary = None
        try:
            # Keep partial responses off the cache path, on the same filesystem
            # so replacement is atomic after every member's size/CRC is verified.
            with tempfile.NamedTemporaryFile(
                dir=self.cache, prefix=f".{subset}.", suffix=".tmp", delete=False
            ) as download:
                temporary = Path(download.name)
                headers = {"Range": f"bytes={start}-{end}"}
                with requests.get(
                    self.url, headers=headers, stream=True, timeout=120
                ) as r:
                    if r.status_code != 206:
                        raise RuntimeError(
                            f"range request refused: HTTP {r.status_code}"
                        )
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        download.write(chunk)
            result = extract(temporary.read_bytes())
            if target is not None:
                temporary.replace(target)
            return result
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _read_csv(text):
    return list(csv.DictReader(io.StringIO(text)))


def _speaker_group(subset, filename):
    if subset == "NISQA_TEST_LIVETALK":
        m = re.match(r"c\d+_([fm]\d+)\.wav$", filename)
        return "livetalk:" + (m.group(1) if m else filename)
    m = re.search(r"reader_(\d+)", filename)
    return "librivox_reader:" + (m.group(1) if m else filename)


# --------------------------------------------------------------------------- stage 1


def _qualify(data, detector):
    """Product decode + detector + window admission; no quality model involved."""
    from ee.voice.services.audio_analysis.decode import decode_audio
    from ee.voice.services.audio_analysis.errors import AudioDeterministicError
    from ee.voice.services.audio_analysis.vqi import prepare_vqi_windows

    try:
        decoded = decode_audio(data)
    except AudioDeterministicError as exc:
        return {"reason": exc.reason}, None
    y16, admitted, coverage, reason = prepare_vqi_windows(
        decoded.waveform, decoded.sample_rate_hz, detector
    )
    return {
        "reason": reason,
        "sample_rate_hz": decoded.sample_rate_hz,
        "duration_seconds": decoded.duration_seconds,
        "waveform_sha256": _sha256(np.ascontiguousarray(decoded.waveform).tobytes()),
        "y16_sha256": _sha256(np.ascontiguousarray(y16).tobytes()),
        "coverage": coverage,
        "admitted_starts": admitted,
    }, (y16, admitted)


def assign_splits(clips):
    """Speaker-grouped, seeded split; VAL_LIVE readers are always locked test."""
    rng = np.random.default_rng(SEED)
    eligible = [c for c in clips if c["eligible"]]
    train_readers = sorted(
        {c["group"] for c in eligible if c["subset"] == "NISQA_TRAIN_LIVE"}
    )
    order = list(rng.permutation(len(train_readers)))
    n_test = round(TEST_TRAIN_READER_FRACTION * len(train_readers))
    test_readers = {train_readers[i] for i in order[:n_test]}
    rest = [train_readers[i] for i in order[n_test:]]
    n_cal = round(CALIBRATION_SHARE_OF_REST * len(rest))
    cal_readers, sel_readers = set(rest[:n_cal]), set(rest[n_cal:])
    for c in clips:
        if not c["eligible"]:
            c["split"] = None
        elif c["subset"] == "NISQA_TEST_LIVETALK":
            c["split"] = "external_test"
        elif c["subset"] == "NISQA_VAL_LIVE" or c["group"] in test_readers:
            c["split"] = "test"
        elif c["group"] in cal_readers:
            c["split"] = "calibration"
        elif c["group"] in sel_readers:
            c["split"] = "selection"
        else:  # unreachable: every train reader is assigned above
            raise RuntimeError("unassigned reader")
    return clips


def stage1(args):
    from ee.voice.services.audio_analysis.vad import SileroVad

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache"
    cache.mkdir(exist_ok=True)
    zr = ZipRanges(cache=cache)
    size = (
        zr.zip.fp.size if hasattr(zr.zip, "fp") and hasattr(zr.zip.fp, "size") else None
    )
    detector = SileroVad()
    detector_id = detector.detector_id
    licenses = {}
    for name in LICENSE_FILES:
        text = zr.read_small(name)
        licenses[name] = {
            "sha256": _sha256(text),
            "license_lines": [
                line.strip()
                for line in text.decode("utf-8", "replace").splitlines()
                if re.search(r"licen|commercial|public domain|attribution", line, re.I)
            ][:12],
        }
    clips = []
    t0 = time.time()
    for subset, language in SUBSETS.items():
        rows = _read_csv(
            zr.read_small(f"NISQA_Corpus/{subset}/{subset}_file.csv").decode()
        )
        audio = zr.fetch_subset(subset)
        for row in rows:
            name = row["filename_deg"]
            data = audio[name]
            votes = float(row["votes"])
            mos_std = (
                float(row["mos_std"])
                if row.get("mos_std") not in (None, "")
                else float("nan")
            )
            result, _ = _qualify(data, detector)
            reason = result["reason"]
            if reason is None and votes < MIN_VOTES:
                reason = "insufficient_ratings"
            clip = {
                "id": f"{subset}/{name}",
                "subset": subset,
                "file": name,
                "language": language,
                "group": _speaker_group(subset, name),
                "condition": row.get("con_description"),
                "mos": float(row["mos"]),
                "votes": votes,
                "mos_std": mos_std,
                "mos_ci95": 1.96 * mos_std / math.sqrt(votes)
                if votes > 0 and math.isfinite(mos_std)
                else None,
                "bytes_sha256": _sha256(data),
                "reason": reason,
                "eligible": reason is None,
            }
            clip.update({k: v for k, v in result.items() if k != "reason"})
            clips.append(clip)
        print(f"stage1 {subset}: {len(rows)} rows, {time.time() - t0:.0f}s", flush=True)
    assign_splits(clips)
    counts = {}
    for c in clips:
        key = (c["subset"], c["reason"] or "eligible", c["split"] or "-")
        counts["|".join(key)] = counts.get("|".join(key), 0) + 1
    manifest = {
        "study": "th2094-vqi-calibration-nisqa-live",
        "stage": 1,
        "created_at": args.created_at,
        "dataset": {
            "record": NISQA_RECORD,
            "url": NISQA_URL,
            "zip_bytes_expected": NISQA_ZIP_BYTES,
            "zip_bytes_observed": size,
            "subsets": SUBSETS,
            "license_evidence": licenses,
        },
        "pipeline": {
            "files_sha256": pipeline_fingerprint(),
            "module_files_sha256_informational": {
                rel: _sha256((ROOT / rel).read_bytes()) for rel in PIPELINE_FILES
            },
            "constants": _pipeline_constants(detector_id),
            "packages": {p: _pkg(p) for p in PACKAGES},
            "python": sys.version.split()[0],
        },
        "protocol": {
            "seed": SEED,
            "min_votes": MIN_VOTES,
            "test_train_reader_fraction": TEST_TRAIN_READER_FRACTION,
            "calibration_share_of_rest": CALIBRATION_SHARE_OF_REST,
            "knot_candidates": KNOT_CANDIDATES,
            "selection_criterion": "lowest selection-split MOS RMSE over in-support clips; ties -> fewer knots",
            "support": "calibration-split x range (first/last knot); outside -> out_of_domain, excluded from accuracy and reported as coverage loss",
            "gates": GATES,
            "cohorts": {
                "en": f"en/{TRANSPORT}/{CAPTURE_ORIGIN}",
                "de": f"de/{TRANSPORT}/{CAPTURE_ORIGIN}",
            },
            "cohort_admission": "a cohort is statistically admitted only if its own held-out data passes every statistical gate (rmse, spearman, lb95, pair_rank_acc, n_heldout, |bias|, cohort rmse); candidate gates report the worst values across admitted cohorts; release qualifications remain unmet by public data",
            "pair_rule": {
                "name": "material_difference_pair_concordance_v1",
                "definition": f"all held-out clip pairs with |MOS_i - MOS_j| >= {PAIR_MIN_DELTA_MOS} and non-overlapping per-clip 95% CIs (1.96*std/sqrt(votes)); accuracy = share ordered like human MOS by calibrated prediction, ties wrong",
                "note": "substitute: the PRD's listener-confirmed same-source paired degradations are not available in this corpus",
            },
            "bootstrap": {
                "unit": "speaker group",
                "n": 2000,
                "seed": SEED,
                "statistic": "Spearman 2.5th percentile",
            },
        },
        "counts": counts,
        "clips": clips,
    }
    raw = _canonical(manifest)
    (out / "manifest.json").write_bytes(raw)
    print(json.dumps({"manifest_sha256": _sha256(raw), "counts": counts}, indent=1))


# --------------------------------------------------------------------------- stage 2


def _fit_and_select(cal_x, cal_y, sel_x, sel_y):
    from .stats import monotone_knots, pchip

    trials = []
    for k in KNOT_CANDIDATES:
        kx, ky = monotone_knots(cal_x, cal_y, k)
        if len(kx) < 2:
            continue
        g = pchip(kx, ky)
        pred = np.asarray(g(np.asarray(sel_x)), dtype=float)
        keep = np.isfinite(pred)
        rmse = (
            float(np.sqrt(np.mean((pred[keep] - np.asarray(sel_y)[keep]) ** 2)))
            if keep.any()
            else float("inf")
        )
        trials.append(
            {
                "knots": k,
                "knots_x": kx,
                "knots_y": ky,
                "selection_rmse": rmse,
                "selection_in_support": int(keep.sum()),
            }
        )
    best = min(trials, key=lambda t: (t["selection_rmse"], t["knots"]))
    return best, trials


def _evaluate(clips, g):
    from .stats import (
        error_summary,
        group_bootstrap_spearman_lb,
        material_pairs,
        pair_concordance,
    )

    xs = np.asarray([c["x"] for c in clips], dtype=float)
    pred = np.asarray(g(xs), dtype=float)
    keep = np.isfinite(pred)
    used = [c for c, k in zip(clips, keep, strict=True) if k]
    p = pred[keep]
    mos = np.asarray([c["mos"] for c in used], dtype=float)
    summary = error_summary(p, mos)
    lb, n_ok = group_bootstrap_spearman_lb(p, mos, [c["group"] for c in used])
    ci = [c["mos_ci95"] if c["mos_ci95"] is not None else float("inf") for c in used]
    pairs = material_pairs(mos, ci, min_delta=PAIR_MIN_DELTA_MOS)
    summary.update(
        {
            "n_eligible": len(clips),
            "n_out_of_support": int((~keep).sum()),
            "in_support_coverage": len(used) / len(clips) if clips else 0.0,
            "n_groups": len({c["group"] for c in used}),
            "spearman_lb95_group_bootstrap": lb,
            "bootstrap_valid_resamples": n_ok,
            "pair_count": len(pairs),
            "pair_rank_acc": pair_concordance(p, mos, pairs),
            "votes_distribution": {
                str(int(v)): int(sum(1 for c in used if int(c["votes"]) == int(v)))
                for v in sorted({c["votes"] for c in used})
            },
        }
    )
    return summary, p


def _gates(s):
    g = GATES
    checks = {
        "rmse": s["rmse_mos"] <= g["rmse_max"],
        "spearman": s["spearman"] >= g["spearman_min"],
        "spearman_lb95": s["spearman_lb95_group_bootstrap"] >= g["spearman_lb95_min"],
        "pair_rank_acc": math.isfinite(s["pair_rank_acc"])
        and s["pair_rank_acc"] >= g["pair_rank_acc_min"],
        "n_heldout": s["n"] >= g["cohort_n_heldout_min"],
        "cohort_bias": abs(s["bias_mos"]) <= g["cohort_abs_bias_max"],
        "cohort_rmse": s["rmse_mos"] <= g["cohort_rmse_max"],
    }
    return checks, all(checks.values())


def _finite(obj):
    """Replace NaN/inf with None so the report is strict JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def stage2(args):
    from ee.voice.services.audio_analysis.vad import SileroVad
    from ee.voice.services.audio_analysis.vqi import _session, mean_adjusted_ovrl

    raw = Path(args.manifest).read_bytes()
    if _sha256(raw) != args.manifest_sha256:
        raise SystemExit("manifest digest does not match the frozen stage-1 digest")
    manifest = json.loads(raw)
    if manifest["pipeline"]["files_sha256"] != pipeline_fingerprint():
        raise SystemExit("pipeline files changed since stage 1; rerun stage 1")
    model_bytes = Path(args.model).read_bytes()
    if _sha256(model_bytes) != args.model_sha256:
        raise SystemExit("model digest mismatch")
    detector = SileroVad()
    if detector.detector_id != manifest["pipeline"]["constants"]["detector_id"]:
        raise SystemExit("detector version changed since stage 1")
    session = _session(str(args.model))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache"
    cache.mkdir(exist_ok=True)
    zr = ZipRanges(cache=cache)
    clips = [c for c in manifest["clips"] if c["eligible"]]
    by_subset = {}
    for c in clips:
        by_subset.setdefault(c["subset"], []).append(c)
    t0 = time.time()
    for subset, items in by_subset.items():
        audio = zr.fetch_subset(subset)
        for n, c in enumerate(items, 1):
            data = audio[c["file"]]
            if _sha256(data) != c["bytes_sha256"]:
                raise SystemExit(f"clip bytes changed: {c['id']}")
            result, prepared = _qualify(data, detector)
            if (
                result["reason"] is not None
                or result["y16_sha256"] != c["y16_sha256"]
                or result["coverage"] != c["coverage"]
            ):
                raise SystemExit(f"stage-1 qualification not reproduced for {c['id']}")
            y16, admitted = prepared
            c["x"] = mean_adjusted_ovrl(session, y16, admitted)
            if n % 100 == 0:
                print(
                    f"stage2 {subset}: {n}/{len(items)} scored, {time.time() - t0:.0f}s",
                    flush=True,
                )
        del audio
        (out / "x_partial.json").write_text(
            json.dumps({c["id"]: c.get("x") for c in clips})
        )
        print(
            f"stage2 {subset}: {len(items)} clips scored, {time.time() - t0:.0f}s",
            flush=True,
        )
    split = lambda name: [c for c in clips if c["split"] == name]  # noqa: E731
    cal, sel, test, ext = (
        split("calibration"),
        split("selection"),
        split("test"),
        split("external_test"),
    )
    best, trials = _fit_and_select(
        [c["x"] for c in cal],
        [c["mos"] for c in cal],
        [c["x"] for c in sel],
        [c["mos"] for c in sel],
    )
    from .stats import pchip

    g = pchip(best["knots_x"], best["knots_y"])
    en_summary, _ = _evaluate(test, g)
    de_summary, _ = _evaluate(ext, g) if ext else ({}, None)
    en_checks, en_pass = _gates(en_summary)
    de_checks, de_pass = _gates(de_summary) if ext else ({}, False)
    calibration_id = f"vqi_nisqa_live_v1_{args.manifest_sha256[:12]}_k{best['knots']}"
    cohorts, admitted_summaries = [], []
    for language, summary, passed in (
        ("en", en_summary, en_pass),
        ("de", de_summary, de_pass),
    ):
        if passed:
            cohorts.append(
                {
                    "tag": manifest["protocol"]["cohorts"][language],
                    "n_heldout": summary["n"],
                    "bias": summary["bias_mos"],
                    "rmse": summary["rmse_mos"],
                }
            )
            admitted_summaries.append(summary)
    # Public natural-speech statistics do not establish any release qualification.
    qualifications = {
        "detector_activity_validated": False,
        "detector_activity_evidence_ref": None,
        "target_domain_validated": False,
        "target_domain_evidence_ref": None,
        "paired_degradations_validated": False,
        "paired_degradations_evidence_ref": None,
    }
    report = {
        "study": manifest["study"],
        "stage": 2,
        "created_at": args.created_at,
        "manifest_sha256": args.manifest_sha256,
        "model_sha256": args.model_sha256,
        "calibration_id": calibration_id,
        "split_sizes": {
            "calibration": len(cal),
            "selection": len(sel),
            "test": len(test),
            "external_test": len(ext),
        },
        "selection_trials": trials,
        "chosen": {
            "knots": best["knots"],
            "knots_x": best["knots_x"],
            "knots_y": best["knots_y"],
        },
        "calibration_x_range": [best["knots_x"][0], best["knots_x"][-1]],
        "heldout_en": en_summary,
        "heldout_en_gates": en_checks,
        "heldout_en_pass": en_pass,
        "external_de": de_summary,
        "external_de_gates": de_checks,
        "external_de_pass": de_pass,
        "admitted_cohorts": [c["tag"] for c in cohorts],
        "statistical_gates_passed": bool(cohorts),
        "release_qualified": False,
        "release_qualifications": qualifications,
        "outcome": "statistical_gates_passed"
        if cohorts
        else "statistical_gates_failed",
        "x_by_clip": {c["id"]: c["x"] for c in clips},
    }
    # Preserve the statistical outcome even if candidate output fails. Never
    # validate/promote this research candidate through the production loader.
    report_path = out / "report.json"
    report_path.write_bytes(
        json.dumps(_finite(report), indent=1, sort_keys=True).encode()
    )
    if cohorts:
        artifact = {
            "calibration_id": calibration_id,
            "model_sha256": args.model_sha256,
            "polynomial_version": manifest["pipeline"]["constants"][
                "polynomial_version"
            ],
            "preprocessing_version": manifest["pipeline"]["constants"][
                "preprocessing_version"
            ],
            "detector_id": manifest["pipeline"]["constants"]["detector_id"],
            "window_policy_version": manifest["pipeline"]["constants"][
                "window_policy_version"
            ],
            "scale_id": manifest["pipeline"]["constants"]["scale_id"],
            "dataset_manifest_sha256": args.manifest_sha256,
            "g": {
                "type": "monotone_pchip",
                "knots_x": best["knots_x"],
                "knots_y": best["knots_y"],
            },
            "support": {"x_min": best["knots_x"][0], "x_max": best["knots_x"][-1]},
            "gates": {
                "rmse": max(s["rmse_mos"] for s in admitted_summaries),
                "spearman": min(s["spearman"] for s in admitted_summaries),
                "spearman_lb95": min(
                    s["spearman_lb95_group_bootstrap"] for s in admitted_summaries
                ),
                "pair_rank_acc": min(s["pair_rank_acc"] for s in admitted_summaries),
                "pair_rank_definition": manifest["protocol"]["pair_rule"]["name"],
            },
            "release_qualifications": qualifications,
            "cohorts": cohorts,
            "source": {
                "dataset": "NISQA Corpus (Mittag et al., 2021), subsets NISQA_TRAIN_LIVE, NISQA_VAL_LIVE, NISQA_TEST_LIVETALK",
                "record": NISQA_RECORD,
                "licenses": "TRAIN/VAL_LIVE: public-domain LibriVox source, readme permits commercial and non-commercial research; LIVETALK: CC BY 4.0",
                "limitations": "natural (not synthesized) speech; telephone/VoIP paths pooled under the product 'sip' tag; ~5 crowd votes per English clip; detector precision/recall not validated on adjudicated activity labels; PRD same-source paired degradations unavailable",
            },
        }
        artifact_raw = _canonical(_finite(artifact))
        (out / "candidate-artifact.json").write_bytes(artifact_raw)
        report["candidate_artifact_sha256"] = _sha256(artifact_raw)
        report_path.write_bytes(
            json.dumps(_finite(report), indent=1, sort_keys=True).encode()
        )
    print(
        json.dumps(
            _finite(
                {
                    k: report[k]
                    for k in (
                        "calibration_id",
                        "split_sizes",
                        "heldout_en",
                        "heldout_en_gates",
                        "external_de_gates",
                        "admitted_cohorts",
                        "statistical_gates_passed",
                        "release_qualified",
                        "outcome",
                    )
                }
            ),
            indent=1,
        )
    )


def main(argv=None):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.test")
    import django

    django.setup()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="stage", required=True)
    s1 = sub.add_parser("stage1")
    s1.add_argument("--out", required=True)
    s1.add_argument("--created-at", required=True)
    s2 = sub.add_parser("stage2")
    s2.add_argument("--manifest", required=True)
    s2.add_argument("--manifest-sha256", required=True)
    s2.add_argument("--model", required=True)
    s2.add_argument("--model-sha256", required=True)
    s2.add_argument("--out", required=True)
    s2.add_argument("--created-at", required=True)
    args = parser.parse_args(argv)
    stage1(args) if args.stage == "stage1" else stage2(args)


if __name__ == "__main__":
    main()
