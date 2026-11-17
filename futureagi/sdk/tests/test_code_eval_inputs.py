"""Code evals on the SDK evaluation route take the inputs their code reads.

A code eval template stores no required_keys: its inputs are the parameters of
its evaluate() function (or main()), which the code executor calls with every
input as a keyword argument. The app's variable-mapping form reads the same
signature. A user's code eval whose template also declares required_keys takes
those keys too, as that form offers them; other templates take only the keys
they declare. A refusal names what the template accepts and how to fix the call.
The code runner passes its own code and language apart from the inputs, so a
parameter named code or language is an input like any other. The form never
offers context, self or cls, so these names are not taken from the code.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from model_hub.models.evals_metric import EvalTemplate

pytestmark = pytest.mark.django_db

CREATE_URL = "/model-hub/eval-templates/create-v2/"
SDK_EVAL_URL = "/sdk/api/v1/new-eval/"
EXECUTOR = "agentic_eval.core_evals.fi_utils.sandbox._call_executor_service"

KWARGS_CODE = (
    "def evaluate(input, output, expected, context, **kwargs):\n"
    "    return output == expected\n"
)
NAMED_CODE = "def evaluate(output, expected):\n    return output == expected\n"
MAIN_CODE = "def main(answer):\n    return bool(answer)\n"
NO_PARAMS_CODE = "def evaluate():\n    return True\n"
JS_CODE = (
    "function evaluate({ output, expected }) {\n"
    "  return output === expected;\n"
    "}\n"
)


def _create_code_eval(auth_client, name, code, language="python"):
    response = auth_client.post(
        CREATE_URL,
        {
            "name": name,
            "eval_type": "code",
            "code": code,
            "code_language": language,
            "output_type": "pass_fail",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    template = EvalTemplate.objects.get(id=response.data["result"]["id"])
    # The create path declares a code eval's inputs in its code only.
    assert template.config["eval_type_id"] == "CustomCodeEval"
    assert template.config["required_keys"] == []
    return template


def _input_errors(user, workspace, template, inputs):
    """The SDK route's validation of ``inputs``: None when accepted."""
    from sdk.serializers.evaluations import StandaloneEvaluationsSerializer

    serializer = StandaloneEvaluationsSerializer(
        data={
            "eval_templates": template.name,
            "inputs": inputs,
            "model_name": None,
            "config": {},
        },
        context={"request": SimpleNamespace(user=user, workspace=workspace)},
    )
    if serializer.is_valid():
        return None
    return {
        key: [str(item) for item in value]
        for key, value in serializer.errors.items()
    }


def test_code_eval_created_over_the_api_runs_on_the_sdk_route_with_its_inputs(
    auth_client, monkeypatch
):
    from ee.usage.services import emitter
    from sdk.utils import evaluations as sdk_evaluations
    from sdk.views import evaluations as views

    template = _create_code_eval(auth_client, "reply-matches-expected", KWARGS_CODE)

    ledger_row = SimpleNamespace(config="{}", status=None, log_id=uuid4(), save=Mock())
    monkeypatch.setattr(
        sdk_evaluations,
        "_log_and_deduct_cost_for_standalone_eval",
        Mock(return_value=ledger_row),
    )
    monkeypatch.setattr(emitter, "emit", Mock())
    monkeypatch.setattr(views, "get_mixpanel_properties", lambda **kwargs: kwargs)
    monkeypatch.setattr(views, "track_mixpanel_event", Mock())

    # The code executor runs the code; model a working one and check what the
    # route sends it.
    with patch(
        EXECUTOR,
        return_value={
            "status": "success",
            "data": {"result": True, "reason": "reply matches"},
        },
    ) as executor:
        response = auth_client.post(
            SDK_EVAL_URL,
            {
                "eval_name": template.name,
                "inputs": {"output": "Paris", "expected": "Paris"},
            },
            format="json",
        )

    assert response.status_code == 200, response.data
    executor.assert_called_once()
    assert executor.call_args.args[0] == template.config["code"]
    assert executor.call_args.args[1] == {"output": "Paris", "expected": "Paris"}
    assert executor.call_args.args[2] == "python"
    assert len(response.data["result"][0]["evaluations"]) == 1


def test_code_eval_with_kwargs_accepts_any_input_key(auth_client, user, workspace):
    template = _create_code_eval(auth_client, "kwargs-code-eval", KWARGS_CODE)

    assert (
        _input_errors(
            user,
            workspace,
            template,
            {"output": "Paris", "expected": "Paris", "question": "Capital?"},
        )
        is None
    )


