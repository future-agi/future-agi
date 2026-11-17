"""Every place in the code that makes Google credentials, or hands a Vertex
configuration to a model call, is listed here, read from the source.

A place that takes a customer's configuration is listed with the checks
that run before it, and each of those checks runs the shared one: so a
configuration that may not be a model's Google credential, or that says the
platform signs in anywhere but at Google's own addresses, is refused before
it gets there. A place that makes credentials only from the platform's own
settings is listed apart, with why. A new place, or a listed one that is
gone, fails the census until the list says what checks it.

A place that makes credentials from the key of a customer's export
connection is listed apart too: before it makes them, it runs the one rule
for where a key says the platform signs in, the shared one and not a copy of
its own, so the export accepts a key only when it names Google's own sign-in
addresses.

A place where only a customer's model is called is listed with the function
that refuses, before any call, a Vertex call that carries no service-account
key of the customer's own; each of those runs the shared refusal. The places
that still make such a call are listed apart, each with why.

The platform's own helper is the one place this census allows to call a
Vertex model without a customer's key: it takes which platform feature is
calling and the prompt, nothing else, and the two platform features call
it instead of a model of their own.

Nothing is imported from the files it reads; nothing is called.
"""

import ast
from pathlib import Path

# The backend's source, which these tests read.
SOURCE_ROOT = Path(__file__).resolve().parents[2]

# What a place calls on a Google credential class or module to make
# credentials from what it is given.
CREDENTIAL_BUILDERS = frozenset(
    {
        "from_service_account_info",
        "from_service_account_file",
        "from_service_account_json",
        "load_credentials_from_info",
        "load_credentials_from_dict",
        "load_credentials_from_file",
    }
)
# Google credential modules whose Credentials class (or its from_info /
# from_file) makes credentials from what it is given.
CREDENTIAL_MODULES = frozenset(
    {
        "aws",
        "external_account",
        "external_account_authorized_user",
        "identity_pool",
        "impersonated_credentials",
        "pluggable",
        "service_account",
    }
)
# The names a model call takes a Vertex configuration under.
VERTEX_CONFIGURATION_NAMES = frozenset({"vertex_credentials", "vertex_ai_credentials"})

# The shared check, by the names a checking function calls it under.
SHARED_CHECK = frozenset(
    {
        "is_google_credential_other_than_a_service_account_key",
        "refuse_google_credential_other_than_a_service_account_key",
    }
)
MODEL_ENDPOINTS = "model_hub/utils/model_endpoints.py"
GUARD = (MODEL_ENDPOINTS, "_guard")
# The two reads of a stored row for a call (``get_api_key`` and
# ``get_provider_api_key`` go through them): a custom model's, and a
# provider key's.
STORED_ROW_LOOKUPS = (
    (
        "agentic_eval/core_evals/run_prompt/litellm_models.py",
        "LiteLLMModelManager.stored_credential_lookup",
    ),
    (
        "agentic_eval/core_evals/run_prompt/litellm_models.py",
        "LiteLLMModelManager._stored_provider_credential_lookup",
    ),
)

# A place that hands a customer's Vertex configuration to a model call, and
# the checking functions that run before it. Each of them calls the shared
# check.
CHECKED_FIRST = {
    ("model_hub/utils/utils.py", "validate_model_working"): (
        ("model_hub/utils/utils.py", "validate_model_working"),
    ),
    ("agentic_eval/core/llm/llm.py", "LLM.call_llm"): (GUARD, *STORED_ROW_LOOKUPS),
    (
        "agentic_eval/core_evals/run_prompt/runprompt_handlers/utils/payload_builder.py",
        "PayloadBuilder._add_api_key",
    ): (GUARD, *STORED_ROW_LOOKUPS),
    # Run Prompt's image call to a customer's Vertex model, with the model's
    # stored configuration.
    (
        "agentic_eval/core_evals/run_prompt/runprompt_handlers/handlers/image_handler.py",
        "ImageHandler._image_generation_response",
    ): (GUARD, *STORED_ROW_LOOKUPS),
    # Run Prompt's standard speech call to a customer's Vertex model, with the
    # model's stored configuration: this call does not go through the guard.
    (
        "agentic_eval/core_evals/run_prompt/runprompt_handlers/handlers/tts/speech_api_handler.py",
        "SpeechAPIHandler._build_speech_params",
    ): STORED_ROW_LOOKUPS,
    # Read only when the module's old run-prompt path is switched back on;
    # its key comes from the stored-row lookups with the model's provider.
    (
        "agentic_eval/core_evals/run_prompt/litellm_response.py",
        "RunPrompt._create_payload",
    ): STORED_ROW_LOOKUPS,
    # What the prompt optimiser's calls to a customer's Vertex model are
    # given, with the model's stored configuration: its key comes from the
    # stored-row lookups.
    (MODEL_ENDPOINTS, "vertex_call_arguments"): STORED_ROW_LOOKUPS,
}

