"""Calibration-study arithmetic: monotone fit, gates and grouped statistics."""

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import numpy as np
import pytest
import requests

from ee.voice.calibration import stats
from ee.voice.calibration import vqi_study as study


def test_pava_is_monotone_and_pools_violations():
    ux, fitted = stats.pava([1, 2, 3, 4], [1.0, 3.0, 2.0, 4.0])
    assert list(ux) == [1, 2, 3, 4]
    assert list(fitted) == [1.0, 2.5, 2.5, 4.0]


def test_pava_pools_tied_x():
    ux, fitted = stats.pava([1, 1, 2], [1.0, 3.0, 2.5])
    assert list(ux) == [1, 2]
    assert list(fitted) == [2.0, 2.5]


def test_monotone_knots_cover_calibration_range_and_mos_anchors():
    rng = np.random.default_rng(0)
    x = rng.uniform(1.5, 4.0, 300)
    y = np.clip(x + rng.normal(0, 0.4, 300), 0.5, 5.5)
    kx, ky = stats.monotone_knots(x, y, 6)
    assert kx[0] == pytest.approx(x.min()) and kx[-1] == pytest.approx(x.max())
    assert all(b > a for a, b in zip(kx, kx[1:], strict=False))
    assert all(b >= a for a, b in zip(ky, ky[1:], strict=False))
    assert 1.0 <= min(ky) <= max(ky) <= 5.0
    g = stats.pchip(kx, ky)
    assert np.isnan(g(x.max() + 0.01))  # no extrapolation outside support


def test_spearman_and_ties():
    assert stats.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert stats.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert list(stats.rankdata([3, 1, 3])) == [2.5, 1.0, 2.5]


def test_group_bootstrap_resamples_whole_groups_deterministically():
    pred = [1, 2, 3, 4, 5, 6]
    mos = [1, 2, 3, 4, 5, 6]
    groups = ["a", "a", "b", "b", "c", "c"]
    lb1 = stats.group_bootstrap_spearman_lb(pred, mos, groups, n_boot=200)
    lb2 = stats.group_bootstrap_spearman_lb(pred, mos, groups, n_boot=200)
    assert lb1 == lb2
    assert lb1[0] == pytest.approx(1.0)


def test_material_pairs_need_delta_and_non_overlapping_ci():
    mos = [1.0, 2.2, 2.4]
    ci = [0.1, 0.1, 0.5]
    assert stats.material_pairs(mos, ci) == [(0, 1), (0, 2)]
    assert stats.material_pairs(mos, [0.1, 0.1, 1.5]) == [(0, 1)]
    assert stats.pair_concordance([1, 2, 2], mos, [(0, 1), (0, 2)]) == 1.0
    assert stats.pair_concordance([2, 2, 1], mos, [(0, 1), (0, 2)]) == 0.0


def test_gates_are_the_prd_values():
    assert study.GATES == {
        "rmse_max": 0.5,
        "spearman_min": 0.70,
        "spearman_lb95_min": 0.60,
        "pair_rank_acc_min": 0.90,
        "cohort_n_heldout_min": 50,
        "cohort_abs_bias_max": 0.25,
        "cohort_rmse_max": 0.65,
    }
    passing = {
        "rmse_mos": 0.4,
        "spearman": 0.8,
        "spearman_lb95_group_bootstrap": 0.7,
        "pair_rank_acc": 0.95,
        "n": 60,
        "bias_mos": 0.1,
    }
    assert study._gates(passing)[1] is True
    for key, bad in (
        ("rmse_mos", 0.51),
        ("spearman", 0.69),
        ("spearman_lb95_group_bootstrap", 0.59),
        ("pair_rank_acc", 0.89),
        ("n", 49),
        ("bias_mos", -0.26),
    ):
        assert study._gates({**passing, key: bad})[1] is False