def test_code_eval_accepts_the_parameters_of_its_evaluate_function(
    auth_client, user, workspace
):
    template = _create_code_eval(auth_client, "named-code-eval", NAMED_CODE)

    assert (
        _input_errors(
            user, workspace, template, {"output": "Paris", "expected": "Paris"}
        )
        is None
    )
    # A missing parameter is not refused here: the executor passes None.
    assert _input_errors(user, workspace, template, {"output": "Paris"}) is None


@pytest.mark.parametrize(
    "name, code, inputs, message",
    [
        (
            "named-code-eval",
            NAMED_CODE,
            {"output": "Paris", "response": "Paris", "question": "Capital?"},
            "Unexpected keys provided: question, response. Code eval "
            "'named-code-eval' reads only the parameters of its evaluate() "
            "function: output, expected. Send inputs with these keys, or add "
            "**kwargs to evaluate() to accept any key.",
        ),
        (
            "main-code-eval",
            MAIN_CODE,
            {"output": "Paris"},
            "Unexpected keys provided: output. Code eval 'main-code-eval' reads "
            "only the parameters of its main() function: answer. Send inputs "
            "with these keys, or add **kwargs to main() to accept any key.",
        ),
        (
            "no-input-code-eval",
            NO_PARAMS_CODE,
            {"output": "Paris"},
            "Unexpected keys provided: output. Code eval 'no-input-code-eval' "
            "reads no inputs: its evaluate() function takes no parameters. Add "
            "the keys you send as parameters of evaluate(), or add **kwargs to "
            "accept any key.",
        ),
    ],
    ids=["evaluate-parameters", "main-parameters", "no-parameters"],
)
def test_code_eval_refusal_names_the_parameters_its_code_reads(
    auth_client, user, workspace, name, code, inputs, message
):
    template = _create_code_eval(auth_client, name, code)

    errors = _input_errors(user, workspace, template, inputs)

    assert errors == {"inputs": [message]}


def test_javascript_code_eval_accepts_any_input_key(auth_client, user, workspace):
    # The executor calls a JavaScript evaluate() with one object of all inputs.
    template = _create_code_eval(
        auth_client, "js-code-eval", JS_CODE, language="javascript"
    )

    assert (
        _input_errors(
            user,
            workspace,
            template,
            {"output": "Paris", "expected": "Paris", "question": "Capital?"},
        )
        is None
    )


def test_template_that_declares_no_inputs_says_how_to_send_them(user, workspace):
    template = EvalTemplate.objects.create(
        name="judge-the-row",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": [],
            "custom_eval": True,
            "output": "Pass/Fail",
            "rule_prompt": "Judge the reply.",
        },
    )

    assert _input_errors(user, workspace, template, {"output": "Paris"}) == {
        "inputs": [
            "Unexpected keys provided: output. Evaluation template "
            "'judge-the-row' declares no input keys. Send an empty inputs "
            "object, or add the keys to the template as variables, for "
            "example {{output}} in its instructions."
        ]
    }


def _user_code_eval(user, workspace, name, code, required_keys):
    """A user's code eval whose template also declares ``required_keys``."""
    return EvalTemplate.objects.create(
        name=name,
        organization=user.organization,
        workspace=workspace,
        owner="user",
        config={
            "eval_type_id": "CustomCodeEval",
            "code": code,
            "required_keys": required_keys,
            "custom_eval": True,
            "output": "Pass/Fail",
        },
    )


@pytest.mark.parametrize(
    "code, inputs",
    [
        (
            NAMED_CODE,
            {"output": "Paris", "expected": "Paris", "question": "Capital?"},
        ),
        (KWARGS_CODE, {"question": "Capital?", "other": "b"}),
    ],
    ids=["parameters-and-declared-keys", "kwargs-takes-any-key"],
)
def test_user_code_eval_takes_its_declared_keys_and_the_inputs_its_code_reads(
    user, workspace, code, inputs
):
    # The app's mapping form offers a user's code eval the parameters of its
    # evaluate() function together with the keys its template declares.
    template = _user_code_eval(
        user, workspace, "declared-code-eval", code, ["question"]
    )

    assert _input_errors(user, workspace, template, inputs) is None


@pytest.mark.parametrize(
    "code, accepted",
    [
        (NAMED_CODE, "output, expected, question"),
        (NO_PARAMS_CODE, "question"),
    ],
    ids=["evaluate-parameters", "no-parameters"],
)
def test_user_code_eval_refusal_names_its_parameters_and_declared_keys(
    user, workspace, code, accepted
):
    template = _user_code_eval(
        user, workspace, "declared-code-eval", code, ["question"]
    )

    errors = _input_errors(
        user, workspace, template, {"question": "Capital?", "other": "b"}
    )

    assert errors == {
        "inputs": [
            "Unexpected keys provided: other. Code eval 'declared-code-eval' "
            "accepts the parameters of its evaluate() function and the keys it "
            f"declares: {accepted}. Send inputs with these keys, or add "
            "**kwargs to evaluate() to accept any key."
        ]
    }


