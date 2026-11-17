"""The smart filter, the criteria helper and the AI eval writer run on their
named platform models.

Each of the three is a named platform feature, and its model is its entry in
the platform's one list of its own models (``PLATFORM_MODELS``): the smart
filter runs Gemini 3.5 Flash Lite on Vertex in its global location at
temperature 0.0, the criteria helper the Bedrock Claude Sonnet 4.5 model at
0.2, and the AI eval writer the Bedrock Claude Haiku 4.5 model at 0.3. The
builder the three call (``model_route.platform_llm``) takes which feature is
calling, not a model: anything other than one of the three is refused before
a model is built or called, and it cannot be given a model, a provider, a
temperature, a key or a configuration.

Every use of that builder in the backend's source is listed here, read from
the source, with the feature it names. A new use, a listed one that is gone,
or a call that does not name its feature by its member fails the census.

Each feature still runs as it does today. With no key of the customer's own,
not on the organization's credits and no gateway answer, its call is made
with its entry's model, provider and temperature, and with no key,
configuration, project or address. The smart filter keeps its max tokens
(800 for the smart mode, 500 for the other two), the request wall and its
five model turns; the criteria helper keeps its 8100 max tokens, and a saved
breakdown makes no call; the AI eval writer keeps its 1500 and 1000.

The tests use values they set themselves; every model call is replaced by a
recorder, so nothing leaves the process.
"""

import ast
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest import mock

import litellm
import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core.llm import platform_model
from agentic_eval.core.utils.model_config import ModelConfigs
from model_hub.services import model_route
from model_hub.services.ai_eval_writer_service import generate_eval_prompt
from model_hub.tasks import agent as agent_tasks
from model_hub.utils.model_endpoints import ModelConfigurationRefused
from model_hub.views import ai_filter

ORGANIZATION_ID = "fi-test-organization"
SCHEMA = [
    {
        "field": "status",
        "label": "Status",
        "type": "enum",
        "category": "system",
        "operators": ["is", "is_not"],
        "choices": ["failed", "completed"],
    }
]
# What a platform feature's call is never given: a key, a customer's
# configuration, a project or an address.
KEYS_AND_ADDRESSES = (
    "api_base",
    "api_key",
    "vertex_ai_credentials",
    "vertex_credentials",
    "vertex_project",
)
# What the builder could be given that would choose the model, how it runs or
# whose it is.
CALL_SETTINGS = (
    "api_key",
    "config",
    "model_name",
    "model_route",
    "operation",
    "org_id",
    "provider",
    "temperature",
    "vertex_location",
)