def test_splits_keep_readers_together_and_val_live_locked():
    clips = []
    for reader in range(40):
        for _ in range(3):
            clips.append(
                {
                    "subset": "NISQA_TRAIN_LIVE",
                    "group": f"r{reader}",
                    "eligible": True,
                }
            )
    clips += [
        {"subset": "NISQA_VAL_LIVE", "group": "v1", "eligible": True},
        {"subset": "NISQA_TEST_LIVETALK", "group": "f1", "eligible": True},
        {"subset": "NISQA_TRAIN_LIVE", "group": "r0", "eligible": False},
    ]
    study.assign_splits(clips)
    by_reader = {}
    for c in clips:
        if c["eligible"] and c["subset"] == "NISQA_TRAIN_LIVE":
            by_reader.setdefault(c["group"], set()).add(c["split"])
    assert all(len(s) == 1 for s in by_reader.values())
    assert clips[-3]["split"] == "test"
    assert clips[-2]["split"] == "external_test"
    assert clips[-1]["split"] is None
    splits = [next(iter(s)) for s in by_reader.values()]
    assert splits.count("test") == 14  # round(0.35 * 40)
    assert splits.count("calibration") == 17 and splits.count("selection") == 9


def test_group_bootstrap_empty_groups_is_unavailable():
    lower_bound, valid = stats.group_bootstrap_spearman_lb([], [], [])
    assert np.isnan(lower_bound)
    assert valid == 0


@pytest.mark.parametrize("xs", [[], [1.0, 4.0]])
def test_evaluate_no_in_support_predictions_reports_failed_gates(xs):
    clips = [
        {"x": x, "mos": 3.0, "group": "speaker", "mos_ci95": 0.1, "votes": 5}
        for x in xs
    ]
    summary, predictions = study._evaluate(clips, stats.pchip([2, 3], [2, 3]))
    assert len(predictions) == summary["n"] == summary["n_groups"] == 0
    assert summary["n_eligible"] == summary["n_out_of_support"] == len(clips)
    assert summary["in_support_coverage"] == 0
    assert summary["bootstrap_valid_resamples"] == summary["pair_count"] == 0
    checks, passed = study._gates(summary)
    assert passed is False
    assert not any(checks.values())
    report = json.loads(json.dumps(study._finite(summary), allow_nan=False))
    assert report["spearman_lb95_group_bootstrap"] is None
    assert report["rmse_mos"] is None


@pytest.fixture
def stage2_inputs(tmp_path, monkeypatch):
    """Synthetic I/O/model inputs; exercise real stage-2 admission and loader."""
    from ee.voice.services.audio_analysis import decode, vad, vqi

    detector = SimpleNamespace(detector_id="fixture-vad")
    monkeypatch.setattr(vad, "SileroVad", lambda: detector)
    monkeypatch.setattr(vqi, "_session", lambda path: object())
    monkeypatch.setattr(vqi, "mean_adjusted_ovrl", lambda *args: 3.0)
    monkeypatch.setattr(study, "pipeline_fingerprint", lambda: {"fixture": "digest"})
    data = b"fixture audio"
    qualification = {"reason": None, "y16_sha256": "decoded", "coverage": {}}
    monkeypatch.setattr(
        study, "_qualify", lambda *args: (qualification, (np.zeros(1), [0]))
    )
    clips = []
    for split in ("calibration", "selection", "test", "external_test"):
        clips.append(
            {
                "id": split,
                "file": f"{split}.wav",
                "eligible": True,
                "subset": "fixture",
                "split": split,
                "language": "de" if split == "external_test" else "en",
                "mos": 3.0,
                "bytes_sha256": study._sha256(data),
                "y16_sha256": "decoded",
                "coverage": {},
            }
        )
    monkeypatch.setattr(
        study,
        "ZipRanges",
        lambda **kwargs: SimpleNamespace(
            fetch_subset=lambda subset: {c["file"]: data for c in clips}
        ),
    )
    best = {"knots": 4, "knots_x": [1.0, 5.0], "knots_y": [1.0, 5.0]}
    monkeypatch.setattr(study, "_fit_and_select", lambda *args: (best, [best]))
    # Keep the old numeric-demo path harmless during the pre-fix RED run.
    monkeypatch.setattr(
        decode,
        "decode_audio",
        lambda data: SimpleNamespace(waveform=np.zeros(1), sample_rate_hz=16000),
    )
    monkeypatch.setattr(vqi, "analyze_vqi", lambda *args, **kwargs: {})
    manifest = {
        "study": "fixture-study",
        "pipeline": {
            "files_sha256": study.pipeline_fingerprint(),
            "constants": study._pipeline_constants(detector.detector_id),
        },
        "protocol": {
            "cohorts": {
                "en": "en/sip/provider_received_track",
                "de": "de/sip/provider_received_track",
            },
            "pair_rule": {"name": "fixture-pairs"},
        },
        "clips": clips,
    }
    manifest_path = tmp_path / "manifest.json"
    raw = study._canonical(manifest)
    manifest_path.write_bytes(raw)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fixture model")
    return SimpleNamespace(
        manifest=manifest_path,
        manifest_sha256=study._sha256(raw),
        model=model,
        model_sha256=study._sha256(model.read_bytes()),
        out=tmp_path / "output",
        created_at="2026-10-01T00:00:00Z",
    )


