"""
Tests for image-input preprocessors registered in evaluations.engine.preprocessing.

Pins three behaviors:
  - Passthrough: None / empty / data-URI / base64 / file path are not touched.
  - SSRF guard: private / loopback / metadata hosts return the original URL
    untouched (sandbox handles the error gracefully). The guard itself
    (IP-pinning, redirect re-validation) is exercised via `_safe_get`
    (`tfc.utils.ssrf_guard`), which this module now delegates to (TH-5648
    follow-up: this used to have its own weaker, prefix-based blocklist).
  - Fetch path: public http(s) URLs are downloaded and returned as base64.
"""

from __future__ import annotations

import base64
import json
import sys
import types

from unittest.mock import patch

import pytest

from evaluations.engine.preprocessing import (
    PREPROCESSORS,
    _DEFAULT_FID_BATCH_SIZE,
    _FID_METRICS,
    _fid_device,
    _fid_batch_size,
    _get_fid_metric,
    _preprocess_fid,
    _resolve_fid_input,
    _resolve_image_input,
    _resolve_image_input_as_data_uri,
    _update_fid_in_batches,
    preprocess_inputs,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_image_preprocessors_registered():
    for name in ("image_properties", "psnr", "ssim"):
        assert name in PREPROCESSORS, f"{name} preprocessor must be registered"


# ---------------------------------------------------------------------------
# Resolver passthroughs (no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "aGVsbG8=",
        "data:image/png;base64,iVBORw0KGgo...",
        "/tmp/img.png",
        42,
        b"bytes",
    ],
)
def test_resolver_does_not_touch_non_url_inputs(value):
    with patch("evaluations.engine.preprocessing.safe_fetch") as mock_get:
        out = _resolve_image_input(value)
        mock_get.assert_not_called()
        assert out == value


# ---------------------------------------------------------------------------
# SSRF guard — blocked URLs must not fetch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9999/img.png",
        "http://10.255.255.255/img.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/admin/screenshot.png",
        "http://localhost/file",
    ],
)
def test_blocked_urls_never_fetch(url):
    """No mocking needed: `_safe_get` rejects these via `_reject_unsafe_ip`
    before it ever opens a connection, so the fetch fails and the original
    URL is returned untouched.
    """
    out = _resolve_image_input(url)
    assert out == url


# ---------------------------------------------------------------------------
# Fetch path
# ---------------------------------------------------------------------------


def _safe_get_result(*, status=200, body=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png", url="https://example.com/img.png"):
    from tfc.utils.ssrf_guard import SsrfResponse
    return SsrfResponse(status, {"Content-Type": content_type}, body, url)


def test_successful_fetch_returns_base64():
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body),
    ):
        out = _resolve_image_input("https://example.com/img.png")
    assert isinstance(out, str)
    assert base64.b64decode(out) == body


def test_non_200_returns_original_url():
    url = "https://example.com/missing.png"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(status=404, body=b"", url=url),
    ):
        assert _resolve_image_input(url) == url


def test_fetch_exception_returns_original_url():
    url = "https://example.com/img.png"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        side_effect=Exception("connection refused"),
    ):
        assert _resolve_image_input(url) == url


def test_oversize_response_rejected_by_safe_get_returns_original_url():
    """The 25MB ceiling is enforced inside `_safe_get` itself; it surfaces as
    a ValueError which `_fetch_url_bytes` must catch and fall back on."""
    url = "https://example.com/huge.png"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        side_effect=ValueError("Image URL body exceeds byte limit."),
    ):
        out = _resolve_image_input(url)
    assert out == url


# ---------------------------------------------------------------------------
# Preprocessor wiring — kwargs get replaced
# ---------------------------------------------------------------------------


def test_image_properties_replaces_text_kwarg():
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(),
    ):
        out = preprocess_inputs("image_properties", {"text": "https://example.com/x.png"})
    assert out["text"] != "https://example.com/x.png"
    assert isinstance(out["text"], str) and len(out["text"]) > 0


def test_psnr_replaces_both_kwargs():
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(),
    ):
        out = preprocess_inputs(
            "psnr",
            {
                "output": "https://example.com/a.png",
                "expected": "https://example.com/b.png",
            },
        )
    assert out["output"] != "https://example.com/a.png"
    assert out["expected"] != "https://example.com/b.png"


def test_ssim_replaces_both_kwargs():
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(),
    ):
        out = preprocess_inputs(
            "ssim",
            {
                "output": "https://example.com/a.png",
                "expected": "https://example.com/b.png",
            },
        )
    assert out["output"] != "https://example.com/a.png"
    assert out["expected"] != "https://example.com/b.png"


# ---------------------------------------------------------------------------
# Data-URI resolver (clip / fid path)
# ---------------------------------------------------------------------------