def _response(answer, tool_calls=None):
    message = SimpleNamespace(content=answer, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class _Model:
    """Stands in for every model call: records what it is given and answers
    with ``answer``; sends nothing."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        return _response(self.answer)


def _model_answering(monkeypatch, answer):
    """Every model call replaced by one recorder that answers ``answer``."""
    model = _Model(answer)
    for name in ("completion", "acompletion"):
        monkeypatch.setattr(litellm, name, model)
    return model


def _models_built(monkeypatch):
    """What each model the features build is built with, in order; each one
    is still the model wrapper itself."""
    built = []

    class _Recorded(llm_module.LLM):
        def __init__(self, **kwargs):
            built.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(llm_module, "LLM", _Recorded)
    monkeypatch.setattr(agent_tasks, "LLM", _Recorded)
    return built


def _model_class(monkeypatch):
    """The model wrapper replaced by a double, wherever the features take it
    from."""
    llm_class = mock.MagicMock(name="LLM")
    monkeypatch.setattr(llm_module, "LLM", llm_class)
    monkeypatch.setattr(agent_tasks, "LLM", llm_class)
    return llm_class


class _SavedCriteria:
    """Stands in for the saved criteria breakdowns: ``saved`` is the one
    found for a prompt, if any; what is saved is recorded."""

    def __init__(self, saved=None):
        self.saved = saved
        self.created = []
        self.objects = self

    def filter(self, **kwargs):
        return SimpleNamespace(first=lambda: self.saved)

    def get_or_create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(**kwargs), True


@pytest.fixture
def no_gateway(monkeypatch):
    """No platform gateway here: a call goes to the model library."""
    try:
        from ee.usage.services import gateway_llm_client
    except ImportError:
        return
    for name in ("get_gateway_client", "get_async_gateway_client"):
        monkeypatch.setattr(gateway_llm_client, name, lambda *args, **kwargs: None)


@pytest.fixture
def not_on_credits():
    """No model runs on the organization's Future AGI credits here."""
    with model_route.managed_routing_port(None):
        yield


@pytest.fixture
def platform_settings(monkeypatch):
    """What building the platform's own models needs, made up here: no file
    is read, no client connects and no usage is logged."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent/test.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fi-test-platform-project")
    monkeypatch.setattr(llm_module.genai, "Client", lambda *args, **kwargs: object())
    for name in ("AnthropicBedrock", "AsyncAnthropicBedrock"):
        monkeypatch.setattr(llm_module, name, lambda *args, **kwargs: object())
    monkeypatch.setattr(llm_module, "log_to_clickhouse", lambda *args, **kwargs: None)


@pytest.fixture
def no_saved_criteria(monkeypatch):
    saved = _SavedCriteria()
    monkeypatch.setattr(agent_tasks, "CriteriaCache", saved)
    return saved


def _bedrock_models_named_here(monkeypatch):
    """PLATFORM_MODELS with the two Bedrock models named by values set here
    (a deployment names them in its settings); everything else is kept."""
    models = dict(platform_model.PLATFORM_MODELS)
    for name in ("CRITERIA_HELPER", "AI_EVAL_WRITER"):
        feature = getattr(platform_model.PlatformFeature, name)
        config, temperature = models[feature]
        models[feature] = (
            replace(config, model_name=f"fi-test-bedrock-{feature.value}"),
            temperature,
        )
    monkeypatch.setattr(platform_model, "PLATFORM_MODELS", MappingProxyType(models))


def _run_the_smart_filter():
    return ai_filter._run_smart_agent(
        "show failed calls",
        SCHEMA,
        lambda *args, **kwargs: [],
        organization=ORGANIZATION_ID,
        workspace=None,
    )


def _run_the_criteria_helper():
    return agent_tasks.create_criteria_text_prompt(
        "Is the answer polite?", organization=ORGANIZATION_ID, workspace=None
    )


def _run_the_ai_eval_writer(output_format="prompt"):
    return generate_eval_prompt(
        description="polite answers",
        output_format=output_format,
        organization=ORGANIZATION_ID,
        workspace=None,
    )


# feature -> (how it is run, what its model answers)
FEATURES = {
    "SMART_FILTER": (_run_the_smart_filter, ""),
    "CRITERIA_HELPER": (_run_the_criteria_helper, '{"1": "The answer is polite."}'),
    "AI_EVAL_WRITER": (_run_the_ai_eval_writer, "Judge whether it is polite."),
}


# ------------------------------------------------ the named platform models ---


def test_each_feature_is_named_with_the_model_it_runs_on_today():
    feature = platform_model.PlatformFeature
    assert platform_model.PLATFORM_MODELS[feature.SMART_FILTER] == (
        ModelConfigs.VERTEX_GEMINI_3_5_FLASH_LITE,
        0.0,
    )
    assert ModelConfigs.VERTEX_GEMINI_3_5_FLASH_LITE.vertex_location == "global"
    assert platform_model.PLATFORM_MODELS[feature.CRITERIA_HELPER] == (
        ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN,
        0.2,
    )
    assert platform_model.PLATFORM_MODELS[feature.AI_EVAL_WRITER] == (
        ModelConfigs.HAIKU_4_5_BEDROCK_ARN,
        0.3,
    )


@pytest.mark.parametrize("name", sorted(FEATURES))
def test_each_feature_calls_its_named_platform_model_and_nothing_of_the_customers(
    monkeypatch,
    no_gateway,
    not_on_credits,
    platform_settings,
    no_saved_criteria,
    name,
):
    _bedrock_models_named_here(monkeypatch)
    feature = getattr(platform_model.PlatformFeature, name)
    config, temperature = platform_model.PLATFORM_MODELS[feature]
    built = _models_built(monkeypatch)
    run, answer = FEATURES[name]
    model = _model_answering(monkeypatch, answer)

    run()

    assert len(built) == 1, "the feature's model was not built once"
    assert built[0]["provider"] == config.provider
    assert built[0]["model_name"] == config.model_name
    assert built[0]["temperature"] == temperature
    assert built[0].get("org_id") == ORGANIZATION_ID
    for setting in ("api_key", "config", "model_route"):
        assert built[0].get(setting) is None, setting
    assert len(model.calls) == 1, "the model call was not made once"
    given = model.calls[0]
    assert given["model"] == config.model_name
    assert given["temperature"] == temperature
    assert given.get("vertex_location") == config.vertex_location
    for setting in KEYS_AND_ADDRESSES:
        assert setting not in given, setting


# (what the builder is given in place of a feature)
NOT_A_FEATURE = {
    "a_model_named_as_text": "vertex_ai/gemini-chosen-by-a-caller",
    "a_features_name_as_text": "smart_filter",
    "a_model_configuration": ModelConfigs.VERTEX_GEMINI_3_5_FLASH_LITE,
    "nothing": None,
}


@pytest.mark.parametrize("given", sorted(NOT_A_FEATURE))
def test_the_builder_refuses_anything_but_a_named_feature_before_any_call(
    monkeypatch, given
):
    llm_class = _model_class(monkeypatch)
    model = _model_answering(monkeypatch, "never")
    with pytest.raises(ModelConfigurationRefused):
        model_route.platform_llm(
            NOT_A_FEATURE[given], organization=ORGANIZATION_ID, max_tokens=10
        )
    llm_class.assert_not_called()
    assert model.calls == [], "the model call was made"


@pytest.mark.parametrize(
    "name", ["CHAT_SIMULATOR_FIRST_MESSAGE", "VOICE_BACKGROUND_CHOICE"]
)
def test_the_builder_refuses_a_feature_that_calls_the_platform_helper(
    monkeypatch, name
):
    llm_class = _model_class(monkeypatch)
    with pytest.raises(ModelConfigurationRefused):
        model_route.platform_llm(
            getattr(platform_model.PlatformFeature, name),
            organization=ORGANIZATION_ID,
        )
    llm_class.assert_not_called()


@pytest.mark.parametrize("setting", CALL_SETTINGS)
def test_the_builder_cannot_be_given_a_model_or_a_key(monkeypatch, setting):
    llm_class = _model_class(monkeypatch)
    with pytest.raises(TypeError):
        model_route.platform_llm(
            platform_model.PlatformFeature.SMART_FILTER,
            organization=ORGANIZATION_ID,
            **{setting: "chosen-by-a-caller"},
        )
    llm_class.assert_not_called()


# ------------------------------------------------- what each feature keeps ---


def _unknown_tool_call():
    function = SimpleNamespace(name="look_elsewhere", arguments="{}")
    return SimpleNamespace(id="call-1", function=function)


def test_the_smart_filter_keeps_its_model_max_tokens_wall_and_five_turns(
    monkeypatch, not_on_credits
):
    llm_class = _model_class(monkeypatch)
    completion = llm_class.return_value._get_completion_with_tools
    completion.return_value = _response("", tool_calls=[_unknown_tool_call()])

    assert _run_the_smart_filter() == []

    built = llm_class.call_args.kwargs
    assert built["provider"] == "vertex_ai"
    assert built["model_name"] == "vertex_ai/gemini-3.5-flash-lite"
    assert built["temperature"] == 0.0
    assert built["max_tokens"] == 800
    assert completion.call_count == 5, "the model turns are not capped at five"
    for call in completion.call_args_list:
        assert 0 < call.kwargs["timeout_ms"] <= ai_filter.SMART_FILTER_REQUEST_WALL_MS


def _filter_request(mode):
    body = {"mode": mode, "query": "show failed calls", "schema": SCHEMA}
    request = APIRequestFactory().post("/model-hub/ai-filter/", body, format="json")
    request.organization = ORGANIZATION_ID
    request.workspace = None
    force_authenticate(
        request, user=SimpleNamespace(is_authenticated=True, organization=None)
    )
    return request


# mode -> what the model answers
OTHER_FILTER_MODES = {
    "build_filters": '[{"field": "status", "operator": "is", "value": "failed"}]',
    "select_fields": '{"fields": ["status"]}',
}


@pytest.mark.parametrize("mode", sorted(OTHER_FILTER_MODES))
def test_the_smart_filters_other_modes_keep_their_model_max_tokens_and_wall(
    monkeypatch, not_on_credits, mode
):
    llm_class = _model_class(monkeypatch)
    completion = llm_class.return_value._get_completion_with_tools
    completion.return_value = _response(OTHER_FILTER_MODES[mode])

    response = ai_filter.AIFilterView.as_view()(_filter_request(mode))

    assert response.status_code == 200
    built = llm_class.call_args.kwargs
    assert built["model_name"] == "vertex_ai/gemini-3.5-flash-lite"
    assert built["temperature"] == 0.0
    assert built["max_tokens"] == 500
    completion.assert_called_once()
    timeout_ms = completion.call_args.kwargs["timeout_ms"]
    assert 0 < timeout_ms <= ai_filter.SMART_FILTER_REQUEST_WALL_MS


def test_the_criteria_helper_keeps_its_model_and_max_tokens(
    monkeypatch, not_on_credits, no_saved_criteria
):
    llm_class = _model_class(monkeypatch)
    llm_class.return_value._get_completion_content.return_value = FEATURES[
        "CRITERIA_HELPER"
    ][1]

    assert _run_the_criteria_helper() == ["The answer is polite."]

    built = llm_class.call_args.kwargs
    assert built["provider"] == ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.provider
    assert built["model_name"] == ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name
    assert built["temperature"] == 0.2
    assert built["max_tokens"] == 8100


def test_a_saved_criteria_breakdown_makes_no_call(monkeypatch, not_on_credits):
    saved = SimpleNamespace(criteria_breakdown=["The answer is polite."])
    monkeypatch.setattr(agent_tasks, "CriteriaCache", _SavedCriteria(saved))
    llm_class = _model_class(monkeypatch)
    model = _model_answering(monkeypatch, "never")

    assert _run_the_criteria_helper() == ["The answer is polite."]

    llm_class.assert_not_called()
    assert model.calls == [], "the model call was made"


# output format -> (what the model answers, the max tokens it is built with)
EVAL_WRITER_FORMATS = {
    "messages": ('[{"role": "system", "content": "Judge politeness."}]', 1500),
    "prompt": ("Judge whether it is polite.", 1000),
}


@pytest.mark.parametrize("output_format", sorted(EVAL_WRITER_FORMATS))
def test_the_ai_eval_writer_keeps_its_model_and_max_tokens(
    monkeypatch, not_on_credits, output_format
):
    answer, max_tokens = EVAL_WRITER_FORMATS[output_format]
    llm_class = _model_class(monkeypatch)
    llm_class.return_value._get_completion_content.return_value = answer

    _run_the_ai_eval_writer(output_format)

    built = llm_class.call_args.kwargs
    assert built["provider"] == ModelConfigs.HAIKU_4_5_BEDROCK_ARN.provider
    assert built["model_name"] == ModelConfigs.HAIKU_4_5_BEDROCK_ARN.model_name
    assert built["temperature"] == 0.3
    assert built["max_tokens"] == max_tokens


# ---------------------------------------------------- the builder's callers ---

# The backend's source, which the census reads.
SOURCE_ROOT = Path(__file__).resolve().parents[2]
BUILDER = "platform_llm"
# Every use of the builder in the backend's source, with the feature it names.
BUILDER_CALLERS = {
    ("model_hub/views/ai_filter.py", "_run_smart_agent"): "SMART_FILTER",
    ("model_hub/views/ai_filter.py", "AIFilterView.post"): "SMART_FILTER",
    ("model_hub/tasks/agent.py", "create_criteria_text_prompt"): "CRITERIA_HELPER",
    (
        "model_hub/services/ai_eval_writer_service.py",
        "generate_eval_prompt",
    ): "AI_EVAL_WRITER",
}
# What a call of the builder is given besides the feature.
BUILDER_KEYWORDS = frozenset({"organization", "workspace", "max_tokens", "llm_class"})


def _is_source_file(path):
    parts = path.relative_to(SOURCE_ROOT).parts
    if any(
        part in ("tests", "migrations", "node_modules", "site-packages")
        or part.startswith(".")
        for part in parts[:-1]
    ):
        return False
    name = parts[-1]
    return not (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name in ("tests.py", "conftest.py")
    )


def _name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


class _BuilderUses(ast.NodeVisitor):
    """(qualified name of the enclosing function, the feature named) for every
    use of the builder: a call names a feature when it is given
    ``PlatformFeature.<member>`` (one of ``members``) as the feature and
    nothing else but BUILDER_KEYWORDS; any other call, and any use that is
    not a call, names none."""

    def __init__(self, members):
        self.members = members
        self.names = {BUILDER}
        self.scope = []
        self.found = []

    def _enter(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _enter

    def _where(self):
        return ".".join(self.scope) or "<module>"

    def _is_builder(self, node):
        return (isinstance(node, ast.Name) and node.id in self.names) or (
            isinstance(node, ast.Attribute) and node.attr == BUILDER
        )

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name == BUILDER and alias.asname:
                self.names.add(alias.asname)

    def visit_Call(self, node):
        if not self._is_builder(node.func):
            self.generic_visit(node)
            return
        self.found.append((self._where(), self._feature(node)))
        if isinstance(node.func, ast.Attribute):
            self.visit(node.func.value)
        for child in (*node.args, *node.keywords):
            self.visit(child)

    def visit_Name(self, node):
        if self._is_builder(node):
            self.found.append((self._where(), None))

    def visit_Attribute(self, node):
        if self._is_builder(node):
            self.found.append((self._where(), None))
        self.generic_visit(node)

    def _feature(self, call):
        given = list(call.args)
        keywords = {}
        for word in call.keywords:
            if word.arg is None:  # a mapping spread into the call
                return None
            keywords[word.arg] = word.value
        if "feature" in keywords:
            given.append(keywords.pop("feature"))
        if len(given) != 1 or not set(keywords) <= BUILDER_KEYWORDS:
            return None
        feature = given[0]
        if not (
            isinstance(feature, ast.Attribute)
            and _name(feature.value) == "PlatformFeature"
            and feature.attr in self.members
        ):
            return None
        return feature.attr


def _uses_in(relative, text, members):
    """{(file, function): [the feature each use there names, or None]}."""
    uses = _BuilderUses(members)
    uses.visit(ast.parse(text, filename=relative))
    found = {}
    for where, feature in uses.found:
        found.setdefault((relative, where), []).append(feature)
    return found


def _uses_in_the_source(members):
    found = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if not _is_source_file(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if BUILDER not in text:
            continue
        found.update(_uses_in(path.relative_to(SOURCE_ROOT).as_posix(), text, members))
    return found


def _census_problems(found, listed, members):
    """What the census refuses: an unlisted use, a listed one that is gone,
    and a listed place whose use does not name its feature by its member."""
    problems = [(place, "not listed") for place in sorted(set(found) - set(listed))]
    problems += [(place, "gone") for place in sorted(set(listed) - set(found))]
    for place in sorted(set(found) & set(listed)):
        if listed[place] not in members or found[place] != [listed[place]]:
            problems.append((place, "does not name its feature"))
    return problems


def test_every_use_of_the_builder_names_its_platform_feature():
    members = frozenset(platform_model.PlatformFeature.__members__)
    found = _uses_in_the_source(members)
    assert _census_problems(found, BUILDER_CALLERS, members) == [], (
        "every use of model_route.platform_llm is a call listed here that "
        "names its PlatformFeature member and nothing that chooses a model"
    )


ALIAS = "from model_hub.services.model_route import platform_llm as build\n"
# sample -> (its source, the feature the one use in it names; None: none)
CENSUS_SAMPLES = {
    "named": (
        "platform_llm(PlatformFeature.SMART_FILTER, organization=o, max_tokens=8)",
        "SMART_FILTER",
    ),
    "named_through_the_module": (
        "model_route.platform_llm(PlatformFeature.SMART_FILTER, workspace=w)",
        "SMART_FILTER",
    ),
    "named_by_keyword": (
        "platform_llm(feature=platform_model.PlatformFeature.SMART_FILTER)",
        "SMART_FILTER",
    ),
    "named_under_another_name": (
        ALIAS + "build(PlatformFeature.SMART_FILTER, organization=o)",
        "SMART_FILTER",
    ),
    "unnamed": ("platform_llm(feature, organization=o)", None),
    "unnamed_under_another_name": (ALIAS + "build(feature, organization=o)", None),
    "a_model_given_as_text": (
        "platform_llm('vertex_ai/gemini-chosen-by-a-caller', organization=o)",
        None,
    ),
    "a_model_given_by_its_settings": (
        "platform_llm(organization=o, operation=OP_SMART_FILTER, "
        "provider=cfg.provider, model_name=cfg.model_name)",
        None,
    ),
    "a_named_feature_given_a_model": (
        "platform_llm(PlatformFeature.SMART_FILTER, model_name='vertex_ai/x')",
        None,
    ),
    "a_named_feature_given_a_temperature": (
        "platform_llm(PlatformFeature.SMART_FILTER, temperature=0.9)",
        None,
    ),
    "not_a_member": (
        "platform_llm(PlatformFeature.CHOSEN_BY_A_CALLER, organization=o)",
        None,
    ),
    "settings_spread_into_the_call": (
        "platform_llm(PlatformFeature.SMART_FILTER, **settings)",
        None,
    ),
    "arguments_spread_into_the_call": ("platform_llm(*given)", None),
    "kept_under_another_name": ("build = platform_llm", None),
    "kept_as_a_callback": ("callback = model_route.platform_llm", None),
}


@pytest.mark.parametrize("sample", sorted(CENSUS_SAMPLES))
def test_the_census_refuses_an_unnamed_call_or_a_model_given_as_text(sample):
    members = frozenset({"SMART_FILTER"})
    source, named = CENSUS_SAMPLES[sample]
    found = _uses_in("sample.py", source, members)
    assert found == {("sample.py", "<module>"): [named]}
    listed = {("sample.py", "<module>"): "SMART_FILTER"}
    problems = _census_problems(found, listed, members)
    assert (problems == []) == (named == "SMART_FILTER"), problems


def test_the_census_names_the_enclosing_function_and_finds_a_new_use():
    members = frozenset({"SMART_FILTER"})
    source = (
        "class View:\n"
        "    def post(self):\n"
        "        return platform_llm(PlatformFeature.SMART_FILTER)\n"
    )
    found = _uses_in("sample.py", source, members)
    assert found == {("sample.py", "View.post"): ["SMART_FILTER"]}
    assert _census_problems(found, {}, members) == [
        (("sample.py", "View.post"), "not listed")
    ]
    listed = {("sample.py", "View.post"): "SMART_FILTER"}
    assert _census_problems({}, listed, members) == [
        (("sample.py", "View.post"), "gone")
    ]