@pytest.mark.parametrize(
    "inputs",
    [{"question": "Capital?"}, {}],
    ids=["declared-key-only", "no-inputs"],
)
def test_user_code_eval_does_not_require_every_key_it_takes(user, workspace, inputs):
    # A key the call leaves out is not refused here, as before: the run decides.
    template = _user_code_eval(
        user, workspace, "declared-code-eval", NAMED_CODE, ["question"]
    )

    assert _input_errors(user, workspace, template, inputs) is None


PIPELINE_URL = "/sdk/api/v1/evaluate-pipeline/"
DECORATED_CODE = (
    "def tolerant(fn):\n"
    "    def inner(**kwargs):\n"
    "        return fn(kwargs.get('output'))\n"
    "    return inner\n"
    "\n"
    "\n"
    "@tolerant\n"
    "def evaluate(output):\n"
    "    return bool(output)\n"
)
# The executor passes every input by name, which a positional-only parameter
# cannot take.
POSITIONAL_ONLY_CODE = "def evaluate(output, /):\n    return bool(output)\n"
LANGUAGE_PARAMETER_CODE = (
    "def evaluate(output, language):\n    return output.startswith(language)\n"
)
CODE_AND_LANGUAGE_MAIN_CODE = (
    "def main(code, language, answer):\n    return bool(answer)\n"
)


@pytest.mark.parametrize(
    "code",
    [
        KWARGS_CODE,
        JS_CODE,
        "def evaluate(output:\n    return True\n",
        "def score(output):\n    return True\n",
        "def evaluate(output):\n    return True\n\n\nevaluate = print\n",
        "if True:\n    def evaluate(output):\n        return True\n",
        "async def evaluate(output):\n    return True\n",
        DECORATED_CODE,
        POSITIONAL_ONLY_CODE,
    ],
    ids=[
        "kwargs",
        "javascript",
        "does-not-parse",
        "no-entry-function",
        "entry-rebound",
        "entry-not-top-level",
        "async-entry",
        "decorated-entry",
        "positional-only",
    ],
)
def test_code_that_takes_any_key_or_cannot_be_read_has_no_entry(code):
    # None means the route accepts any key and the executor reports any
    # problem when the eval runs.
    from model_hub.utils.code_eval_inputs import code_eval_entry

    assert code_eval_entry(code) is None


@pytest.mark.parametrize(
    "code, parameters",
    [
        (NAMED_CODE, ("output", "expected")),
        (
            "def evaluate(output, *, expected=None):\n    return True\n",
            ("output", "expected"),
        ),
        (MAIN_CODE + "\n\n" + NAMED_CODE, ("output", "expected")),
    ],
    ids=["named", "keyword-only", "evaluate-before-main"],
)
def test_entry_parameters_are_the_inputs_the_code_reads(code, parameters):
    from model_hub.utils.code_eval_inputs import code_eval_entry

    entry = code_eval_entry(code)

    assert (entry.name, entry.parameters) == ("evaluate", parameters)


@pytest.mark.parametrize(
    "code",
    [
        "def evaluate(output, context):\n    return bool(context)\n",
        "def evaluate(self, output):\n    return bool(output)\n",
        "def evaluate(cls, output):\n    return bool(output)\n",
    ],
    ids=["context", "self", "cls"],
)
def test_entry_parameters_leave_out_names_that_are_not_inputs(code):
    # The app's mapping form never offers context, self or cls as inputs.
    from model_hub.utils.code_eval_inputs import code_eval_entry

    entry = code_eval_entry(code)

    assert (entry.name, entry.parameters) == ("evaluate", ("output",))


@pytest.mark.parametrize(
    "code, name, parameters",
    [
        (LANGUAGE_PARAMETER_CODE, "evaluate", ("output", "language")),
        (
            "def evaluate(code, output):\n    return code in output\n",
            "evaluate",
            ("code", "output"),
        ),
        (CODE_AND_LANGUAGE_MAIN_CODE, "main", ("code", "language", "answer")),
    ],
    ids=["language", "code", "code-and-language-in-main"],
)
def test_entry_parameters_take_code_and_language(code, name, parameters):
    # The code runner passes its own code and language apart from the inputs,
    # so an input with either name reaches the entry function as sent.
    from model_hub.utils.code_eval_inputs import code_eval_entry

    entry = code_eval_entry(code)

    assert (entry.name, entry.parameters) == (name, parameters)