def _study_summaries(monkeypatch, *, en_pass=True, de_pass=True):
    passing = {
        "rmse_mos": 0.4,
        "spearman": 0.8,
        "spearman_lb95_group_bootstrap": 0.7,
        "pair_rank_acc": 0.95,
        "n": 60,
        "bias_mos": 0.1,
    }
    english = {**passing, "rmse_mos": 0.45 if en_pass else 0.6}
    german = {**passing, "spearman": 0.75 if de_pass else 0.6}
    summaries = iter((english, german))
    monkeypatch.setattr(study, "_evaluate", lambda *args: (next(summaries), None))
    return english, german


@pytest.mark.parametrize(
    "en_pass,de_pass", [(True, True), (False, True), (True, False)]
)
def test_stage2_only_writes_unqualified_candidate(
    stage2_inputs, monkeypatch, en_pass, de_pass
):
    from ee.voice.services.audio_analysis.calibration import load_calibration
    from ee.voice.services.audio_analysis.errors import AudioDeterministicError

    english, german = _study_summaries(monkeypatch, en_pass=en_pass, de_pass=de_pass)
    study.stage2(stage2_inputs)
    out = stage2_inputs.out
    assert not (out / "artifact.json").exists()
    candidate = out / "candidate-artifact.json"
    raw = json.loads(candidate.read_bytes())
    report = json.loads((out / "report.json").read_bytes())
    assert report["statistical_gates_passed"] is True
    assert report["release_qualified"] is False
    assert report["candidate_artifact_sha256"] == study._sha256(candidate.read_bytes())
    assert raw["release_qualifications"] == report["release_qualifications"]
    for name in ("detector_activity", "target_domain", "paired_degradations"):
        assert raw["release_qualifications"][f"{name}_validated"] is False
        assert raw["release_qualifications"][f"{name}_evidence_ref"] is None
    admitted = [s for s, passed in ((english, en_pass), (german, de_pass)) if passed]
    tags = [
        f"{lang}/sip/provider_received_track"
        for lang, passed in (("en", en_pass), ("de", de_pass))
        if passed
    ]
    assert report["admitted_cohorts"] == [c["tag"] for c in raw["cohorts"]] == tags
    assert raw["gates"]["rmse"] == max(s["rmse_mos"] for s in admitted)
    assert raw["gates"]["spearman"] == min(s["spearman"] for s in admitted)
    assert raw["gates"]["spearman_lb95"] == min(
        s["spearman_lb95_group_bootstrap"] for s in admitted
    )
    assert raw["gates"]["pair_rank_acc"] == min(s["pair_rank_acc"] for s in admitted)
    with pytest.raises(AudioDeterministicError, match="not_validated"):
        load_calibration(
            candidate,
            report["candidate_artifact_sha256"],
            model_sha256=stage2_inputs.model_sha256,
            detector_id="fixture-vad",
        )


def test_stage2_writes_report_when_all_statistical_gates_fail(
    stage2_inputs, monkeypatch
):
    _study_summaries(monkeypatch, en_pass=False, de_pass=False)
    study.stage2(stage2_inputs)
    report = json.loads((stage2_inputs.out / "report.json").read_bytes())
    assert report["statistical_gates_passed"] is False
    assert report["release_qualified"] is False
    assert report["admitted_cohorts"] == []
    assert not list(stage2_inputs.out.glob("*artifact.json"))


def test_stage2_preserves_report_before_candidate_write_failure(
    stage2_inputs, monkeypatch
):
    _study_summaries(monkeypatch)
    write_bytes = Path.write_bytes

    def fail_candidate(path, data):
        if path.name in ("artifact.json", "candidate-artifact.json"):
            assert (stage2_inputs.out / "report.json").exists()
            raise OSError("candidate write failed")
        return write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_candidate)
    with pytest.raises(OSError, match="candidate write failed"):
        study.stage2(stage2_inputs)
    report = json.loads((stage2_inputs.out / "report.json").read_bytes())
    assert report["statistical_gates_passed"] is True
    assert report["release_qualified"] is False
    assert "candidate_artifact_sha256" not in report


