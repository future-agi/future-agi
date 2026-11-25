"""
Tests for audio content handling in LLM multimodal messages.

These tests verify the fix for CORE-BACKEND-YR0: "Unsupported image format: mp3"
which occurred when audio content was incorrectly sent with type "image_url"
instead of proper audio types.

The fix involves:
1. Audio agents using "audio_content" type instead of "image_url"
2. LLM preprocessing to convert "audio_content" to provider-specific formats
3. Fallback logic skipping Anthropic for audio content (Anthropic doesn't support audio)
"""

import pytest
import copy

# Import the audio utilities directly (no Django dependency)
from agentic_eval.core.llm.audio_utils import (
    is_audio_url,
    messages_contain_audio,
    preprocess_messages_for_provider,
    AUDIO_EXTENSIONS,
    AUDIO_MIME_TYPES,
)


class TestIsAudioUrl:
    """Tests for the is_audio_url function."""

    def test_data_url_mp3(self):
        """Test data URL with mp3 audio."""
        url = "data:audio/mp3;base64,SGVsbG8gV29ybGQ="
        assert is_audio_url(url) is True

    def test_data_url_wav(self):
        """Test data URL with wav audio."""
        url = "data:audio/wav;base64,SGVsbG8gV29ybGQ="
        assert is_audio_url(url) is True

    def test_data_url_mpeg(self):
        """Test data URL with mpeg audio."""
        url = "data:audio/mpeg;base64,SGVsbG8gV29ybGQ="
        assert is_audio_url(url) is True

    def test_data_url_ogg(self):
        """Test data URL with ogg audio."""
        url = "data:audio/ogg;base64,SGVsbG8gV29ybGQ="
        assert is_audio_url(url) is True

    def test_data_url_flac(self):
        """Test data URL with flac audio."""
        url = "data:audio/flac;base64,SGVsbG8gV29ybGQ="
        assert is_audio_url(url) is True

    def test_data_url_image_not_audio(self):
        """Test data URL with image (should return False)."""
        url = "data:image/png;base64,SGVsbG8gV29ybGQ="
        assert is_audio_url(url) is False

    def test_file_url_mp3(self):
        """Test file URL with .mp3 extension."""
        url = "https://example.com/audio/sample.mp3"
        assert is_audio_url(url) is True

    def test_file_url_wav(self):
        """Test file URL with .wav extension."""
        url = "https://example.com/audio/sample.wav"
        assert is_audio_url(url) is True

    def test_file_url_mp3_uppercase(self):
        """Test file URL with uppercase .MP3 extension."""
        url = "https://example.com/audio/sample.MP3"
        assert is_audio_url(url) is True

    def test_file_url_image_not_audio(self):
        """Test file URL with image extension (should return False)."""
        url = "https://example.com/images/photo.jpg"
        assert is_audio_url(url) is False

    def test_empty_string(self):
        """Test empty string."""
        assert is_audio_url("") is False

    def test_none(self):
        """Test None input."""
        assert is_audio_url(None) is False

    def test_plain_text(self):
        """Test plain text (should return False)."""
        assert is_audio_url("just some text") is False


class TestMessagesContainAudio:
    """Tests for the messages_contain_audio function."""

    def test_audio_content_type(self):
        """Test detection of audio_content type."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this audio"},
                    {
                        "type": "audio_content",
                        "audio_content": {
                            "url": "data:audio/mp3;base64,abc",
                            "format": "mp3",
                        },
                    },
                ],
            }
        ]
        assert messages_contain_audio(messages) is True

    def test_input_audio_type(self):
        """Test detection of input_audio type (OpenAI format)."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe this"},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "abc", "format": "mp3"},
                    },
                ],
            }
        ]
        assert messages_contain_audio(messages) is True

    def test_image_url_with_audio_data_url(self):
        """Test detection of image_url containing audio data URL (legacy/incorrect usage)."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:audio/mp3;base64,abc"},
                    },
                ],
            }
        ]
        assert messages_contain_audio(messages) is True

    def test_image_url_with_actual_image(self):
        """Test that image_url with actual image returns False."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    },
                ],
            }
        ]
        assert messages_contain_audio(messages) is False

    def test_text_only_message(self):
        """Test that text-only messages return False."""
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Just a simple question"}],
            }
        ]
        assert messages_contain_audio(messages) is False

    def test_string_content(self):
        """Test that simple string content returns False."""
        messages = [{"role": "user", "content": "Just a simple question"}]
        assert messages_contain_audio(messages) is False

    def test_empty_messages(self):
        """Test empty messages list."""
        assert messages_contain_audio([]) is False

    def test_none_messages(self):
        """Test None messages."""
        assert messages_contain_audio(None) is False


