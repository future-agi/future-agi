"""
Unit tests for image evaluation functions: _parse_image_list, calculate_fid, calculate_clip_score
and their wrapper classes FidScore, ClipScore.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pil_image(width=64, height=64, color="red"):
    """Create a small PIL Image for testing."""
    from PIL import Image
    return Image.new("RGB", (width, height), color=color)


def _import_functions():
    """Lazy import to avoid module-level import chain issues."""
    from agentic_eval.core_evals.fi_evals.function.functions import (
        _parse_image_list,
        calculate_clip_score,
        calculate_fid,
    )
    return _parse_image_list, calculate_fid, calculate_clip_score


# ---------------------------------------------------------------------------
# _parse_image_list
# ---------------------------------------------------------------------------

class TestParseImageList:
    """Unit tests for _parse_image_list."""

    @pytest.mark.unit
    def test_list_of_pil_images(self):
        """Accepts a list of PIL Images and returns them as-is."""
        _parse_image_list, _, _ = _import_functions()
        from PIL import Image

        imgs = [_make_pil_image(), _make_pil_image()]
        result = _parse_image_list(imgs)
        assert len(result) == 2
        assert all(isinstance(img, Image.Image) for img in result)

    @pytest.mark.unit
    def test_json_string_of_urls(self):
        """Parses a JSON string containing a list of URLs."""
        _parse_image_list, _, _ = _import_functions()

        urls = ["https://example.com/a.png", "https://example.com/b.png"]
        json_str = json.dumps(urls)
        fake_img = _make_pil_image()

        with patch("tfc.utils.storage.open_image_from_url", return_value=fake_img):
            result = _parse_image_list(json_str)
        assert len(result) == 2

    @pytest.mark.unit
    def test_single_url_string(self):
        """Parses a single URL string (not JSON)."""
        _parse_image_list, _, _ = _import_functions()

        fake_img = _make_pil_image()
        with patch("tfc.utils.storage.open_image_from_url", return_value=fake_img):
            result = _parse_image_list("https://example.com/image.png")
        assert len(result) == 1

    @pytest.mark.unit
    def test_list_of_urls(self):
        """Parses a list of URL strings."""
        _parse_image_list, _, _ = _import_functions()

        fake_img = _make_pil_image()
        urls = ["https://example.com/a.png", "https://example.com/b.png"]
        with patch("tfc.utils.storage.open_image_from_url", return_value=fake_img):
            result = _parse_image_list(urls)
        assert len(result) == 2

    @pytest.mark.unit
    def test_invalid_string_raises(self):
        """Raises ValueError for a non-URL, non-JSON string."""
        _parse_image_list, _, _ = _import_functions()

        with pytest.raises(ValueError, match="Cannot parse images input"):
            _parse_image_list("not-a-url-or-json")

    @pytest.mark.unit
    def test_non_list_raises(self):
        """Raises ValueError when input is not a list after parsing."""
        _parse_image_list, _, _ = _import_functions()

        with pytest.raises(ValueError, match="Expected a list of images"):
            _parse_image_list(12345)

    @pytest.mark.unit
    def test_unsupported_element_type_raises(self):
        """Raises ValueError for unsupported element types in list."""
        _parse_image_list, _, _ = _import_functions()

        with pytest.raises(ValueError, match="Unsupported image type"):
            _parse_image_list([12345])

    @pytest.mark.unit
    def test_url_load_failure_raises(self):
        """Raises ValueError when open_image_from_url returns None."""
        _parse_image_list, _, _ = _import_functions()

        with patch("tfc.utils.storage.open_image_from_url", return_value=None):
            with pytest.raises(ValueError, match="Failed to load image from"):
                _parse_image_list(["https://example.com/broken.png"])

    @pytest.mark.unit
    def test_empty_list(self):
        """Returns empty list for empty input."""
        _parse_image_list, _, _ = _import_functions()
        result = _parse_image_list([])
        assert result == []

    @pytest.mark.unit
    def test_mixed_pil_and_urls(self):
        """Handles a mix of PIL images and URL strings."""
        _parse_image_list, _, _ = _import_functions()

        pil_img = _make_pil_image()
        fake_img = _make_pil_image(color="blue")
        with patch("tfc.utils.storage.open_image_from_url", return_value=fake_img):
            result = _parse_image_list([pil_img, "https://example.com/img.png"])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# calculate_fid
# ---------------------------------------------------------------------------

class TestCalculateFid:
    """Unit tests for calculate_fid."""

    @pytest.mark.unit
    def test_too_few_real_images(self):
        """Returns error when fewer than 2 real images."""
        _, calculate_fid, _ = _import_functions()

        fake_img = _make_pil_image()
        result = calculate_fid(
            real_images=[fake_img],
            fake_images=[fake_img, fake_img],
        )
        assert result["result"] is None
        assert "at least 2" in result["reason"]

    @pytest.mark.unit
    def test_too_few_fake_images(self):
        """Returns error when fewer than 2 fake images."""
        _, calculate_fid, _ = _import_functions()

        fake_img = _make_pil_image()
        result = calculate_fid(
            real_images=[fake_img, fake_img],
            fake_images=[fake_img],
        )
        assert result["result"] is None
        assert "at least 2" in result["reason"]

    @pytest.mark.unit
    def test_invalid_real_images_input(self):
        """Returns error when real_images cannot be parsed."""
        _, calculate_fid, _ = _import_functions()

        result = calculate_fid(
            real_images="not-a-url",
            fake_images=[_make_pil_image(), _make_pil_image()],
        )
        assert result["result"] is None
        assert "Failed to parse real_images" in result["reason"]

    @pytest.mark.unit
    def test_invalid_fake_images_input(self):
        """Returns error when fake_images cannot be parsed."""
        _, calculate_fid, _ = _import_functions()

        result = calculate_fid(
            real_images=[_make_pil_image(), _make_pil_image()],
            fake_images="not-a-url",
        )
        assert result["result"] is None
        assert "Failed to parse fake_images" in result["reason"]

    @pytest.mark.unit
    def test_successful_fid_computation(self):
        """Returns a numeric score on successful computation."""
        _, calculate_fid, _ = _import_functions()
        import torch

        mock_fid = MagicMock()
        mock_fid.compute.return_value = torch.tensor(42.5)
        mock_fid.to.return_value = mock_fid

        imgs = [_make_pil_image() for _ in range(3)]

        with patch("torchmetrics.image.fid.FrechetInceptionDistance", return_value=mock_fid):
            result = calculate_fid(real_images=imgs, fake_images=imgs)

        assert result["result"] == pytest.approx(42.5)
        assert "42.500" in result["reason"]

    @pytest.mark.unit
    def test_fid_compute_exception(self):
        """Returns error dict when fid.compute() raises."""
        _, calculate_fid, _ = _import_functions()
        import torch

        mock_fid = MagicMock()
        mock_fid.compute.side_effect = RuntimeError("covariance singular")
        mock_fid.to.return_value = mock_fid

        imgs = [_make_pil_image() for _ in range(3)]

        with patch("torchmetrics.image.fid.FrechetInceptionDistance", return_value=mock_fid):
            result = calculate_fid(real_images=imgs, fake_images=imgs)

        assert result["result"] is None
        assert "Failed to compute FID score" in result["reason"]

    @pytest.mark.unit
    def test_fid_accepts_json_urls(self):
        """Accepts JSON string of URLs as input."""
        _, calculate_fid, _ = _import_functions()
        import torch

        fake_img = _make_pil_image()
        urls = json.dumps(["https://example.com/1.png", "https://example.com/2.png"])

        mock_fid = MagicMock()
        mock_fid.compute.return_value = torch.tensor(10.0)
        mock_fid.to.return_value = mock_fid

        with patch("tfc.utils.storage.open_image_from_url", return_value=fake_img), \
             patch("torchmetrics.image.fid.FrechetInceptionDistance", return_value=mock_fid):
            result = calculate_fid(real_images=urls, fake_images=urls)

        assert result["result"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# calculate_clip_score
# ---------------------------------------------------------------------------

class TestCalculateClipScore:
    """Unit tests for calculate_clip_score."""

    @pytest.mark.unit
    def test_single_image_single_text(self):
        """Computes score for a single image and text string."""
        _, _, calculate_clip_score = _import_functions()
        import numpy as np

        mock_model = MagicMock()
        mock_model.side_effect = lambda x: np.array([0.6, 0.8])

        mock_manager = MagicMock()
        mock_manager.image_text_model = mock_model

        img = _make_pil_image()

        with patch("agentic_eval.core.embeddings.embedding_manager.model_manager", mock_manager):
            result = calculate_clip_score(images=img, text="a red square")

        assert result["result"] is not None
        assert isinstance(result["result"], float)
        assert "CLIP score" in result["reason"]

    @pytest.mark.unit
    def test_model_not_available(self):
        """Returns error when image_text_model is None."""
        _, _, calculate_clip_score = _import_functions()

        mock_manager = MagicMock()
        mock_manager.image_text_model = None

        img = _make_pil_image()

        with patch("agentic_eval.core.embeddings.embedding_manager.model_manager", mock_manager):
            result = calculate_clip_score(images=img, text="test")

        assert result["result"] is None
        assert "not available" in result["reason"]

    @pytest.mark.unit
    def test_text_count_mismatch(self):
        """Returns error when text list length doesn't match image count."""
        _, _, calculate_clip_score = _import_functions()

        mock_manager = MagicMock()
        mock_manager.image_text_model = MagicMock()

        imgs = [_make_pil_image(), _make_pil_image(), _make_pil_image()]

        with patch("agentic_eval.core.embeddings.embedding_manager.model_manager", mock_manager):
            result = calculate_clip_score(
                images=imgs,
                text=["text1", "text2"],  # 2 texts for 3 images
            )

        assert result["result"] is None
        assert "must match" in result["reason"]

    @pytest.mark.unit
    def test_single_text_replicated_for_multiple_images(self):
        """Single text string is replicated for all images."""
        _, _, calculate_clip_score = _import_functions()
        import numpy as np

        mock_model = MagicMock()
        mock_model.side_effect = lambda x: np.array([0.6, 0.8])

        mock_manager = MagicMock()
        mock_manager.image_text_model = mock_model

        imgs = [_make_pil_image(), _make_pil_image()]

        with patch("agentic_eval.core.embeddings.embedding_manager.model_manager", mock_manager):
            result = calculate_clip_score(images=imgs, text="a red square")

        assert result["result"] is not None

    @pytest.mark.unit
    def test_invalid_images_input(self):
        """Returns error for unparseable image input."""
        _, _, calculate_clip_score = _import_functions()

        with patch("agentic_eval.core.embeddings.embedding_manager.model_manager", MagicMock()):
            result = calculate_clip_score(images="not-a-url", text="test")

        assert result["result"] is None
        assert "Failed to parse images" in result["reason"]

    @pytest.mark.unit
    def test_json_url_list_input(self):
        """Accepts JSON string of URLs as images input."""
        _, _, calculate_clip_score = _import_functions()
        import numpy as np

        fake_img = _make_pil_image()
        urls_json = json.dumps(["https://example.com/1.png", "https://example.com/2.png"])

        mock_model = MagicMock()
        mock_model.side_effect = lambda x: np.array([0.6, 0.8])

        mock_manager = MagicMock()
        mock_manager.image_text_model = mock_model

        with patch("tfc.utils.storage.open_image_from_url", return_value=fake_img), \
             patch("agentic_eval.core.embeddings.embedding_manager.model_manager", mock_manager):
            result = calculate_clip_score(images=urls_json, text="test")

        assert result["result"] is not None

    @pytest.mark.unit
    def test_json_text_list_input(self):
        """Accepts JSON string list as text input."""
        _, _, calculate_clip_score = _import_functions()
        import numpy as np

        mock_model = MagicMock()
        mock_model.side_effect = lambda x: np.array([0.6, 0.8])

        mock_manager = MagicMock()
        mock_manager.image_text_model = mock_model

        imgs = [_make_pil_image(), _make_pil_image()]

        with patch("agentic_eval.core.embeddings.embedding_manager.model_manager", mock_manager):
            result = calculate_clip_score(
                images=imgs,
                text=json.dumps(["text one", "text two"]),
            )

        assert result["result"] is not None

    @pytest.mark.unit
    def test_empty_embeddings_skipped(self):
        """Returns error when all embeddings fail."""
        _, _, calculate_clip_score = _import_functions()

        mock_model = MagicMock()
        mock_model.return_value = []  # empty embedding

        mock_manager = MagicMock()
        mock_manager.image_text_model = mock_model

        img = _make_pil_image()

        with patch("agentic_eval.core.embeddings.embedding_manager.model_manager", mock_manager):
            result = calculate_clip_score(images=img, text="test")

        assert result["result"] is None
        assert "Failed to compute CLIP scores" in result["reason"]