# Places this census does not check, and why.
NOT_CHECKED_HERE = {
    (
        "accounts/services/gcp_procurement.py",
        "GCPProcurementService._credentials",
    ): "the platform's own marketplace identity, from its settings",
    (
        "accounts/services/gcp_service_control.py",
        "GCPServiceControlService._credentials",
    ): "the platform's own marketplace identity, from its settings",
}

# The one rule for where a key says the platform signs in, shared by every
# place that takes a customer's Google key.
WHERE_A_KEY_SIGNS_IN = "signs_in_only_at_googles_own_addresses"
EXPORT_STORAGE = "integrations/services/cloud_storage_service.py"
EXPORT_QUEUE = "integrations/services/queue_service.py"
# A place that makes Google credentials from the key of a customer's export
# connection. Before it makes them, it runs the one rule, imported from
# MODEL_ENDPOINTS, so the key is accepted only when it names Google's own
# sign-in addresses.
CHECKED_WHERE_A_KEY_SIGNS_IN_FIRST = frozenset(
    {
        # The storage export's connection check and its scheduled upload.
        (EXPORT_STORAGE, "CloudStorageService._validate_gcs"),
        (EXPORT_STORAGE, "CloudStorageService._upload_gcs"),
        # The queue export's connection check and its scheduled publish.
        (EXPORT_QUEUE, "QueueService._validate_pubsub"),
        (EXPORT_QUEUE, "QueueService._publish_pubsub"),
    }
)

# The shared refusal of a call to a customer's Vertex model that carries no
# service-account key of the customer's own, by the names a function calls
# it under.
NO_KEY_OF_ITS_OWN_REFUSAL = frozenset(
    {
        "refuse_vertex_call_without_a_service_account_key",
        "vertex_call_has_no_service_account_key",
    }
)
# What a place calls to call a model.
MODEL_CALLS = frozenset(
    {
        "acompletion",
        "aspeech",
        "completion",
        "guarded_acompletion",
        "guarded_completion",
        "guarded_image_generation",
        "image_generation",
        "speech",
        "transcription",
    }
)
UTILS = "model_hub/utils/utils.py"
RUN_PROMPT_HANDLERS = "agentic_eval/core_evals/run_prompt/runprompt_handlers"
PAYLOAD_BUILDER = f"{RUN_PROMPT_HANDLERS}/utils/payload_builder.py"
IMAGE_HANDLER = f"{RUN_PROMPT_HANDLERS}/handlers/image_handler.py"
STT_HANDLER = f"{RUN_PROMPT_HANDLERS}/handlers/stt_handler.py"
SPEECH_HANDLER = f"{RUN_PROMPT_HANDLERS}/handlers/tts/speech_api_handler.py"
SPEECH_PARAMETERS = (SPEECH_HANDLER, "SpeechAPIHandler._build_speech_params")
OLD_RUN_PROMPT = "agentic_eval/core_evals/run_prompt/litellm_response.py"
OLD_RUN_PROMPT_WHY = (
    "the old Run Prompt path, which runs only when the handlers are switched off"
)
LLM_MODULE = "agentic_eval/core/llm/llm.py"
EVALUATION_CALLS_WHY = (
    "an evaluation's model call given no key, after the managed route and the "
    "gateway (the platform's own Vertex calls): not in this change; a call "
    "given a key is refused for a Vertex model first (LLM._given_key_arguments)"
)
LAST_FALLBACK_WHY = (
    "the last fallback of an evaluation's model call calls the platform's own "
    "Vertex model with no key: not in this change"
)
OPTIMISER_GENERATOR = "ee/agent_opt/generators/litellm_generator.py"
OPTIMISER_GEPA = "ee/agent_opt/optimizers/gepa.py"
OPTIMISER_RANDOM_SEARCH = "ee/agent_opt/optimizers/random_search.py"
# What a call to a customer's Vertex model from the prompt optimiser is
# given: it runs the shared refusal before the call.
VERTEX_CALL_ARGUMENTS = (MODEL_ENDPOINTS, "vertex_call_arguments")