def test_data_uri_resolver_returns_data_uri_for_url():
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="image/png"),
    ):
        out = _resolve_image_input_as_data_uri("https://example.com/x.png")
    assert isinstance(out, str)
    assert out.startswith("data:image/png;base64,")


def test_data_uri_resolver_forces_image_mime_when_octet_stream():
    """S3 sometimes serves Content-Type: application/octet-stream; consumers
    rely on the `data:image/...` prefix to route — force it."""
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="application/octet-stream"),
    ):
        out = _resolve_image_input_as_data_uri("https://example.com/key-no-extension")
    assert out.startswith("data:image/jpeg;base64,")


def test_data_uri_resolver_passthrough_on_existing_data_uri():
    given = "data:image/png;base64,XXX"
    assert _resolve_image_input_as_data_uri(given) == given


def test_data_uri_resolver_blocks_private_hosts():
    url = "http://169.254.169.254/latest/meta-data/"
    out = _resolve_image_input_as_data_uri(url)
    assert out == url


def test_data_uri_resolver_rejects_svg_content_type():
    """SVG can carry <script>; if the preprocessor emitted
    `data:image/svg+xml;base64,...` any downstream inline renderer would
    execute it. Reject at fetch time — fall through to the original URL so
    the sandbox produces its own load error, no data URI is returned.
    """
    body = b"<svg onload=\"alert(1)\"></svg>"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="image/svg+xml"),
    ):
        out = _resolve_image_input_as_data_uri("https://example.com/x.svg")
    # On rejection, _fetch_url_bytes returns None → resolver returns original
    # URL string, not a data URI carrying the SVG.
    assert out == "https://example.com/x.svg"
    assert not out.startswith("data:")


# ---------------------------------------------------------------------------
# FID list resolver
# ---------------------------------------------------------------------------


def test_fid_resolver_json_list_in_json_list_out():
    body = b"\x89PNG\r\n\x1a\n"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="image/png"),
    ):
        out = _resolve_fid_input(
            '["https://example.com/a.png", "https://example.com/b.png"]'
        )
    parsed = json.loads(out)
    assert len(parsed) == 2
    assert all(item.startswith("data:image/png;base64,") for item in parsed)


def test_fid_resolver_python_list_passthrough_shape():
    body = b"\x89PNG\r\n\x1a\n"
    with patch(
        "evaluations.engine.preprocessing.safe_fetch",
        return_value=_safe_get_result(body=body, content_type="image/png"),
    ):
        out = _resolve_fid_input(["https://example.com/a.png"])
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0].startswith("data:image/png;base64,")


def test_fid_resolver_preserves_non_url_items():
    items = ["data:image/png;base64,YYY", "/tmp/local.png"]
    out = _resolve_fid_input(items)
    assert out == items


# ---------------------------------------------------------------------------
# FID batching
# ---------------------------------------------------------------------------


class _FakeBatch:
    def __init__(self, count):
        self.count = count

    def to(self, device):
        return self


class _FakeTorch:
    @staticmethod
    def cat(tensors, dim):
        return _FakeBatch(sum(tensor.count for tensor in tensors))


class _FakeMetric:
    def __init__(self):
        self.updates = []

    def update(self, batch, real):
        self.updates.append((batch.count, real))


@pytest.mark.parametrize(
    "image_count,batch_size,expected_counts",
    [
        (0, 4, []),
        (1, 4, [1]),
        (3, 4, [3]),
        (4, 4, [4]),
        (9, 4, [4, 4, 1]),
    ],
)
def test_fid_updates_images_in_batches(image_count, batch_size, expected_counts):
    metric = _FakeMetric()
    images = list(range(image_count))

    update_count = _update_fid_in_batches(
        metric,
        images,
        real=True,
        batch_size=batch_size,
        device="cpu",
        torch=_FakeTorch(),
        image_to_tensor=lambda image: _FakeBatch(1),
    )

    assert update_count == len(expected_counts)
    assert [count for count, real in metric.updates] == expected_counts
    assert all(real is True for count, real in metric.updates)


def test_fid_batch_size_is_configurable(monkeypatch):
    monkeypatch.setenv("FID_BATCH_SIZE", "7")
    assert _fid_batch_size() == 7


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_invalid_fid_batch_size_uses_default(monkeypatch, value):
    monkeypatch.setenv("FID_BATCH_SIZE", value)
    assert _fid_batch_size() == _DEFAULT_FID_BATCH_SIZE