# ---------------------------------------------------------------------------
# Wrapper classes
# ---------------------------------------------------------------------------

class TestFidScoreWrapper:
    """Unit tests for FidScore wrapper class."""

    @pytest.mark.unit
    def test_initialization(self):
        """FidScore wrapper initializes with correct function name."""
        from agentic_eval.core_evals.fi_evals.function.wrapper import FidScore
        evaluator = FidScore()
        assert evaluator.name == "FidScore"

    @pytest.mark.unit
    def test_custom_display_name(self):
        """FidScore accepts a custom display name."""
        from agentic_eval.core_evals.fi_evals.function.wrapper import FidScore
        evaluator = FidScore(display_name="My FID")
        assert evaluator.display_name == "My FID"

    @pytest.mark.unit
    def test_default_display_name(self):
        """FidScore uses function name as default display name."""
        from agentic_eval.core_evals.fi_evals.function.wrapper import FidScore
        evaluator = FidScore()
        assert evaluator.display_name == "FidScore"


class TestClipScoreWrapper:
    """Unit tests for ClipScore wrapper class."""

    @pytest.mark.unit
    def test_initialization(self):
        """ClipScore wrapper initializes with correct function name."""
        from agentic_eval.core_evals.fi_evals.function.wrapper import ClipScore
        evaluator = ClipScore()
        assert evaluator.name == "ClipScore"

    @pytest.mark.unit
    def test_custom_display_name(self):
        """ClipScore accepts a custom display name."""
        from agentic_eval.core_evals.fi_evals.function.wrapper import ClipScore
        evaluator = ClipScore(display_name="My CLIP")
        assert evaluator.display_name == "My CLIP"

    @pytest.mark.unit
    def test_default_display_name(self):
        """ClipScore uses function name as default display name."""
        from agentic_eval.core_evals.fi_evals.function.wrapper import ClipScore
        evaluator = ClipScore()
        assert evaluator.display_name == "ClipScore"


# ---------------------------------------------------------------------------
# Operations registry
# ---------------------------------------------------------------------------

class TestOperationsRegistry:
    """Verify new evals are registered in the operations dict."""

    @pytest.mark.unit
    def test_fid_score_registered(self):
        """FidScore is registered in the operations dict."""
        from agentic_eval.core_evals.fi_evals.function.functions import operations
        assert "FidScore" in operations
        assert callable(operations["FidScore"])

    @pytest.mark.unit
    def test_clip_score_registered(self):
        """ClipScore is registered in the operations dict."""
        from agentic_eval.core_evals.fi_evals.function.functions import operations
        assert "ClipScore" in operations
        assert callable(operations["ClipScore"])