# A place where only a customer's model is called, and the function that
# refuses there, before any call, a Vertex call with no service-account key
# of the customer's own. Each of them calls the shared refusal.
REFUSED_FIRST_WITHOUT_A_KEY_OF_ITS_OWN = {
    # The custom-model forms' validation call.
    (UTILS, "validate_model_working"): (UTILS, "validate_model_working"),
    # Run Prompt's chat calls: the payload each chat handler sends.
    (PAYLOAD_BUILDER, "PayloadBuilder._add_api_key"): (
        PAYLOAD_BUILDER,
        "PayloadBuilder._add_api_key",
    ),
    (IMAGE_HANDLER, "ImageHandler._image_generation_response"): (
        IMAGE_HANDLER,
        "ImageHandler._image_generation_response",
    ),
    (STT_HANDLER, "StandardSTTHandler._execute_transcription"): (
        STT_HANDLER,
        "StandardSTTHandler._execute_transcription",
    ),
    (SPEECH_HANDLER, "SpeechAPIHandler.execute_sync"): SPEECH_PARAMETERS,
    (SPEECH_HANDLER, "SpeechAPIHandler.execute_async"): SPEECH_PARAMETERS,
    # An evaluation's model call (custom prompt evaluations included): the
    # platform's own features call their helper, and a model on the
    # organization's Future AGI credits goes to the managed route before it,
    # so only a customer's Vertex model reaches the refusal.
    (LLM_MODULE, "LLM.call_llm"): (LLM_MODULE, "LLM.call_llm"),
    # The prompt optimiser's calls: its generator, its reflection model's
    # check and its reflection model, and its prompt variations.
    (OPTIMISER_GENERATOR, "LiteLLMGenerator.generate"): VERTEX_CALL_ARGUMENTS,
    (OPTIMISER_GEPA, "GEPAOptimizer._verify_reflection_auth"): VERTEX_CALL_ARGUMENTS,
    (OPTIMISER_GEPA, "GEPAOptimizer._build_reflection_lm"): VERTEX_CALL_ARGUMENTS,
    (OPTIMISER_RANDOM_SEARCH, "_generate_variations"): VERTEX_CALL_ARGUMENTS,
}

# Places that still call a Vertex model with no service-account key of the
# customer's own, and why.
STILL_OPEN_WITHOUT_A_KEY_OF_ITS_OWN = {
    (OLD_RUN_PROMPT, "RunPrompt._speech_response"): OLD_RUN_PROMPT_WHY,
    (OLD_RUN_PROMPT, "RunPrompt._image_generation_response"): OLD_RUN_PROMPT_WHY,
    (OLD_RUN_PROMPT, "RunPrompt._transcription_response"): OLD_RUN_PROMPT_WHY,
    (LLM_MODULE, "LLM._get_completion_content"): EVALUATION_CALLS_WHY,
    (LLM_MODULE, "LLM._get_completion_content_async"): EVALUATION_CALLS_WHY,
    (LLM_MODULE, "LLM._get_completion_with_tools"): EVALUATION_CALLS_WHY,
    (LLM_MODULE, "LLM._handle_final_fallback"): LAST_FALLBACK_WHY,
    (LLM_MODULE, "LLM._handle_final_fallback_async"): LAST_FALLBACK_WHY,
}

# The one place allowed to call a Vertex model without a customer's key: the
# platform's own helper, for the platform's own features. It takes which
# feature is calling and the prompt; the model and its location are the
# platform's own.
PLATFORM_HELPER = ("agentic_eval/core/llm/platform_model.py", "call_platform_model")
PLATFORM_HELPER_PARAMETERS = ("feature", "messages")
# The platform's own features, each of which calls the helper.
PLATFORM_FEATURES = (
    ("simulate/services/chat_initial_message.py", "_generate_chat_initial_message"),
    ("ee/voice/services/background_sound_selector.py", "select_background_sound"),
)


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


def _source_files():
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if _is_source_file(path):
            yield path


def _text(node):
    return node.value if isinstance(node, ast.Constant) else None


def _hands_on_a_vertex_configuration(node):
    """Whether ``node`` puts a value under a Vertex configuration name for a
    model call: a key of a dict, an item assigned, a keyword argument, or
    ``setdefault``."""
    if isinstance(node, ast.Dict):
        return any(_text(key) in VERTEX_CONFIGURATION_NAMES for key in node.keys)
    if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(
            isinstance(target, ast.Subscript)
            and _text(target.slice) in VERTEX_CONFIGURATION_NAMES
            for target in targets
        )
    if isinstance(node, ast.Call):
        if any(word.arg in VERTEX_CONFIGURATION_NAMES for word in node.keywords):
            return True
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefault"
            and bool(node.args)
            and _text(node.args[0]) in VERTEX_CONFIGURATION_NAMES
        )
    return False