@pytest.mark.parametrize(
    "error", [RecursionError, MemoryError], ids=["recursion", "memory"]
)
def test_code_eval_whose_code_cannot_be_read_accepts_any_input_key(
    auth_client, user, workspace, monkeypatch, error
):
    from model_hub.utils import code_eval_inputs

    template = _create_code_eval(
        auth_client, f"unreadable-code-eval-{error.__name__.lower()}", NAMED_CODE
    )

    def cannot_read(*args, **kwargs):
        raise error("the code is too deep to read")

    monkeypatch.setattr(code_eval_inputs.ast, "parse", cannot_read)

    # Not a server error: the executor reports the code when the eval runs.
    assert (
        _input_errors(
            user, workspace, template, {"output": "Paris", "question": "Capital?"}
        )
        is None
    )


def test_decorated_code_eval_accepts_any_input_key(auth_client, user, workspace):
    # A decorator can change the signature the executor calls.
    template = _create_code_eval(auth_client, "decorated-code-eval", DECORATED_CODE)

    assert (
        _input_errors(
            user, workspace, template, {"output": "Paris", "expected": "Paris"}
        )
        is None
    )


def test_code_eval_with_positional_only_parameters_accepts_any_input_key(
    auth_client, user, workspace
):
    # The executor reports such a function when the eval runs.
    template = _create_code_eval(
        auth_client, "positional-only-code-eval", POSITIONAL_ONLY_CODE
    )

    assert (
        _input_errors(
            user, workspace, template, {"output": "Paris", "question": "Capital?"}
        )
        is None
    )


@pytest.mark.parametrize(
    "code, inputs",
    [
        (LANGUAGE_PARAMETER_CODE, {"output": "Bonjour", "language": "fr"}),
        ("def evaluate(code):\n    return bool(code)\n", {"code": "x = 1"}),
        (
            CODE_AND_LANGUAGE_MAIN_CODE,
            {"code": "x = 1", "language": "fr", "answer": "Paris"},
        ),
    ],
    ids=["language", "code", "code-and-language-in-main"],
)
def test_code_eval_takes_an_input_named_code_or_language_that_its_code_reads(
    auth_client, user, workspace, code, inputs
):
    # The code runner passes its own code and language apart from the inputs,
    # so an input with either name reaches the eval's function as sent, and
    # both routes take it like any other parameter of that function.
    template = _create_code_eval(auth_client, "code-and-language-code-eval", code)

    assert _input_errors(user, workspace, template, inputs) is None
    assert (
        _pipeline_input_errors(
            user, template, {key: [value] for key, value in inputs.items()}
        )
        is None
    )


@pytest.mark.parametrize("route", ["evaluation-route", "pipeline-route"])
def test_code_eval_refusal_names_code_and_language_among_the_parameters_its_code_reads(
    auth_client, user, workspace, route
):
    template = _create_code_eval(
        auth_client, "code-and-language-main-eval", CODE_AND_LANGUAGE_MAIN_CODE
    )
    inputs = {"answer": "Paris", "question": "Capital?"}

    if route == "evaluation-route":
        errors = _input_errors(user, workspace, template, inputs)
    else:
        errors = _pipeline_input_errors(
            user, template, {key: [value] for key, value in inputs.items()}
        )

    assert errors == {
        "inputs": [
            "Unexpected keys provided: question. Code eval "
            "'code-and-language-main-eval' reads only the parameters of its "
            "main() function: code, language, answer. Send inputs with these "
            "keys, or add **kwargs to main() to accept any key."
        ]
    }


def test_code_eval_takes_a_declared_key_named_after_an_argument_of_the_code_runner(
    user, workspace
):
    # A key the template declares is accepted as before, whatever its name.
    template = _user_code_eval(
        user,
        workspace,
        "declared-language-code-eval",
        LANGUAGE_PARAMETER_CODE,
        ["language"],
    )

    assert (
        _input_errors(
            user, workspace, template, {"output": "Bonjour", "language": "fr"}
        )
        is None
    )
    assert (
        _pipeline_input_errors(
            user, template, {"output": ["Bonjour"], "language": ["fr"]}
        )
        is None
    )


def test_code_eval_does_not_take_context_from_its_code(
    auth_client, user, workspace
):
    # The code runner fills context with the row; the app's mapping form does
    # not offer it as an input.
    template = _create_code_eval(
        auth_client,
        "context-code-eval",
        "def evaluate(output, context):\n    return bool(output)\n",
    )

    assert _input_errors(user, workspace, template, {"output": "Paris"}) is None
    assert _input_errors(
        user, workspace, template, {"output": "Paris", "context": "row"}
    ) == {
        "inputs": [
            "Unexpected keys provided: context. Code eval 'context-code-eval' "
            "reads only the parameters of its evaluate() function: output. "
            "Send inputs with these keys, or add **kwargs to evaluate() to "
            "accept any key."
        ]
    }


