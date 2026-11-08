"""
Literal text-to-speech prompts must not use media columns (TH-9061).

A speech API reads the prompt's text aloud. Before this fix, a prompt that
used an audio, image, images or document column sent the speech provider a
generated label ("Audio Input_0 is given below:"), the media URL, or text
derived from it. These tests drive the real renderer (populate_placeholders)
through the real dataset preview, saved column rerun, experiment row and
conditional column paths.

Fixture boundaries (synthetic data only, no network):
- Provider: ``litellm.speech`` / ``litellm.completion`` are patched and record
  what the provider would receive. ``AudioProcessor.format_audio_output`` is
  patched so no audio is uploaded.
- Media fetch: ``safe_fetch`` and ``convert_image_from_url_to_base64`` in the
  renderer are patched, so a fetch is recorded but never leaves the process.
- Media URLs are synthetic ``https://media.example/...`` addresses.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import ApiKey
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import RunPrompter
from tfc.middleware.workspace_context import set_workspace_context

TTS_MODEL = "openai/tts-1"
PREVIEW_URL = "/model-hub/develops/preview_run_prompt_column/"
SPOKEN_AUDIO_URL = "https://audio.example/th-9061-speech.mp3"
RECORDING_URL = "https://media.example/th-9061/recording.wav"
MY_RECORDING_URL = "https://media.example/th-9061/my-recording.wav"
CLIP_URL = "https://media.example/th-9061/clip.wav"
COVER_URL = "https://media.example/th-9061/cover.png"
DOC_URL = "https://media.example/th-9061/manual.pdf"
PHOTO_URLS = [
    "https://media.example/th-9061/a.png",
    "https://media.example/th-9061/b.png",
]
AUDIO_LABEL = "Audio Input_0 is given below:"
PROVIDER_KEY = "sk-th9061-synthetic"


def _silent_wav():
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 800)
    return buffer.getvalue()


SILENT_WAV = _silent_wav()


def media_error(column, data_type, model=TTS_MODEL):
    return (
        f'Text-to-speech model "{model}" reads the prompt aloud as text, '
        f'so it cannot use the {data_type} column "{column}". '
        "Remove this variable from the prompt."
    )


def text_part(text):
    return [{"type": "text", "text": text}]


def tts_config(messages, model=TTS_MODEL, template_format=None):
    run_prompt_config = {
        "modelName": model,
        "modelType": "tts",
        "voice": "alloy",
        "voice_id": "alloy",
        "format": "mp3",
    }
    if template_format:
        run_prompt_config["template_format"] = template_format
    return {
        "model": model,
        "messages": messages,
        "output_format": "audio",
        "run_prompt_config": run_prompt_config,
    }


def user_prompt(text):
    return [{"role": "user", "content": text_part(text)}]


# ----------------------------------------------------------------- fixtures


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="TH-9061 Organization")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="th9061@example.com",
        password="testpassword123",
        name="TH-9061 User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="TH-9061 Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    set_workspace_context(workspace=workspace, organization=user.organization)
    return client


@pytest.fixture
def openai_provider_key(db, organization):
    return ApiKey.objects.create(
        provider="openai", organization=organization, key=PROVIDER_KEY
    )


def add_custom_model(organization, workspace, user, model, provider):
    from model_hub.models.custom_models import CustomAIModel

    return CustomAIModel.objects.create(
        user_model_id=model,
        provider=provider,
        input_token_cost=0,
        output_token_cost=0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config={"key": "th9061-synthetic"},
    )


@pytest.fixture
def dataset(db, organization, workspace):
    ds = Dataset.objects.create(
        name="TH-9061 Media Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def row(db, dataset):
    return Row.objects.create(dataset=dataset, order=0)


_NO_CELL = object()


def add_column(dataset, row, name, data_type, value=_NO_CELL):
    column = Column.objects.create(
        name=name,
        dataset=dataset,
        data_type=data_type,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(column.id))
    dataset.save()
    if value is not _NO_CELL:
        Cell.objects.create(dataset=dataset, column=column, row=row, value=value)
    return column


@pytest.fixture
def columns(dataset, row):
    """Text and media columns of one synthetic row, by name."""
    spec = [
        ("Name", DataTypeChoices.TEXT.value, "Ada"),
        ("Script", DataTypeChoices.TEXT.value, "Hello there"),
        ("Say This", DataTypeChoices.TEXT.value, "{{ Recording }}"),
        ("clip.title", DataTypeChoices.TEXT.value, "Intro"),
        # A text child named like a dict method.
        ("clip.get", DataTypeChoices.TEXT.value, "Greeting"),
        ("Items", DataTypeChoices.TEXT.value, '[{"Recording": "a"}, {"other": 1}]'),
        ("Recording", DataTypeChoices.AUDIO.value, RECORDING_URL),
        ("My Recording", DataTypeChoices.AUDIO.value, MY_RECORDING_URL),
        ("clip.audio", DataTypeChoices.AUDIO.value, CLIP_URL),
        ("default", DataTypeChoices.AUDIO.value, RECORDING_URL),
        # Shadows the Jinja2 global range().
        ("range", DataTypeChoices.AUDIO.value, RECORDING_URL),
        ("Flag", DataTypeChoices.TEXT.value, ""),
        ("Cover", DataTypeChoices.IMAGE.value, COVER_URL),
        ("Photos", DataTypeChoices.IMAGES.value, json.dumps(PHOTO_URLS)),
        ("Doc", DataTypeChoices.DOCUMENT.value, DOC_URL),
    ]
    return {
        name: add_column(dataset, row, name, data_type, value)
        for name, data_type, value in spec
    }


@pytest.fixture
def speech_boundary():
    """Provider fixture: litellm.speech records its input; no upload happens."""
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
        AudioProcessor,
    )

    with (
        patch("litellm.speech", return_value=MagicMock(content=b"ID3 fake")) as speech,
        patch.object(
            AudioProcessor,
            "format_audio_output",
            return_value=(SPOKEN_AUDIO_URL, {"metadata": {}}),
        ),
    ):
        yield speech


@pytest.fixture
def media_fetch():
    """Media fixture: the renderer's fetches are recorded, never sent."""
    fetched = MagicMock(content=SILENT_WAV)
    fetched.raise_for_status.return_value = None
    with (
        patch("model_hub.views.run_prompt.safe_fetch", return_value=fetched) as fetch,
        patch(
            "model_hub.views.run_prompt.convert_image_from_url_to_base64",
            return_value="data:image/png;base64,AAAA",
        ) as image,
        patch(
            "model_hub.views.run_prompt.handle_media",
            side_effect=lambda item, model_name: item,
        ) as inline,
    ):
        yield SimpleNamespace(audio=fetch, image=image, inline=inline)