def _name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _google_credential_names(tree):
    """(modules, classes): the names a file gives a Google credential module
    (its own name, or the one it is imported as) and that module's
    Credentials class (imported by name)."""
    modules, classes = set(CREDENTIAL_MODULES), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "google."
        ):
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name in CREDENTIAL_MODULES:
                    modules.add(bound)
                elif (
                    alias.name == "Credentials"
                    and node.module.rsplit(".", 1)[-1] in CREDENTIAL_MODULES
                ):
                    classes.add(bound)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name.startswith("google.")
                    and alias.asname
                    and alias.name.rsplit(".", 1)[-1] in CREDENTIAL_MODULES
                ):
                    modules.add(alias.asname)
    return modules, classes


def _makes_google_credentials(node, modules, classes):
    """Whether ``node`` makes Google credentials from what it is given."""
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    name = _name(function)
    if name in CREDENTIAL_BUILDERS:
        return True
    # Credentials(...), Credentials.from_info(...), Credentials.from_file(...)
    # of a Google credential module.
    if name in ("from_info", "from_file") and isinstance(function, ast.Attribute):
        function = function.value
    if isinstance(function, ast.Name):
        return function.id in classes
    return (
        isinstance(function, ast.Attribute)
        and function.attr == "Credentials"
        and _name(function.value) in modules
    )


class _Places(ast.NodeVisitor):
    """(file, qualified name of the enclosing function) of every place."""

    def __init__(self, relative, tree):
        self.relative = relative
        self.modules, self.classes = _google_credential_names(tree)
        self.scope = []
        self.found = set()

    def _enter(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _enter

    def generic_visit(self, node):
        if _makes_google_credentials(
            node, self.modules, self.classes
        ) or _hands_on_a_vertex_configuration(node):
            self.found.add((self.relative, ".".join(self.scope) or "<module>"))
        super().generic_visit(node)


def _places():
    needles = (*CREDENTIAL_BUILDERS, *VERTEX_CONFIGURATION_NAMES, "Credentials")
    found = set()
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if not any(needle in text for needle in needles):
            continue
        tree = ast.parse(text, filename=str(path))
        places = _Places(path.relative_to(SOURCE_ROOT).as_posix(), tree)
        places.visit(tree)
        found |= places.found
    return found


def _function(relative, qualified_name):
    """The function ``qualified_name`` (``Class.method`` or ``function``)
    defined in ``relative``, read from the source."""
    tree = ast.parse((SOURCE_ROOT / relative).read_text(encoding="utf-8"))
    scope = tree
    for part in qualified_name.split("."):
        scope = next(
            node
            for node in ast.iter_child_nodes(scope)
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == part
        )
    return scope


def _called(relative, qualified_name):
    """The names the function calls: a plain name, or an attribute's."""
    return {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(_function(relative, qualified_name))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute | ast.Name)
    }


def test_every_place_that_makes_google_credentials_or_hands_on_a_vertex_configuration_is_listed():
    found = _places()
    checked = set(CHECKED_FIRST) | CHECKED_WHERE_A_KEY_SIGNS_IN_FIRST
    listed = checked | set(NOT_CHECKED_HERE)
    assert set(CHECKED_FIRST).isdisjoint(NOT_CHECKED_HERE)
    assert CHECKED_WHERE_A_KEY_SIGNS_IN_FIRST.isdisjoint(CHECKED_FIRST)
    assert CHECKED_WHERE_A_KEY_SIGNS_IN_FIRST.isdisjoint(NOT_CHECKED_HERE)
    assert sorted(found - listed) == [], (
        "a new place makes Google credentials or hands a Vertex configuration "
        "to a model call: list it with the checks that run before it"
    )
    assert sorted(listed - found) == [], "a listed place is gone: remove it"


def test_every_check_listed_before_a_vertex_configuration_runs_the_shared_one():
    for place, checks in sorted(CHECKED_FIRST.items()):
        assert checks, place
        for check in checks:
            assert _called(*check) & SHARED_CHECK, (place, check)


def test_the_shared_check_also_decides_where_a_vertex_key_says_the_platform_signs_in():
    # The shared check for a Vertex configuration runs the one rule for the
    # addresses a key gives for where the platform signs in.
    assert "vertex_config_is_not_a_service_account_key" in _called(
        MODEL_ENDPOINTS, "is_google_credential_other_than_a_service_account_key"
    )
    assert "signs_in_only_at_googles_own_addresses" in _called(
        MODEL_ENDPOINTS, "vertex_config_is_not_a_service_account_key"
    )