@pytest.mark.parametrize("name", ["context", "self", "cls"])
def test_code_eval_takes_no_input_named_context_self_or_cls_on_either_route(
    user, workspace, name
):
    # The app's mapping form never offers these as inputs, so neither route
    # takes them from the code, while code and language are taken.
    template = _user_code_eval(
        user,
        workspace,
        f"{name}-code-eval",
        f"def evaluate({name}, output):\n    return bool(output)\n",
        [],
    )
    message = (
        f"Unexpected keys provided: {name}. Code eval '{name}-code-eval' reads "
        "only the parameters of its evaluate() function: output. Send inputs "
        "with these keys, or add **kwargs to evaluate() to accept any key."
    )

    assert _input_errors(user, workspace, template, {"output": "Paris"}) is None
    assert _pipeline_input_errors(user, template, {"output": ["Paris"]}) is None
    assert _input_errors(
        user, workspace, template, {"output": "Paris", name: "x"}
    ) == {"inputs": [message]}
    assert _pipeline_input_errors(
        user, template, {"output": ["Paris"], name: ["x"]}
    ) == {"inputs": [message]}


@pytest.mark.parametrize(
    "required_keys, inputs, message",
    [
        (
            [""],
            {"output": "Paris"},
            "Unexpected keys provided: output. Evaluation template "
            "'blank-variable-judge' declares no input keys. Send an empty "
            "inputs object, or add the keys to the template as variables, for "
            "example {{output}} in its instructions.",
        ),
        (
            ["", "output"],
            {"output": "Paris", "expected": "Paris"},
            "Unexpected keys provided: expected. Accepted keys are: output",
        ),
        (
            [None, " ", "output"],
            {"output": "Paris", "expected": "Paris"},
            "Unexpected keys provided: expected. Accepted keys are: output",
        ),
        ([""], {"": "Paris"}, None),
    ],
    ids=["only-blank", "blank-and-named", "not-a-string", "blank-key-still-accepted"],
)
def test_blank_declared_keys_are_never_named_as_accepted(
    user, workspace, required_keys, inputs, message
):
    # A variable with no name, "{{ }}", in a template's instructions declares
    # the key "". The template accepts it as before, but a refusal never names
    # it, so it never ends in an empty list.
    template = EvalTemplate.objects.create(
        name="blank-variable-judge",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": required_keys,
            "custom_eval": True,
            "output": "Pass/Fail",
            "rule_prompt": "Judge {{ }}.",
        },
        # Saved with a model, as the app saves an LLM judge; without one the
        # accepted call is refused for its model, not its keys.
        model="turing_large",
    )

    expected = None if message is None else {"inputs": [message]}
    assert _input_errors(user, workspace, template, inputs) == expected


def _built_in_code_runner():
    """The built-in custom_code_evaluation template: no code of its own."""
    from model_hub.utils.evals import evals_template

    definition = next(
        item for item in evals_template if item["name"] == "custom_code_evaluation"
    )
    template = EvalTemplate.objects.create(
        name=definition["name"],
        organization=None,
        owner="system",
        eval_id=definition["eval_id"],
        config=definition["config"],
    )
    assert "code" not in template.config
    return template


def test_built_in_code_eval_without_code_refuses_input_keys(user, workspace):
    template = _built_in_code_runner()

    assert _input_errors(user, workspace, template, {"output": "Paris"}) == {
        "inputs": [
            "Unexpected keys provided: output. Evaluation template "
            "'custom_code_evaluation' declares no input keys. Send an empty "
            "inputs object."
        ]
    }


@pytest.mark.parametrize(
    "required_keys, message",
    [
        (
            ["output"],
            "Unexpected keys provided: expected. Accepted keys are: output",
        ),
        (
            [],
            "Unexpected keys provided: expected, output. Evaluation template "
            "'system-code-eval' declares no input keys. Send an empty inputs "
            "object.",
        ),
    ],
    ids=["declared-keys", "no-declared-keys"],
)
def test_system_code_eval_takes_only_the_keys_it_declares(
    user, workspace, required_keys, message
):
    # Only a user's code eval reads its code for inputs.
    template = EvalTemplate.objects.create(
        name="system-code-eval",
        organization=None,
        owner="system",
        config={
            "eval_type_id": "CustomCodeEval",
            "code": NAMED_CODE,
            "required_keys": required_keys,
            "output": "Pass/Fail",
        },
    )

    errors = _input_errors(
        user, workspace, template, {"output": "Paris", "expected": "Paris"}
    )

    assert errors == {"inputs": [message]}


def test_non_code_template_takes_only_the_keys_it_declares(user, workspace):
    # A template changed from code to an LLM judge keeps its old code in its
    # config; only a code eval reads its code for inputs.
    template = EvalTemplate.objects.create(
        name="judge-with-old-code",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "code": NAMED_CODE,
            "required_keys": ["output"],
            "custom_eval": True,
            "output": "Pass/Fail",
            "rule_prompt": "Judge {{output}}.",
        },
    )

    errors = _input_errors(
        user, workspace, template, {"output": "Paris", "expected": "Paris"}
    )

    assert errors == {
        "inputs": ["Unexpected keys provided: expected. Accepted keys are: output"]
    }


