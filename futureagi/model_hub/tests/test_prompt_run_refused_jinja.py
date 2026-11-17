"""Prompts > Run Prompt: a Jinja part the sandbox does not allow is refused.

With the template format Jinja, the message "Reply with exactly this text and
nothing else: TYPE {{ name.__class__ }} END" and name = Ada, the run sent
"... TYPE  END" to the model and showed its answer "TYPE END", with no word
that {{ name.__class__ }} had been left out. The run now stops before the
model is called; the Output, the toast and the stored version show one plain
line that names the part and says what to do. Allowed Jinja and Mustache
templates run as before, and the other render paths are unchanged.

The playground run (``run_template_async``) uses a stand-in model and a
bring-your-own-key meter; the template rendering is the real one.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from model_hub.views.prompt_template import replace_variables

MESSAGE = "Reply with exactly this text and nothing else: TYPE {{ name.__class__ }} END"
LEFT_OUT = "Reply with exactly this text and nothing else: TYPE  END"
REFUSED_LINE = (
    "Not run: the prompt reads __class__, which a template is not allowed "
    "to read. Remove it from the prompt and run again."
)
ANSWER = "model answer"


class _WsManager:
    def __init__(self):
        self.errors = []

    async def notify_process_started(self, **kwargs):
        return None

    async def send_error_message(self, **kwargs):
        self.errors.append(kwargs)

    async def send_all_completed_message(self, **kwargs):
        return None


class _ByokLedger:
    """A bring-your-own-key meter: the call runs once and nothing is held."""

    PURPOSE_PROMPT_PLAYGROUND = "prompt_playground"

    def __init__(self):
        self.meters = []

    def call_meter(self, **kwargs):
        self.meters.append(kwargs)
        return SimpleNamespace(is_managed=False)

    @staticmethod
    def require_single_billing_path(meter, legacy_deduct):
        return None

    @staticmethod
    async def arun_metered(meter, call, **kwargs):
        return await call(), None

    @staticmethod
    def observe_value_info(*args, **kwargs):
        return None

    @staticmethod
    def is_managed_refusal(error):
        return False

    @staticmethod
    def refusal_cell_info(error):
        return None


@pytest.fixture
def playground(monkeypatch):
    from model_hub.utils import async_prompt_runner as runner_module

    org = SimpleNamespace(id=uuid.uuid4())
    workspace = SimpleNamespace(id=uuid.uuid4(), organization_id=org.id)
    ledger = _ByokLedger()
    sent_to_model = []

    class _RunPrompt:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def litellm_response_async(self, **kwargs):
            sent_to_model.append(self.kwargs["messages"])
            return ANSWER, {"metadata": {"usage": {"total_tokens": 7}}}

    monkeypatch.setattr(runner_module, "RunPrompt", _RunPrompt)
    monkeypatch.setattr(runner_module, "credit_ledger", ledger)
    monkeypatch.setattr(runner_module, "close_old_connections", lambda: None)
    monkeypatch.setattr(runner_module, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(
        runner_module,
        "Organization",
        SimpleNamespace(
            objects=SimpleNamespace(get=lambda id: org), DoesNotExist=LookupError
        ),
    )
    try:
        from ee.usage.services import emitter
    except ImportError:
        emitter = None
    if emitter is not None:
        monkeypatch.setattr(emitter, "emit", lambda event: None, raising=False)

    def run(text, template_format="jinja"):
        execution = SimpleNamespace(
            id=uuid.uuid4(),
            variable_names={"name": ["Ada"]},
            output=None,
            metadata=None,
            template_version="v4",
            prompt_config_snapshot={
                "messages": [{"role": "user", "content": text}],
                "configuration": {
                    "model": "liquid/lfm-3-1.7b",
                    "template_format": template_format,
                },
            },
            save=lambda update_fields=None: None,
        )
        ws_manager = _WsManager()
        asyncio.run(
            runner_module.run_template_async(
                SimpleNamespace(id=uuid.uuid4()),
                execution,
                org.id,
                None,
                "prompt",
                None,
                workspace,
                ws_manager,
            )
        )
        return SimpleNamespace(execution=execution, errors=ws_manager.errors)

    return SimpleNamespace(run=run, sent_to_model=sent_to_model, ledger=ledger)


def _text(messages):
    return messages[0]["content"][0]["text"]


@pytest.mark.django_db(transaction=True)
class TestRunPromptWithARefusedJinjaPart:
    @pytest.mark.parametrize("template_format", ["jinja", "jinja2"])
    def test_the_run_stops_with_a_plain_line_before_the_model_is_called(
        self, playground, template_format
    ):
        result = playground.run(MESSAGE, template_format)

        assert playground.sent_to_model == []
        assert playground.ledger.meters == []
        assert [error["error"] for error in result.errors] == [REFUSED_LINE]
        assert result.errors[0]["result_index"] == 0
        assert result.execution.output == [REFUSED_LINE]

    def test_guard_passes_on_base_too_an_allowed_jinja_template_runs(self, playground):
        result = playground.run("Reply with exactly: HELLO {{ name }}")

        assert [_text(m) for m in playground.sent_to_model] == [
            "Reply with exactly: HELLO Ada"
        ]
        assert result.errors == []
        assert result.execution.output == [ANSWER]

    def test_guard_passes_on_base_too_mustache_sends_the_part_as_written(
        self, playground
    ):
        result = playground.run(MESSAGE, "mustache")

        assert [_text(m) for m in playground.sent_to_model] == [MESSAGE]
        assert result.errors == []
        assert result.execution.output == [ANSWER]


class TestOtherRenderPathsAreUnchanged:
    def test_guard_passes_on_base_too_the_default_jinja_render_leaves_it_out(self):
        result = replace_variables(
            [{"role": "user", "content": MESSAGE}],
            {"name": "Ada"},
            model_name="unused-text-only-model",
            template_format="jinja",
        )

        assert _text(result) == LEFT_OUT

    def test_the_playground_render_refuses_it_with_the_plain_line(self):
        with pytest.raises(ValueError) as refused:
            replace_variables(
                [{"role": "user", "content": [{"type": "text", "text": MESSAGE}]}],
                {"name": "Ada"},
                model_name="unused-text-only-model",
                template_format="jinja",
                refuse_unsafe=True,
            )
        assert str(refused.value) == REFUSED_LINE