def preview(client, dataset, config):
    return client.post(
        PREVIEW_URL,
        {
            "dataset_id": str(dataset.id),
            "name": "TTS Preview",
            "first_n_rows": 1,
            "config": config,
        },
        format="json",
    )


# ------------------------------------------------- dataset preview (Jinja2)

# (template, column, data type). Preview renders with the default Jinja2.
PREVIEW_REJECTED = [
    ("{{Recording}}", "Recording", "audio"),
    ("Say {{ Recording }} now", "Recording", "audio"),
    ("{{ Recording|upper }}", "Recording", "audio"),
    ("{{ Recording[0:8] }}", "Recording", "audio"),
    ("{{ Recording|length }}", "Recording", "audio"),
    ("{{ Recording|reverse|join('') }}", "Recording", "audio"),
    ("{{ Recording | default('x') }}", "Recording", "audio"),
    ("{{ Recording|int }}", "Recording", "audio"),
    ("{{ Recording is defined }}", "Recording", "audio"),
    ("{% if Recording %}yes{% endif %}", "Recording", "audio"),
    ("{{ Recording.split('/')[-1] }}", "Recording", "audio"),
    ("{% set r = Recording %}{{ r }}", "Recording", "audio"),
    ("{{ range }}", "range", "audio"),
    ("{{ range|upper }} {{ Missing.value }}", "range", "audio"),
    ("{{ My Recording }}", "My Recording", "audio"),
    ("{{ Say This }}", "Recording", "audio"),
    ("{{clip.audio}}", "clip.audio", "audio"),
    ("{{ clip['audio'] }}", "clip.audio", "audio"),
    ("{{ clip }}", "clip.audio", "audio"),
    ("{{ clip.get('audio') }}", "clip.audio", "audio"),
    ("{{ clip.items() | list }}", "clip.audio", "audio"),
    (
        "{{ clip.title }} {% for n in [1] %}{% if false %}{% set clip = {} %}"
        "{% endif %}{{ clip.audio.upper() }}{% endfor %}",
        "clip.audio",
        "audio",
    ),
    ("{{ Cover|string }}", "Cover", "image"),
    ("{{Photos}}", "Photos", "images"),
    ("{{ Photos[0] }}", "Photos", "images"),
    ("{% for p in Photos %}{{ p }}{% endfor %}", "Photos", "images"),
    ("{{Doc}}", "Doc", "document"),
    ("Read {{ Doc }}", "Doc", "document"),
]