def _pipeline_input_errors(user, template, inputs):
    """The CI/CD route's validation of one item's ``inputs``: None when accepted."""
    from sdk.serializers.eval_ci_cd import CICDEvaluationItemSerializer

    serializer = CICDEvaluationItemSerializer(
        data={"eval_template": template.name, "inputs": inputs},
        context={"request": SimpleNamespace(user=user)},
    )
    if serializer.is_valid():
        return None
    return {
        key: [str(item) for item in value]
        for key, value in serializer.errors.items()
    }


def test_code_eval_created_over_the_api_is_accepted_on_the_pipeline_route(
    auth_client, organization, monkeypatch
):
    from sdk.views import eval_ci_cd as views
    from tracer.models.project import Project

    template = _create_code_eval(auth_client, "pipeline-code-eval", NAMED_CODE)
    project = Project.objects.create(
        name="code-eval-pipeline",
        organization=organization,
        trace_type="observe",
        model_type="GenerativeLLM",
    )
    create_run = Mock(
        return_value=SimpleNamespace(project=project, version="v1", id=uuid4())
    )
    monkeypatch.setattr(views, "create_evaluation_run", create_run)

    response = auth_client.post(
        PIPELINE_URL,
        {
            "project_name": project.name,
            "version": "v1",
            "eval_data": [
                {
                    "eval_template": template.name,
                    "inputs": {"output": ["Paris"], "expected": ["Paris"]},
                }
            ],
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    create_run.assert_called_once()
    (item,) = create_run.call_args.args[0]["eval_data"]
    assert item["inputs"] == {"output": ["Paris"], "expected": ["Paris"]}


def test_pipeline_route_refusal_names_the_parameters_its_code_reads(
    auth_client, user
):
    template = _create_code_eval(auth_client, "pipeline-named-code-eval", NAMED_CODE)

    errors = _pipeline_input_errors(
        user, template, {"output": ["Paris"], "question": ["Capital?"]}
    )

    assert errors == {
        "inputs": [
            "Unexpected keys provided: question. Code eval "
            "'pipeline-named-code-eval' reads only the parameters of its "
            "evaluate() function: output, expected. Send inputs with these "
            "keys, or add **kwargs to evaluate() to accept any key."
        ]
    }


def test_pipeline_route_template_without_keys_says_how_to_add_them(
    user, workspace
):
    # This route needs at least one input, so it never says to send none.
    template = EvalTemplate.objects.create(
        name="pipeline-judge-the-row",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": [],
            "custom_eval": True,
            "output": "Pass/Fail",
            "rule_prompt": "Judge the reply.",
        },
    )

    assert _pipeline_input_errors(user, template, {"output": ["Paris"]}) == {
        "inputs": [
            "Unexpected keys provided: output. Evaluation template "
            "'pipeline-judge-the-row' declares no input keys. Add the keys to "
            "the template as variables, for example {{output}} in its "
            "instructions."
        ]
    }


def test_pipeline_route_built_in_template_without_keys_cannot_run(user):
    template = _built_in_code_runner()

    assert _pipeline_input_errors(user, template, {"output": ["Paris"]}) == {
        "inputs": [
            "Unexpected keys provided: output. Evaluation template "
            "'custom_code_evaluation' declares no input keys, and this endpoint "
            "needs at least one input, so the template cannot run here."
        ]
    }


def test_pipeline_route_takes_declared_keys_and_parameters_of_a_user_code_eval(
    user, workspace
):
    template = _user_code_eval(
        user, workspace, "pipeline-declared-code-eval", NAMED_CODE, ["question"]
    )

    assert (
        _pipeline_input_errors(
            user,
            template,
            {"output": ["Paris"], "expected": ["Paris"], "question": ["Capital?"]},
        )
        is None
    )
    assert _pipeline_input_errors(
        user, template, {"question": ["Capital?"], "other": ["b"]}
    ) == {
        "inputs": [
            "Unexpected keys provided: other. Code eval "
            "'pipeline-declared-code-eval' accepts the parameters of its "
            "evaluate() function and the keys it declares: output, expected, "
            "question. Send inputs with these keys, or add **kwargs to "
            "evaluate() to accept any key."
        ]
    }


def test_pipeline_route_takes_an_input_named_language_that_its_code_reads(
    auth_client, user
):
    template = _create_code_eval(
        auth_client, "pipeline-language-code-eval", LANGUAGE_PARAMETER_CODE
    )

    assert (
        _pipeline_input_errors(
            user, template, {"output": ["Bonjour"], "language": ["fr"]}
        )
        is None
    )
    assert _pipeline_input_errors(
        user,
        template,
        {"output": ["Bonjour"], "language": ["fr"], "question": ["Greeting?"]},
    ) == {
        "inputs": [
            "Unexpected keys provided: question. Code eval "
            "'pipeline-language-code-eval' reads only the parameters of its "
            "evaluate() function: output, language. Send inputs with these "
            "keys, or add **kwargs to evaluate() to accept any key."
        ]
    }


def test_pipeline_route_template_with_only_blank_declared_keys_says_how_to_add_them(
    user, workspace
):
    template = EvalTemplate.objects.create(
        name="pipeline-blank-variable-judge",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": [" "],
            "custom_eval": True,
            "output": "Pass/Fail",
            "rule_prompt": "Judge {{ }}.",
        },
    )

    assert _pipeline_input_errors(user, template, {"output": ["Paris"]}) == {
        "inputs": [
            "Unexpected keys provided: output. Evaluation template "
            "'pipeline-blank-variable-judge' declares no input keys. Add the "
            "keys to the template as variables, for example {{output}} in its "
            "instructions."
        ]
    }


