"""TH-2356: literal TTS must speak user-role script, not control messages.

Regression for generated audio saying "record audio with script" before the
user's script. Control roles must not enter the literal speech field.
Intentional occurrences of that phrase in user content must be preserved.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Stub the Django/storage imports the extractor module loads but the script
# boundary does not use. Without this, collection fails before the regression
# can fail on the missing method.
for _name in (
    "tfc",
    "tfc.utils",
    "tfc.utils.lazy_extras",
    "tfc.utils.storage",
    "agentic_eval",
    "agentic_eval.core_evals",
    "agentic_eval.core_evals.fi_utils",
    "agentic_eval.core_evals.fi_utils.token_count_helper",
    "structlog",
):
    sys.modules.setdefault(_name, types.ModuleType(_name))
_storage = sys.modules["tfc.utils.storage"]
for _fn in (
    "audio_bytes_from_url_or_base64",
    "convert_to_mp3",
    "detect_audio_format",
    "get_audio_duration",
    "upload_audio_to_s3",
):
    setattr(_storage, _fn, None)
sys.modules["tfc.utils.lazy_extras"].av = None
sys.modules["structlog"].get_logger = lambda *_a, **_k: None
sys.modules["agentic_eval.core_evals.fi_utils.token_count_helper"].calculate_total_cost = None

_PROCESSOR = (
    Path(__file__).resolve().parents[1]
    / "agentic_eval/core_evals/run_prompt/runprompt_handlers/utils/audio_processor.py"
)
_spec = importlib.util.spec_from_file_location(
    "th2356_audio_processor", _PROCESSOR
)
_module = importlib.util.module_from_spec(_spec)
# Load only the extractor module. Full package import pulls Django/structlog
# and would hide a missing-method failure behind an environment error.
_spec.loader.exec_module(_module)
AudioProcessor = _module.AudioProcessor


PHRASE = "record audio with script"


def test_literal_speech_excludes_control_roles_and_keeps_user_script():
    messages = [
        {"role": "system", "content": PHRASE},
        {"role": "developer", "content": "developer instruction"},
        {"role": "assistant", "content": "assistant aside"},
        {"role": "tool", "content": "tool payload"},
        {"role": "user", "content": "Hello, welcome."},
    ]

    script = AudioProcessor.extract_literal_speech_script(messages)

    assert script == "Hello, welcome."
    assert PHRASE not in script
    assert "developer instruction" not in script
    assert "assistant aside" not in script
    assert "tool payload" not in script


def test_intentional_phrase_in_user_script_is_preserved():
    messages = [
        {"role": "system", "content": "Do not say this."},
        {"role": "user", "content": PHRASE},
    ]

    script = AudioProcessor.extract_literal_speech_script(messages)

    assert script == PHRASE


def test_empty_user_script_fails_even_when_control_text_exists():
    messages = [
        {"role": "system", "content": PHRASE},
        {"role": "user", "content": "   "},
    ]

    with pytest.raises(ValueError, match="Enter text to synthesize speech"):
        AudioProcessor.extract_literal_speech_script(messages)


def test_unknown_role_is_not_treated_as_user():
    messages = [
        {"role": "narrator", "content": "should not be spoken"},
        {"role": "user", "content": "spoken"},
    ]

    script = AudioProcessor.extract_literal_speech_script(messages)

    assert script == "spoken"