def _first_call(relative, qualified_name, names):
    """The line of the function's first call, in its source, to any of
    ``names``; None when it calls none of them."""
    return min(
        (
            node.lineno
            for node in ast.walk(_function(relative, qualified_name))
            if isinstance(node, ast.Call) and _name(node.func) in names
        ),
        default=None,
    )


def _first_credentials(relative, qualified_name):
    """The line of the function's first call, in its source, that makes Google
    credentials; None when it makes none."""
    tree = ast.parse((SOURCE_ROOT / relative).read_text(encoding="utf-8"))
    modules, classes = _google_credential_names(tree)
    return min(
        (
            node.lineno
            for node in ast.walk(_function(relative, qualified_name))
            if _makes_google_credentials(node, modules, classes)
        ),
        default=None,
    )


def _runs_the_shared_rule(relative):
    """Whether the file imports the one rule for where a key signs in from
    MODEL_ENDPOINTS and defines no function of that name of its own."""
    tree = ast.parse((SOURCE_ROOT / relative).read_text(encoding="utf-8"))
    module = MODEL_ENDPOINTS.removesuffix(".py").replace("/", ".")
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == module
        and WHERE_A_KEY_SIGNS_IN in {alias.name for alias in node.names}
        for node in ast.walk(tree)
    )
    defined = any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == WHERE_A_KEY_SIGNS_IN
        for node in ast.walk(tree)
    )
    return imported and not defined


def test_every_export_connection_checks_where_a_key_signs_in_before_it_makes_credentials():
    for place in sorted(CHECKED_WHERE_A_KEY_SIGNS_IN_FIRST):
        checked = _first_call(*place, {WHERE_A_KEY_SIGNS_IN})
        made = _first_credentials(*place)
        assert checked is not None, (place, "does not check where the key signs in")
        assert made is not None and checked < made, (
            place,
            "makes credentials before it checks where the key signs in",
        )
        assert _runs_the_shared_rule(place[0]), (
            place,
            "checks where the key signs in with a rule other than the shared one",
        )


def test_every_place_a_customers_vertex_model_is_called_refuses_a_call_without_its_own_key_first():
    assert set(REFUSED_FIRST_WITHOUT_A_KEY_OF_ITS_OWN).isdisjoint(
        STILL_OPEN_WITHOUT_A_KEY_OF_ITS_OWN
    )
    for place, refuses in sorted(REFUSED_FIRST_WITHOUT_A_KEY_OF_ITS_OWN.items()):
        assert _called(*refuses) & NO_KEY_OF_ITS_OWN_REFUSAL, (place, refuses)
        refusing = NO_KEY_OF_ITS_OWN_REFUSAL | {refuses[1].rsplit(".", 1)[-1]}
        model_call = _first_call(*place, MODEL_CALLS)
        if refuses != place or model_call is not None:
            first = _first_call(*place, refusing)
            assert first is not None, (place, "does not refuse")
            assert model_call is None or first < model_call, (
                place,
                "calls the model before it refuses",
            )


def test_every_place_still_open_without_a_key_of_its_own_is_listed_with_why():
    for place, why in sorted(STILL_OPEN_WITHOUT_A_KEY_OF_ITS_OWN.items()):
        assert why, place
        assert not _called(*place) & NO_KEY_OF_ITS_OWN_REFUSAL, (
            place,
            "it refuses now: list it with the places that refuse first",
        )


def test_only_the_platforms_helper_may_call_a_vertex_model_without_a_customers_key():
    assert PLATFORM_HELPER not in REFUSED_FIRST_WITHOUT_A_KEY_OF_ITS_OWN
    assert PLATFORM_HELPER not in STILL_OPEN_WITHOUT_A_KEY_OF_ITS_OWN
    arguments = _function(*PLATFORM_HELPER).args
    parameters = [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
    ]
    given = tuple(parameter.arg for parameter in parameters)
    assert given == PLATFORM_HELPER_PARAMETERS, (
        "the helper takes something other than which feature is calling and the prompt"
    )
    assert arguments.vararg is None and arguments.kwarg is None
    for feature in PLATFORM_FEATURES:
        called = _called(*feature)
        assert PLATFORM_HELPER[1] in called, (feature, "does not call the helper")
        assert not called & (MODEL_CALLS | {"LLM", "call_llm"}), (
            feature,
            "calls a model of its own",
        )