@pytest.mark.parametrize("code", [None, "  \n"], ids=["no-code", "blank-code"])
def test_user_code_eval_without_code_says_to_add_an_evaluate_function(
    user, workspace, code
):
    # A user's copy of the built-in custom_code_evaluation has no code yet.
    config = {
        "eval_type_id": "CustomCodeEval",
        "required_keys": [],
        "custom_eval": True,
        "output": "Pass/Fail",
    }
    if code is not None:
        config["code"] = code
    template = EvalTemplate.objects.create(
        name="copied-code-eval",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
        config=config,
    )

    assert _input_errors(user, workspace, template, {"output": "Paris"}) == {
        "inputs": [
            "Unexpected keys provided: output. Evaluation template "
            "'copied-code-eval' declares no input keys. Send an empty inputs "
            "object, or add code with an evaluate() function that takes these "
            "keys."
        ]
    }
    assert _pipeline_input_errors(user, template, {"output": ["Paris"]}) == {
        "inputs": [
            "Unexpected keys provided: output. Evaluation template "
            "'copied-code-eval' declares no input keys. Add code with an "
            "evaluate() function that takes these keys."
        ]
    }


UPDATE_URL = "/model-hub/eval-templates/{}/update/"


def _edited_in_the_app(auth_client, name, code, saved_from):
    """An existing code eval whose code is changed and saved in the app.

    The eval page and the eval picker both send the code and no instructions,
    so the template keeps the required_keys it has.
    """
    template = _create_code_eval(auth_client, name, MAIN_CODE)
    payload = {
        "code": code,
        "code_language": "python",
        "model": template.model,
        "output_type": "pass_fail",
        "pass_threshold": 0.5,
        "choice_scores": None,
        "multi_choice": False,
    }
    if saved_from == "eval-page":
        payload.update(
            description=None,
            tags=[],
            check_internet=False,
            error_localizer_enabled=False,
            template_format="mustache",
        )
    response = auth_client.put(UPDATE_URL.format(template.id), payload, format="json")
    assert response.status_code == 200, response.data
    template.refresh_from_db()
    return template