class TestPreprocessMessagesForProvider:
    """Tests for the preprocess_messages_for_provider function."""

    def _create_audio_message(
        self, audio_url="data:audio/mp3;base64,SGVsbG8=", audio_format="mp3"
    ):
        """Helper to create a standard audio message."""
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this audio"},
                    {
                        "type": "audio_content",
                        "audio_content": {"url": audio_url, "format": audio_format},
                    },
                ],
            }
        ]

    def test_vertex_ai_conversion(self):
        """Test that Vertex AI converts audio_content to image_url."""
        messages = self._create_audio_message()
        result = preprocess_messages_for_provider(messages, "vertex_ai")

        assert len(result) == 1
        content = result[0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "url" in content[1]["image_url"]

    def test_openai_conversion(self):
        """Test that OpenAI converts audio_content to input_audio."""
        messages = self._create_audio_message()
        result = preprocess_messages_for_provider(messages, "openai")

        assert len(result) == 1
        content = result[0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "input_audio"
        assert "data" in content[1]["input_audio"]
        assert "format" in content[1]["input_audio"]

    def test_azure_conversion(self):
        """Test that Azure converts audio_content to input_audio consistently."""
        messages = self._create_audio_message()
        result = preprocess_messages_for_provider(messages, "azure")

        assert len(result) == 1
        content = result[0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "input_audio"

    def test_anthropic_strips_audio(self):
        """Test that Anthropic strips audio content (Anthropic doesn't support audio)."""
        messages = self._create_audio_message()
        result = preprocess_messages_for_provider(messages, "anthropic")

        assert len(result) == 1
        content = result[0]["content"]
        # Audio should be stripped, only text remains
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_unknown_provider_strips_audio(self):
        """Test that unknown providers strip audio content for safety."""
        messages = self._create_audio_message()
        result = preprocess_messages_for_provider(messages, "unknown_provider")

        assert len(result) == 1
        content = result[0]["content"]
        # Audio should be stripped, only text remains
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_preserves_non_audio_content(self):
        """Test that non-audio content is preserved."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    },
                ],
            }
        ]
        result = preprocess_messages_for_provider(messages, "openai")

        assert len(result) == 1
        content = result[0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"

    def test_empty_messages(self):
        """Test with empty messages list."""
        result = preprocess_messages_for_provider([], "openai")
        assert result == []

    def test_does_not_modify_original(self):
        """Test that the original messages are not modified."""
        messages = self._create_audio_message()
        original_copy = copy.deepcopy(messages)
        preprocess_messages_for_provider(messages, "openai")
        assert messages == original_copy

    def test_multiple_audio_blocks(self):
        """Test processing of multiple audio blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Compare these audios"},
                    {
                        "type": "audio_content",
                        "audio_content": {
                            "url": "data:audio/mp3;base64,abc",
                            "format": "mp3",
                        },
                    },
                    {
                        "type": "audio_content",
                        "audio_content": {
                            "url": "data:audio/wav;base64,def",
                            "format": "wav",
                        },
                    },
                ],
            }
        ]
        result = preprocess_messages_for_provider(messages, "openai")

        assert len(result) == 1
        content = result[0]["content"]
        assert len(content) == 3
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "input_audio"
        assert content[2]["type"] == "input_audio"


class TestRegressionCOREBACKENDYR0:
    """
    Regression tests for CORE-BACKEND-YR0: Unsupported image format: mp3.

    These tests verify that the specific error scenario is fixed.
    """

    def test_mp3_audio_not_sent_as_image_to_anthropic(self):
        """
        Test that MP3 audio content is NOT sent to Anthropic as image_url.

        This is the core of the bug: audio was being sent as image_url,
        and when fallback to Anthropic happened, Anthropic rejected it with
        "Unsupported image format: mp3".
        """
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Evaluate this audio"},
                    {
                        "type": "audio_content",
                        "audio_content": {
                            "url": "data:audio/mp3;base64,SGVsbG8gV29ybGQ=",
                            "format": "mp3",
                        },
                    },
                ],
            }
        ]

        # Process for Anthropic
        result = preprocess_messages_for_provider(messages, "anthropic")

        # Verify no image_url or audio_content blocks remain
        content = result[0]["content"]
        for block in content:
            block_type = block.get("type", "")
            assert block_type != "image_url", (
                "image_url should not be present for Anthropic"
            )
            assert block_type != "audio_content", (
                "audio_content should be stripped for Anthropic"
            )

    def test_audio_messages_detected_before_anthropic_fallback(self):
        """
        Test that messages_contain_audio correctly identifies audio content.

        This is used by the fallback logic to skip Anthropic when audio is present.
        """
        # Original message that would have caused the error
        messages_with_incorrect_type = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this audio"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:audio/mp3;base64,abc"},
                    },
                ],
            }
        ]

        # This should be detected as containing audio
        assert messages_contain_audio(messages_with_incorrect_type) is True

        # Corrected message format
        messages_with_correct_type = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this audio"},
                    {
                        "type": "audio_content",
                        "audio_content": {
                            "url": "data:audio/mp3;base64,abc",
                            "format": "mp3",
                        },
                    },
                ],
            }
        ]

        # This should also be detected as containing audio
        assert messages_contain_audio(messages_with_correct_type) is True

    def test_vertex_ai_audio_format_preserved(self):
        """
        Test that Vertex AI can receive audio in image_url format.

        Vertex AI uses image_url for audio content, which is why the original
        code worked for Vertex AI but failed on fallback.
        """
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this audio"},
                    {
                        "type": "audio_content",
                        "audio_content": {
                            "url": "data:audio/mp3;base64,SGVsbG8gV29ybGQ=",
                            "format": "mp3",
                        },
                    },
                ],
            }
        ]

        result = preprocess_messages_for_provider(messages, "vertex_ai")

        content = result[0]["content"]
        assert len(content) == 2
        # For Vertex AI, audio should be converted to image_url
        assert content[1]["type"] == "image_url"
        assert "data:audio/mp3" in content[1]["image_url"]["url"]