@pytest.fixture(params=[ZIP_STORED, ZIP_DEFLATED])
def ranged_corpus(tmp_path, monkeypatch, request):
    subset = "NISQA_TRAIN_LIVE"
    data = b"fixture wav bytes" * 10
    archive = io.BytesIO()
    with ZipFile(archive, "w", compression=request.param) as z:
        z.writestr(f"NISQA_Corpus/{subset}/deg/clip.wav", data)
        info = z.infolist()[0]
    # The range reader requests 1024 bytes of local-header padding plus an
    # inclusive endpoint; emulate the following archive bytes as well.
    size = 30 + len(info.filename.encode()) + 1024 + info.compress_size + 1
    blob = archive.getvalue().ljust(size, b"\0")
    reader = study.ZipRanges.__new__(study.ZipRanges)
    reader.url = "https://corpus.invalid/archive.zip"
    reader.infos = {info.filename: info}
    reader.cache = tmp_path
    get = Mock()
    monkeypatch.setattr(requests, "get", get)

    class Response:
        status_code = 206
        body = blob
        interrupt = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size):
            yield self.body[:16]
            if self.interrupt:
                raise requests.Timeout("interrupted fixture stream")
            yield self.body[16:]

    response = Response()
    get.return_value = response
    return SimpleNamespace(
        reader=reader,
        subset=subset,
        data=data,
        blob=blob,
        response=response,
        get=get,
        target=tmp_path / f"{subset}.range",
    )


@pytest.mark.parametrize("corruption", ["truncated", "crc", "header"])
def test_range_cache_invalid_entries_are_refetched(ranged_corpus, corruption):
    f = ranged_corpus
    bad = bytearray(f.blob)
    if corruption == "truncated":
        bad = bad[:10]
    elif corruption == "header":
        bad[0] ^= 0xFF
    else:
        info = next(iter(f.reader.infos.values()))
        bad[30 + len(info.filename.encode())] ^= 0xFF
    f.target.write_bytes(bad)
    assert f.reader.fetch_subset(f.subset) == {"clip.wav": f.data}
    assert f.target.read_bytes() == f.blob
    f.get.assert_called_once_with(
        f.reader.url,
        headers={"Range": f"bytes=0-{len(f.blob) - 1}"},
        stream=True,
        timeout=120,
    )
    assert f.reader.fetch_subset(f.subset) == {"clip.wav": f.data}
    assert f.get.call_count == 1  # Valid cache hits never fetch again.
    assert not list(f.target.parent.glob("*.tmp"))


def test_interrupted_range_download_never_publishes_cache(ranged_corpus):
    f = ranged_corpus
    f.response.interrupt = True
    with pytest.raises(requests.Timeout):
        f.reader.fetch_subset(f.subset)
    assert not f.target.exists()
    assert not list(f.target.parent.glob("*.tmp"))
    f.response.interrupt = False
    assert f.reader.fetch_subset(f.subset) == {"clip.wav": f.data}
    assert f.target.read_bytes() == f.blob


@pytest.mark.parametrize("corruption", ["truncated", "crc"])
def test_invalid_download_never_publishes_range_cache(ranged_corpus, corruption):
    f = ranged_corpus
    if corruption == "truncated":
        f.response.body = f.blob[:10]
    else:
        # A complete but different ZIP member must fail its original CRC.
        bad = bytearray(f.blob)
        info = next(iter(f.reader.infos.values()))
        bad[30 + len(info.filename.encode()) + info.compress_size // 2] ^= 0x01
        f.response.body = bytes(bad)
    with pytest.raises((RuntimeError, study.struct.error, study.zlib.error)):
        f.reader.fetch_subset(f.subset)
    assert not f.target.exists()
    assert not list(f.target.parent.glob("*.tmp"))


def test_uncached_range_download_still_validates_members(ranged_corpus):
    f = ranged_corpus
    f.reader.cache = None
    assert f.reader.fetch_subset(f.subset) == {"clip.wav": f.data}
    assert not f.target.exists()