def _made_in_the_app_create_form(auth_client, name, code):
    """A code eval made in the app's create form: a draft, then its code.

    The form does not offer the Code type today; this is the path it takes
    when that type is offered again.
    """
    response = auth_client.post(
        CREATE_URL,
        {
            "is_draft": True,
            "output_type": "pass_fail",
            "model": "turing_large",
            "pass_threshold": 0.5,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    template_id = response.data["result"]["id"]
    response = auth_client.put(
        UPDATE_URL.format(template_id),
        {
            "name": name,
            "eval_type": "code",
            "code": code,
            "code_language": "python",
            "output_type": "pass_fail",
            "pass_threshold": 0.5,
            "choice_scores": None,
            "template_format": "mustache",
            "description": None,
            "tags": [],
            "publish": True,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    return EvalTemplate.objects.get(id=template_id)


@pytest.mark.parametrize(
    "saved_in_the_app",
    ["edited-on-the-eval-page", "edited-in-the-eval-picker"],
)
def test_code_eval_saved_in_the_app_takes_the_inputs_of_one_made_over_the_api(
    auth_client, user, workspace, saved_in_the_app
):
    made_over_the_api = _create_code_eval(
        auth_client, "made-over-the-api", NAMED_CODE
    )
    saved = _edited_in_the_app(
        auth_client,
        "saved-in-the-app",
        NAMED_CODE,
        "eval-page"
        if saved_in_the_app == "edited-on-the-eval-page"
        else "eval-picker",
    )

    _assert_same_inputs(user, workspace, made_over_the_api, saved)


def test_code_eval_from_the_create_form_once_it_offers_code_again_takes_the_same_inputs(
    auth_client, user, workspace
):
    # The app's create form does not offer the Code type today. This is the
    # path a code eval made there takes when that type is shown again.
    made_over_the_api = _create_code_eval(
        auth_client, "made-over-the-api", NAMED_CODE
    )
    saved = _made_in_the_app_create_form(auth_client, "saved-in-the-app", NAMED_CODE)

    _assert_same_inputs(user, workspace, made_over_the_api, saved)


def _assert_same_inputs(user, workspace, made_over_the_api, saved):
    """Both templates store the same code eval and take the same inputs."""
    for template in (made_over_the_api, saved):
        assert template.eval_type == "code"
        assert template.config["eval_type_id"] == "CustomCodeEval"
        assert template.config["code"] == NAMED_CODE
        assert template.config["required_keys"] == []
        assert (
            _input_errors(
                user, workspace, template, {"output": "Paris", "expected": "Paris"}
            )
            is None
        )
        assert (
            _pipeline_input_errors(
                user, template, {"output": ["Paris"], "expected": ["Paris"]}
            )
            is None
        )
        assert _input_errors(
            user, workspace, template, {"output": "Paris", "question": "Capital?"}
        ) == {
            "inputs": [
                "Unexpected keys provided: question. Code eval "
                f"'{template.name}' reads only the parameters of its evaluate() "
                "function: output, expected. Send inputs with these keys, or add "
                "**kwargs to evaluate() to accept any key."
            ]
        }


UNDECLARED_KEYS = ("expected", "unlisted")


def _refused_keys(errors):
    """The keys a refusal names as unexpected; empty when the call is accepted."""
    if errors is None:
        return set()
    (message,) = errors["inputs"]
    assert message.startswith("Unexpected keys provided: "), message
    # A refusal says what to send instead: it never ends in an empty list.
    assert not message.rstrip().endswith(":"), message
    named = message.removeprefix("Unexpected keys provided: ").split(". ", 1)[0]
    return set(named.split(", "))


@pytest.mark.parametrize("route", ["evaluation-route", "pipeline-route"])
@pytest.mark.parametrize(
    "required_keys",
    [[], ["question", "output"]],
    ids=["no-declared-keys", "declared-keys"],
)
@pytest.mark.parametrize(
    "code",
    [None, "  \n", NAMED_CODE, KWARGS_CODE, JS_CODE],
    ids=["no-code", "blank-code", "named-parameters", "kwargs", "javascript"],
)
@pytest.mark.parametrize(
    "eval_type_id",
    ["CustomCodeEval", "CustomPromptEvaluator", "IsJson"],
    ids=["code-eval", "llm-judge", "function-eval"],
)
@pytest.mark.parametrize("owner", ["system", "user"])
def test_every_template_accepts_at_least_what_it_accepted_before(
    user, workspace, owner, eval_type_id, code, required_keys, route
):
    """Compatibility of the input-key check for every kind of template.

    Before code evals took the inputs their code reads, every template on
    both routes took exactly the keys its required_keys declare. Every
    template still accepts a call with those keys and never refuses one of
    them. Only a user's code eval with code may take more: every other
    template refuses exactly the keys it does not declare.
    """
    config = {
        "eval_type_id": eval_type_id,
        "required_keys": required_keys,
        "output": "Pass/Fail",
    }
    if code is not None:
        config["code"] = code
    if owner == "user":
        config["custom_eval"] = True
    template = EvalTemplate.objects.create(
        name="compatibility-template",
        organization=user.organization if owner == "user" else None,
        workspace=workspace if owner == "user" else None,
        owner=owner,
        eval_type="code" if eval_type_id == "CustomCodeEval" else "llm",
        config=config,
        # An LLM judge is saved with a model, as the app saves one; without
        # it both routes refuse the call for its model, not its keys.
        model="turing_large" if eval_type_id == "CustomPromptEvaluator" else None,
    )

    def errors(keys):
        if route == "evaluation-route":
            return _input_errors(user, workspace, template, dict.fromkeys(keys, "x"))
        return _pipeline_input_errors(
            user, template, {key: ["x"] for key in keys}
        )

    # The pipeline route has always refused an empty inputs object, so a
    # template that declares no keys had no call it accepted there.
    if required_keys or route == "evaluation-route":
        assert errors(required_keys) is None

    refused = _refused_keys(errors([*required_keys, *UNDECLARED_KEYS]))

    assert refused <= set(UNDECLARED_KEYS)
    reads_its_code = (
        owner == "user"
        and eval_type_id == "CustomCodeEval"
        and bool(code and code.strip())
    )
    if not reads_its_code:
        assert refused == set(UNDECLARED_KEYS)
