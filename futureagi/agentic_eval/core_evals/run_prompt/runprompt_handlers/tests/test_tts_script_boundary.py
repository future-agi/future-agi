"""TH-2356: literal TTS speaks user-role script, not control messages.

Runs inside the app test environment. Does not stub structlog or other
shared modules: a collection-time stub leaked into the rest of the suite
and broke CI (`NoneType` has no attribute `debug`).
"""

import pytest

from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
    AudioProcessor,
)

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


def test_intentional_phrase_in_user_script_is_preserved():
    messages = [
        {"role": "system", "content": "Do not say this."},
        {"role": "user", "content": PHRASE},
    ]

    assert AudioProcessor.extract_literal_speech_script(messages) == PHRASE


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

    assert AudioProcessor.extract_literal_speech_script(messages) == "spoken"