def test_fid_metric_is_reused_per_device():
    class FakeMetric:
        instances = 0

        def __init__(self, feature):
            FakeMetric.instances += 1

        def to(self, device):
            return self

    device = "test-device"
    _FID_METRICS.pop(device, None)

    first = _get_fid_metric(FakeMetric, device)
    second = _get_fid_metric(FakeMetric, device)

    assert first is second
    assert FakeMetric.instances == 1


def test_fid_device_cache_distinguishes_cuda_devices():
    class FakeMetric:
        instances = []

        def __init__(self, feature):
            self.feature = feature
            self.placed_on = None
            FakeMetric.instances.append(self)

        def to(self, device):
            self.placed_on = device
            return self

    _FID_METRICS.clear()
    try:
        first = _get_fid_metric(FakeMetric, "cuda:0")
        same_device = _get_fid_metric(FakeMetric, "cuda:0")
        second = _get_fid_metric(FakeMetric, "cuda:1")

        assert first is same_device
        assert second is not first
        assert [metric.placed_on for metric in FakeMetric.instances] == [
            "cuda:0",
            "cuda:1",
        ]
    finally:
        _FID_METRICS.clear()


def test_fid_device_uses_active_cuda_index():
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            current_device=lambda: 1,
        )
    )

    assert _fid_device(fake_torch) == "cuda:1"


def test_fid_device_uses_real_cuda_index_when_available():
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available")

    assert _fid_device(torch) == f"cuda:{torch.cuda.current_device()}"


class _FakeInferenceMode:
    entered = 0

    def __enter__(self):
        _FakeInferenceMode.entered += 1

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _ProductionBatch:
    def __init__(self, size):
        self.shape = (size, 3, 299, 299)

    def to(self, device):
        return self


class _ProductionTorch(types.ModuleType):
    def __init__(self):
        super().__init__("torch")
        self.cuda = types.SimpleNamespace(
            is_available=lambda: False,
            current_device=lambda: 0,
        )

    @staticmethod
    def cat(tensors, dim):
        assert dim == 0
        return _ProductionBatch(len(tensors))

    @staticmethod
    def inference_mode():
        return _FakeInferenceMode()


class _FakeScore:
    def detach(self):
        return self

    def cpu(self):
        return self

    def __float__(self):
        return 12.5


def test_fid_preprocessor_batches_reuses_and_resets_metric(monkeypatch):
    class FakeFID:
        instances = []

        def __init__(self, feature):
            self.feature = feature
            self.reset_count = 0
            self.updates = []
            FakeFID.instances.append(self)

        def to(self, device):
            self.device = device
            return self

        def reset(self):
            self.reset_count += 1

        def update(self, batch, real):
            self.updates.append((batch.shape, real))

        def compute(self):
            return _FakeScore()

    torchmetrics_module = types.ModuleType("torchmetrics")
    torchmetrics_image_module = types.ModuleType("torchmetrics.image")
    torchmetrics_fid_module = types.ModuleType("torchmetrics.image.fid")
    torchmetrics_fid_module.FrechetInceptionDistance = FakeFID
    functions_module = types.ModuleType(
        "agentic_eval.core_evals.fi_evals.function.functions"
    )
    functions_module._parse_image_list = lambda images: list(images)
    functions_module._pil_to_uint8_tensor = lambda image: object()

    fake_torch = _ProductionTorch()
    monkeypatch.setenv("FID_BATCH_SIZE", "2")
    _FID_METRICS.clear()
    _FakeInferenceMode.entered = 0
    try:
        with patch.dict(
            sys.modules,
            {
                "torch": fake_torch,
                "torchmetrics": torchmetrics_module,
                "torchmetrics.image": torchmetrics_image_module,
                "torchmetrics.image.fid": torchmetrics_fid_module,
                "agentic_eval.core_evals.fi_evals.function.functions": functions_module,
            },
        ), patch(
            "evaluations.engine.preprocessing._resolve_fid_input",
            side_effect=lambda images: images,
        ):
            first = _preprocess_fid(
                {
                    "real_images": ["real-1", "real-2", "real-3"],
                    "fake_images": ["fake-1", "fake-2", "fake-3"],
                }
            )
            second = _preprocess_fid(
                {
                    "real_images": ["real-1", "real-2", "real-3"],
                    "fake_images": ["fake-1", "fake-2", "fake-3"],
                }
            )

        assert first["_fid_precomputed_score"] == 12.5
        assert second["_fid_precomputed_score"] == 12.5
        assert len(FakeFID.instances) == 1
        metric = FakeFID.instances[0]
        assert metric.updates == [
            ((2, 3, 299, 299), True),
            ((1, 3, 299, 299), True),
            ((2, 3, 299, 299), False),
            ((1, 3, 299, 299), False),
        ] * 2
        assert metric.reset_count == 4
        assert _FakeInferenceMode.entered == 2
    finally:
        _FID_METRICS.clear()