@pytest.mark.django_db
class TestPreviewRejectsMediaColumns:
    """Dataset preview: the real route, renderer and TTS routing."""

    @pytest.mark.parametrize(("template", "column", "data_type"), PREVIEW_REJECTED)
    def test_media_reference_is_rejected_before_fetch_and_speech(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
        template,
        column,
        data_type,
    ):
        response = preview(auth_client, dataset, tts_config(user_prompt(template)))

        speech_boundary.assert_not_called()
        media_fetch.audio.assert_not_called()
        media_fetch.image.assert_not_called()
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["responses"] == [
            media_error(column, data_type)
        ]

    def test_reference_by_column_id_is_rejected(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        template = "{{" + str(columns["Recording"].id) + "}}"

        response = preview(auth_client, dataset, tts_config(user_prompt(template)))

        speech_boundary.assert_not_called()
        assert response.json()["result"]["responses"] == [
            media_error("Recording", "audio")
        ]
        media_fetch.audio.assert_not_called()

    @pytest.mark.parametrize(
        "model",
        [
            "gpt-4o-mini-tts",
            # A custom speech provider (CustomTTSHandler) reads the text too.
            "neuphonic-tts",
            # An organization chat-mode model with audio output uses TTSHandler.
            "th9061-voice",
            # The speech endpoint is called first, before any completion retry.
            "openai/gpt-4o-audio-preview",
        ],
    )
    def test_every_literal_speech_lane_rejects(
        self,
        auth_client,
        organization,
        workspace,
        user,
        dataset,
        columns,
        speech_boundary,
        media_fetch,
        model,
    ):
        if model in ("th9061-voice", "openai/gpt-4o-audio-preview"):
            add_custom_model(organization, workspace, user, model, "openai")

        response = preview(
            auth_client,
            dataset,
            tts_config(user_prompt("Play {{ Recording }}"), model=model),
        )

        speech_boundary.assert_not_called()
        assert response.json()["result"]["responses"] == [
            media_error("Recording", "audio", model=model)
        ]
        media_fetch.audio.assert_not_called()

    def test_inline_media_is_not_processed_before_the_rejection(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        content = [
            {"type": "audio_url", "audio_url": {"url": RECORDING_URL}},
            {"type": "text", "text": "{{ Recording }}"},
        ]
        messages = [{"role": "user", "content": content}]

        response = preview(auth_client, dataset, tts_config(messages))

        speech_boundary.assert_not_called()
        assert response.json()["result"]["responses"] == [
            media_error("Recording", "audio")
        ]
        media_fetch.inline.assert_not_called()

    def test_system_message_reference_is_rejected(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        # Literal speech reads every message today, the system one included.
        messages = [
            {"role": "system", "content": text_part("Context: {{Recording}}")},
            {"role": "user", "content": text_part("Hello {{ Name }}")},
        ]

        response = preview(auth_client, dataset, tts_config(messages))

        speech_boundary.assert_not_called()
        assert response.json()["result"]["responses"] == [
            media_error("Recording", "audio")
        ]
        media_fetch.audio.assert_not_called()

    def test_string_content_is_rejected(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        messages = [{"role": "user", "content": "Play {{ Recording }}"}]

        response = preview(auth_client, dataset, tts_config(messages))

        speech_boundary.assert_not_called()
        assert response.json()["result"]["responses"] == [
            media_error("Recording", "audio")
        ]

    @pytest.mark.parametrize("cell_value", ["", None, _NO_CELL])
    def test_empty_or_missing_media_cell_is_rejected(
        self,
        auth_client,
        dataset,
        row,
        openai_provider_key,
        speech_boundary,
        media_fetch,
        cell_value,
    ):
        add_column(dataset, row, "Name", DataTypeChoices.TEXT.value, "Ada")
        add_column(dataset, row, "Recording", DataTypeChoices.AUDIO.value, cell_value)

        response = preview(
            auth_client,
            dataset,
            tts_config(user_prompt("Hello {{ Name }}, {{Recording}}")),
        )

        speech_boundary.assert_not_called()
        assert response.json()["result"]["responses"] == [
            media_error("Recording", "audio")
        ]

    def test_media_read_through_a_json_parent_column_id_is_rejected(
        self,
        auth_client,
        dataset,
        row,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        # A JSON text column "take" holds the dotted media column take.audio,
        # so the media value is also reachable as <id of take>.audio.
        take = add_column(
            dataset, row, "take", DataTypeChoices.TEXT.value, '{"title": "Intro"}'
        )
        add_column(dataset, row, "take.audio", DataTypeChoices.AUDIO.value, CLIP_URL)
        alias = "_" * str(take.id)[0].isdigit() + str(take.id).replace("-", "_")

        rejected = preview(
            auth_client,
            dataset,
            tts_config(user_prompt("{{ " + alias + ".audio }}")),
        )
        speech_boundary.assert_not_called()
        assert rejected.json()["result"]["responses"] == [
            media_error("take.audio", "audio")
        ]

        spoken = preview(
            auth_client,
            dataset,
            tts_config(user_prompt("{{ take.title }} {{ take }}")),
        )
        assert spoken.json()["result"]["responses"] == [SPOKEN_AUDIO_URL]
        # The JSON column prints as its own text, never with the media child.
        assert speech_boundary.call_args.kwargs["input"] == 'Intro {"title": "Intro"}'
        media_fetch.audio.assert_not_called()

    def test_group_with_only_a_media_value_keeps_text_defaults(
        self,
        auth_client,
        dataset,
        row,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        add_column(dataset, row, "grp.audio", DataTypeChoices.AUDIO.value, CLIP_URL)
        add_column(dataset, row, "grp.title", DataTypeChoices.TEXT.value)  # no cell

        response = preview(
            auth_client,
            dataset,
            tts_config(user_prompt("{{ grp.title | default('Hello') }}")),
        )

        assert response.json()["result"]["responses"] == [SPOKEN_AUDIO_URL]
        assert speech_boundary.call_args.kwargs["input"] == "Hello"
        media_fetch.audio.assert_not_called()

    def test_unreadable_message_content_fails_closed(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        content = [
            {"type": "text", "text": None},
            {"type": "text", "text": "Hello {{ Name }}"},
        ]

        response = preview(
            auth_client,
            dataset,
            tts_config([{"role": "user", "content": content}]),
        )

        speech_boundary.assert_not_called()
        [message] = response.json()["result"]["responses"]
        assert message.startswith(
            f'Text-to-speech model "{TTS_MODEL}" cannot read this prompt: '
            "its template is invalid (TypeError:"
        )

    def test_several_media_columns_are_named(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        response = preview(
            auth_client,
            dataset,
            tts_config(user_prompt("{{ Recording }} and {{ Photos }}")),
        )

        speech_boundary.assert_not_called()
        assert response.json()["result"]["responses"] == [
            f'Text-to-speech model "{TTS_MODEL}" reads the prompt aloud as text, '
            'so it cannot use the media columns "Recording" (audio), '
            '"Photos" (images). Remove these variables from the prompt.'
        ]

    def test_unreadable_template_is_rejected_not_spoken_as_written(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        response = preview(
            auth_client,
            dataset,
            tts_config(user_prompt("Hello {{ Recording }} {% if %}")),
        )

        [message] = response.json()["result"]["responses"]
        assert message.startswith(
            f'Text-to-speech model "{TTS_MODEL}" cannot read this prompt: '
            "its template is invalid (TemplateSyntaxError:"
        )
        assert message.endswith("Fix the template.")
        speech_boundary.assert_not_called()
        media_fetch.audio.assert_not_called()


# (template, exact text the speech provider receives)
PREVIEW_SPOKEN = [
    ("Hello {{ Name }}", "Hello Ada"),
    ('{{ "Recording" }} is a word', "Recording is a word"),
    ("{% raw %}{{ Recording }}{% endraw %}", "{{ Recording }}"),
    ("{{ clip.title }}", "Intro"),
    ("{{ clip['caption'] | default('Hello') }}", "Hello"),
    ("{{ clip.subtitle | default('Hi') }}", "Hi"),
    # Template-local names shadow the dataset's media columns.
    (
        "{{ clip.title }} {% with clip = {'audio': 'Hello'} %}"
        "{{ clip.audio }}{% endwith %}",
        "Intro Hello",
    ),
    ("{% for Recording in ['a', 'b'] %}{{ Recording }}{% endfor %}", "ab"),
    (
        "{{ clip.title }} {% set clip = {'audio': 'Hello'} %}{{ clip.audio }}",
        "Intro Hello",
    ),
    ('{{ Script | default("Hello") }}', "Hello there"),
    ("record audio with script", "record audio with script"),
]


@pytest.mark.django_db
class TestPreviewStillSpeaksText:
    """Text, quoted words and text variables still reach the speech API."""

    @pytest.mark.parametrize(("template", "spoken"), PREVIEW_SPOKEN)
    def test_text_prompt_is_spoken(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
        template,
        spoken,
    ):
        response = preview(auth_client, dataset, tts_config(user_prompt(template)))

        assert response.json()["result"]["responses"] == [SPOKEN_AUDIO_URL]
        speech_boundary.assert_called_once()
        assert speech_boundary.call_args.kwargs["input"] == spoken
        assert speech_boundary.call_args.kwargs["model"] == TTS_MODEL
        media_fetch.audio.assert_not_called()

    def test_messages_are_spoken_in_order(
        self,
        auth_client,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        messages = [
            {"role": "user", "content": text_part("First {{ Name }}.")},
            {"role": "user", "content": text_part("Then {{ clip.title }}.")},
        ]

        response = preview(auth_client, dataset, tts_config(messages))

        assert response.json()["result"]["responses"] == [SPOKEN_AUDIO_URL]
        assert speech_boundary.call_args.kwargs["input"] == "First Ada. Then Intro."

    def test_completion_audio_model_keeps_its_media_handling(
        self,
        auth_client,
        organization,
        workspace,
        user,
        dataset,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        # gpt-4o-audio-preview (an organization model) is a completion-audio
        # model, not literal speech: the renderer's existing media handling
        # applies unchanged, including its audio-input capability check.
        add_custom_model(
            organization, workspace, user, "gpt-4o-audio-preview", "openai"
        )
        with patch("litellm.completion") as completion:
            response = preview(
                auth_client,
                dataset,
                tts_config(
                    user_prompt("Describe {{Recording}}"),
                    model="gpt-4o-audio-preview",
                ),
            )

        assert response.json()["result"]["responses"] == [
            "Model gpt-4o-audio-preview does not support audio input."
        ]
        completion.assert_not_called()
        speech_boundary.assert_not_called()


# ------------------------------------------- saved column, every template format


def saved_tts_column(dataset, organization, workspace, row, messages, template_format):
    run_prompter = RunPrompter.objects.create(
        name="TTS Column",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.NOT_STARTED.value,
        model=TTS_MODEL,
        messages=messages,
        output_format="audio",
        run_prompt_config=tts_config([], template_format=template_format)[
            "run_prompt_config"
        ],
    )
    column = Column.objects.create(
        name="TTS Column",
        dataset=dataset,
        data_type=DataTypeChoices.AUDIO.value,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=str(run_prompter.id),
    )
    dataset.column_order.append(str(column.id))
    dataset.save()
    return run_prompter, column


def rerun_saved_column(run_prompter, column, row):
    from model_hub.views.run_prompt import RunPrompts

    with (
        patch("model_hub.views.run_prompt.log_and_deduct_cost_for_api_request", None),
        patch("model_hub.views.run_prompt.close_old_connections"),
    ):
        runner = RunPrompts(run_prompter.id)
        runner.load_run_prompt_id()
        runner.process_row(row, column, edit_mode=True)
    return Cell.objects.get(dataset=column.dataset, column=column, row=row)


# (template format, template, column, data type)
SAVED_REJECTED = [
    ("jinja", "{{ Recording|upper }}", "Recording", "audio"),
    ("jinja", "{{ Photos[1] }}", "Photos", "images"),
    ("mustache", "{{Recording}}", "Recording", "audio"),
    ("mustache", "Say {{ Recording }}", "Recording", "audio"),
    ("mustache", "{{{Recording}}}", "Recording", "audio"),
    ("mustache", "{{#Recording}}present{{/Recording}}", "Recording", "audio"),
    ("mustache", "{{^Recording}}absent{{/Recording}}", "Recording", "audio"),
    ("mustache", "{{clip.audio}}", "clip.audio", "audio"),
    ("mustache", "{{#clip}}{{audio}}{{/clip}}", "clip.audio", "audio"),
    ("mustache", "Hello {{.}}", "Recording", "audio"),
    ("mustache", "{{^Flag}}{{.}}{{/Flag}}", "Recording", "audio"),
    ("f-string", "{Recording}", "Recording", "audio"),
    ("f-string", "{Recording[0]}", "Recording", "audio"),
    ("f-string", "{Recording!r}", "Recording", "audio"),
    ("f-string", "{Recording:.8}", "Recording", "audio"),
    ("f-string", "{Name:{Recording}}", "Recording", "audio"),
    ("f-string", "{My Recording}", "My Recording", "audio"),
    ("f-string", "{clip[audio]}", "clip.audio", "audio"),
    ("f-string", "{clip.get.__self__[audio]}", "clip.audio", "audio"),
    ("mustache", "{{#Items}}{{Recording}}{{/Items}}", "Recording", "audio"),
    ("f-string", "{Doc}", "Doc", "document"),
]


@pytest.mark.django_db
class TestSavedColumnRejectsMediaColumns:
    """Saved column rerun (worker path) for each template format."""

    @pytest.mark.parametrize(
        ("template_format", "template", "column_name", "data_type"), SAVED_REJECTED
    )
    def test_media_reference_fails_the_row_without_speech(
        self,
        dataset,
        organization,
        workspace,
        row,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
        template_format,
        template,
        column_name,
        data_type,
    ):
        messages = user_prompt(template)
        run_prompter, column = saved_tts_column(
            dataset, organization, workspace, row, messages, template_format
        )
        stored_messages = json.loads(json.dumps(run_prompter.messages))

        cell = rerun_saved_column(run_prompter, column, row)

        speech_boundary.assert_not_called()
        if "{{.}}" in template:
            # The whole context is read: the first media value reached is named.
            assert cell.value.startswith(
                f'Text-to-speech model "{TTS_MODEL}" reads the prompt aloud as '
                "text, so it cannot use the "
            )
        else:
            assert cell.value == media_error(column_name, data_type)
        assert cell.status == CellStatus.ERROR.value
        media_fetch.audio.assert_not_called()
        media_fetch.image.assert_not_called()
        run_prompter.refresh_from_db()
        assert run_prompter.messages == stored_messages

    @pytest.mark.parametrize(
        ("template_format", "template", "error"),
        [
            ("jinja", "{{ Recording }} {% if %}", "TemplateSyntaxError"),
            ("mustache", "{{#Recording}}unclosed", "ChevronError"),
            ("f-string", "{Recording", "ValueError"),
        ],
    )
    def test_unreadable_template_fails_the_row(
        self,
        dataset,
        organization,
        workspace,
        row,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
        template_format,
        template,
        error,
    ):
        run_prompter, column = saved_tts_column(
            dataset,
            organization,
            workspace,
            row,
            user_prompt(template),
            template_format,
        )

        cell = rerun_saved_column(run_prompter, column, row)

        speech_boundary.assert_not_called()
        assert cell.status == CellStatus.ERROR.value
        assert cell.value.startswith(
            f'Text-to-speech model "{TTS_MODEL}" cannot read this prompt: '
            f"its template is invalid ({error}:"
        )
        speech_boundary.assert_not_called()

    def test_mustache_section_scope_shadows_a_media_column(
        self,
        dataset,
        organization,
        workspace,
        row,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        # chevron finds "Recording" in the Lines section's JSON value first.
        add_column(
            dataset, row, "Lines", DataTypeChoices.TEXT.value, '{"Recording": "Hello"}'
        )
        run_prompter, column = saved_tts_column(
            dataset,
            organization,
            workspace,
            row,
            user_prompt("{{#Lines}}{{ Recording }}{{/Lines}}"),
            "mustache",
        )

        cell = rerun_saved_column(run_prompter, column, row)

        assert cell.status == CellStatus.PASS.value
        assert speech_boundary.call_args.kwargs["input"] == "Hello"
        media_fetch.audio.assert_not_called()

    @pytest.mark.parametrize(
        ("template_format", "template", "spoken"),
        [
            ("jinja", "Hello {{ Name }}", "Hello Ada"),
            ("mustache", "Hello {{Name}} from {{clip.title}}", "Hello Ada from Intro"),
            ("f-string", "Hello {Name} from {clip[title]}", "Hello Ada from Intro"),
        ],
    )
    def test_text_variables_are_spoken(
        self,
        dataset,
        organization,
        workspace,
        row,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
        template_format,
        template,
        spoken,
    ):
        run_prompter, column = saved_tts_column(
            dataset,
            organization,
            workspace,
            row,
            user_prompt(template),
            template_format,
        )

        cell = rerun_saved_column(run_prompter, column, row)

        assert cell.status == CellStatus.PASS.value
        assert cell.value == SPOKEN_AUDIO_URL
        speech_boundary.assert_called_once()
        assert speech_boundary.call_args.kwargs["input"] == spoken


# ------------------------------------------------------- experiment row


@pytest.mark.django_db
class TestExperimentRowRejectsMediaColumns:
    """Experiment rows (_process_row_impl, used by the V1 and V2 workflows)."""

    def _run(self, dataset, row, template):
        from model_hub.views.experiment_runner import _process_row_impl

        output = Column.objects.create(
            name="Experiment TTS",
            dataset=dataset,
            data_type=DataTypeChoices.AUDIO.value,
            source=SourceChoices.EXPERIMENT.value,
        )
        experiment = ExperimentsTable.objects.create(
            name="TH-9061 Experiment",
            dataset=dataset,
            column=output,
            status=StatusType.RUNNING.value,
        )
        config = tts_config([])
        with patch("model_hub.views.experiment_runner.close_old_connections"):
            _process_row_impl(
                row_id=row.id,
                column_id=output.id,
                dataset_id=dataset.id,
                experiment_id=experiment.id,
                messages=user_prompt(template),
                model=TTS_MODEL,
                model_config={"template_format": "jinja"},
                output_format="audio",
                run_prompt_config=config["run_prompt_config"],
                skip_celery_evals=True,
            )
        return Cell.objects.get(column=output, row=row)

    def test_media_reference_fails_the_cell(
        self, dataset, row, columns, openai_provider_key, speech_boundary, media_fetch
    ):
        cell = self._run(dataset, row, "Narrate {{ Recording }}")

        speech_boundary.assert_not_called()
        assert cell.status == CellStatus.ERROR.value
        assert cell.value == media_error("Recording", "audio")
        assert json.loads(cell.value_infos)["reason"] == media_error(
            "Recording", "audio"
        )
        media_fetch.audio.assert_not_called()

    def test_runner_process_row_fails_the_cell(
        self, dataset, row, columns, openai_provider_key, speech_boundary, media_fetch
    ):
        # ExperimentRunner.process_row, the instance-method row path.
        from model_hub.views.experiment_runner import ExperimentRunner

        output = Column.objects.create(
            name="Experiment TTS",
            dataset=dataset,
            data_type=DataTypeChoices.AUDIO.value,
            source=SourceChoices.EXPERIMENT.value,
        )
        experiment = ExperimentsTable.objects.create(
            name="TH-9061 Runner Experiment",
            dataset=dataset,
            column=output,
            status=StatusType.RUNNING.value,
        )
        runner = ExperimentRunner(experiment.id)
        runner.load_experiment()
        with patch("model_hub.views.experiment_runner.close_old_connections"):
            runner.process_row(
                row,
                output,
                user_prompt("{{ Recording|upper }}"),
                TTS_MODEL,
                {"template_format": "jinja"},
                output_format="audio",
                run_prompt_config=tts_config([])["run_prompt_config"],
            )

        cell = Cell.objects.get(column=output, row=row)
        speech_boundary.assert_not_called()
        assert cell.status == CellStatus.ERROR.value
        assert cell.value == media_error("Recording", "audio")
        media_fetch.audio.assert_not_called()

    def test_text_prompt_is_spoken(
        self, dataset, row, columns, openai_provider_key, speech_boundary, media_fetch
    ):
        cell = self._run(dataset, row, "Hello {{ Name }}")

        assert cell.status == CellStatus.PASS.value
        assert cell.value == SPOKEN_AUDIO_URL
        assert speech_boundary.call_args.kwargs["input"] == "Hello Ada"


# ------------------------------------------------------- conditional column


@pytest.mark.django_db
class TestConditionalBranchReportsTheRejection:
    """A conditional column's run_prompt branch reports the error as the row's."""

    def test_media_reference_is_the_rows_error(
        self,
        dataset,
        organization,
        row,
        columns,
        openai_provider_key,
        speech_boundary,
        media_fetch,
    ):
        from model_hub.views.dynamic_columns import ConditionalColumnView

        config = tts_config(user_prompt("{{ Recording|upper }}"))
        branch = {
            "branch_type": "else",
            "condition": "",
            "branch_node_config": {"type": "run_prompt", "config": config},
        }

        value, value_infos = ConditionalColumnView()._process_row(
            row, [branch], org_id=organization.id
        )

        speech_boundary.assert_not_called()
        assert value == media_error("Recording", "audio")
        assert value_infos == {"reason": media_error("Recording", "audio")}
        media_fetch.audio.assert_not_called()


# ------------------------------------------------------- routing predicate


@pytest.mark.django_db
class TestLiteralSpeechRouting:
    """The guard follows RunPrompt's routing, so completion lanes are untouched."""

    @pytest.mark.parametrize(
        ("model", "output_format", "literal"),
        [
            (TTS_MODEL, "audio", True),
            ("openai/tts-1-hd", "audio", True),
            ("gpt-4o-mini-tts", "audio", True),
            ("eleven_multilingual_v2", "audio", True),
            ("neuphonic-tts", "audio", True),
            (TTS_MODEL, "string", False),
            (TTS_MODEL, None, False),
            ("vertex_ai/gemini-2.5-flash-preview-tts", "audio", False),
            ("whisper-1", "audio", False),
            ("", "audio", False),
        ],
    )
    def test_catalog_lane(self, organization, model, output_format, literal):
        from model_hub.views.run_prompt import is_literal_speech_run

        assert is_literal_speech_run(model, output_format, organization.id) is literal

    @pytest.mark.parametrize(
        ("model", "provider", "literal"),
        [
            ("gpt-4o-audio-preview", "openai", False),
            ("gpt-4o-audio-preview-2024-12-17", "openai", False),
            # SpeechAPIHandler sends the text to the speech endpoint first and
            # only retries through completion after an error.
            ("openai/gpt-4o-audio-preview", "openai", True),
            ("azure/gpt-4o-audio-preview-voice", "azure", True),
            # A custom speech provider wins over a completion-looking name.
            ("gpt-4o-audio-preview", "neuphonic", True),
            # Audio output routes any chat-mode model to TTSHandler.
            ("th9061-voice", "openai", True),
        ],
    )
    def test_organization_model_lane(
        self, organization, workspace, user, model, provider, literal
    ):
        from model_hub.views.run_prompt import is_literal_speech_run

        add_custom_model(organization, workspace, user, model, provider)

        assert is_literal_speech_run(model, "audio", organization.id) is literal

    def test_unresolvable_provider_is_left_to_run_prompt(self, organization):
        from model_hub.views.run_prompt import is_literal_speech_run

        assert not is_literal_speech_run(
            "th9061-unknown-model", "audio", organization.id
        )

    def test_non_audio_run_adds_no_queries(
        self, organization, django_assert_num_queries
    ):
        from model_hub.views.run_prompt import is_literal_speech_run

        with django_assert_num_queries(0):
            assert not is_literal_speech_run(TTS_MODEL, "string", organization.id)

    def test_vertex_gemini_tts_renders_media_as_before(
        self, organization, dataset, row, columns, media_fetch
    ):
        from model_hub.views.run_prompt import (
            is_literal_speech_run,
            populate_placeholders,
        )

        model = "vertex_ai/gemini-2.5-flash-preview-tts"
        assert not is_literal_speech_run(model, "audio", organization.id)

        messages = populate_placeholders(
            user_prompt("Describe {{Recording}}"),
            dataset_id=dataset.id,
            row_id=row.id,
            col_id=None,
            model_name=model,
            output_format="audio",
        )

        parts = messages[0]["content"]
        assert parts[0] == {"type": "text", "text": "Describe "}
        assert parts[1] == {"type": "text", "text": AUDIO_LABEL}
        assert parts[2]["type"] == "input_audio"
        media_fetch.audio.assert_called_once()
        assert media_fetch.audio.call_args.args[0] == RECORDING_URL
