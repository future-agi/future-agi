"""Hub calls made for an organization's dataset import carry the organization's
saved Hugging Face key, or no token at all.

The Hub is replaced at its client boundary only: ``requests.get`` for the
datasets-server and Hub API calls, ``HfApi`` for the revision lookup, and the
``datasets`` package for loads and dataset infos. Each stand-in records what
the call carried.
"""

import ast
import importlib
import importlib.machinery
import json
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import requests

from model_hub.models.api_key import ApiKey
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Column, Dataset, Row
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401
from tfc.utils.error_codes import get_error_message

pytestmark = pytest.mark.usefixtures("no_external_lookups")

DATASET = "fixture/sample"
SAVED_KEY = "hf_saved_key_for_tests"
NEEDS_TOKEN_SENTENCE = (
    "This dataset needs a Hugging Face access token. "
    "Save your own token in Settings, then try again."
)
HUB_TOKEN_SETTINGS = (
    "HUGGINGFACE_API_TOKEN",
    "HUGGINGFACE_API_TOKEN_1",
    "HUGGINGFACE_API_TOKEN_2",
)
# Stand-in values for the Hub token settings: no Hub call may carry one.
SETTING_STAND_INS = {
    name: f"settings-stand-in-{index}" for index, name in enumerate(HUB_TOKEN_SETTINGS)
}
MODULES_THAT_MAY_HOLD_THE_SETTINGS = (
    "tfc.settings.settings",
    "model_hub.utils.utils",
    "model_hub.views.utils.hugginface",
    "model_hub.views.datasets.create.huggingface",
    "model_hub.views.datasets.add_rows.huggingface",
    "model_hub.views.develop_dataset",
    "model_hub.services.huggingface_revision",
    "model_hub.services.dataset_service",
)
CONFIG_URL = "/model-hub/develops/get-huggingface-dataset-config/"
CREATE_URL = "/model-hub/develops/create-dataset-from-huggingface/"
DETAIL_URL = "/model-hub/datasets/huggingface/detail/"
TEXT_FEATURE = {"dtype": "string", "_type": "Value"}
ROWS = [{"text": "first"}, {"text": "second"}]


# --- the Hub at its client boundary ---------------------------------------


class _Answer:
    """A datasets-server or Hub API answer, shaped as ``requests`` returns it."""

    def __init__(self, url, status_code, payload):
        self.url = url
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Error for url: {self.url}", response=self
            )


def _hub_error(status_code):
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

    response = requests.Response()
    response.status_code = status_code
    response.url = f"https://huggingface.co/api/datasets/{DATASET}"
    kind = GatedRepoError if status_code in (401, 403) else HfHubHTTPError
    return kind(
        f"{status_code} Client Error for url: {response.url}", response=response
    )


def _payload(url):
    path = urlparse(url).path
    if path.endswith("/info"):
        return {"dataset_info": {"default": {"splits": {"train": {}}}}}
    if path.endswith("/size"):
        return {
            "size": {
                "splits": [
                    {
                        "dataset": DATASET,
                        "config": "default",
                        "split": "train",
                        "num_rows": len(ROWS),
                    }
                ]
            }
        }
    if path.endswith("/first-rows"):
        return {
            "features": [{"feature_idx": 0, "name": "text", "type": TEXT_FEATURE}],
            "rows": [],
        }
    if path.endswith("/api/datasets"):
        return [
            {
                "id": DATASET,
                "name": DATASET,
                "description": "",
                "downloads": 1,
                "likes": 0,
                "tags": [],
                "author": "fixture",
            }
        ]
    return {}


def _is_hub_url(url):
    from huggingface_hub.constants import ENDPOINT

    host = urlparse(url).hostname or ""
    return (
        host == "huggingface.co"
        or host.endswith(".huggingface.co")
        or url.startswith(ENDPOINT)
    )


class _Snapshot:
    """What ``datasets.load_dataset`` returns: features and iterable rows."""

    features = SimpleNamespace(to_dict=lambda: {"text": TEXT_FEATURE})

    def __iter__(self):
        return iter([dict(row) for row in ROWS])


class HubRecorder:
    """Answers every Hub call and records what each one carried."""

    def __init__(self):
        self.calls = []
        self.failures_left = 0
        self.refuse_status = None

    def _status(self):
        if self.failures_left:
            self.failures_left -= 1
            return 500
        return self.refuse_status or 200

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        self.calls.append({"via": "http", "url": url, "headers": dict(headers or {})})
        status_code = self._status()
        payload = _payload(url) if status_code == 200 else {"error": "refused"}
        return _Answer(url, status_code, payload)

    def hf_api(self, *args, **kwargs):
        recorder = self
        if "token" in kwargs:
            self.calls.append({"via": "HfApi()", "token": kwargs["token"]})

        class _Api:
            def dataset_info(self, repo_id, *, revision=None, token=None, **rest):
                recorder.calls.append({"via": "HfApi.dataset_info", "token": token})
                status_code = recorder._status()
                if status_code != 200:
                    raise _hub_error(status_code)
                return SimpleNamespace(sha="resolved-commit")

        return _Api()

    def _library_call(self, via, token):
        self.calls.append({"via": via, "token": token})
        status_code = self._status()
        if status_code == 500:
            raise _hub_error(status_code)
        if status_code != 200:
            # The datasets library answers a gated dataset with its own
            # not-found error, raised from the Hub's answer.
            raise FileNotFoundError(
                f"Dataset '{DATASET}' is a gated dataset on the Hub."
            ) from _hub_error(status_code)

    def datasets_package(self):
        package = types.ModuleType("datasets")
        package.__spec__ = importlib.machinery.ModuleSpec("datasets", None)

        def load_dataset(path, name=None, split=None, streaming=False, **kwargs):
            self._library_call("datasets.load_dataset", kwargs.get("token"))
            return _Snapshot()

        def get_dataset_infos(path, revision=None, token=None, **kwargs):
            self._library_call("datasets.get_dataset_infos", token)
            return {
                "default": SimpleNamespace(
                    splits={"train": SimpleNamespace(num_examples=len(ROWS))}
                )
            }

        package.load_dataset = load_dataset
        package.get_dataset_infos = get_dataset_infos
        return package

    def hub_calls(self):
        return [
            call
            for call in self.calls
            if call["via"] != "http" or _is_hub_url(call["url"])
        ]


def _describe(call):
    return f"{call['via']} {call['url']}" if call["via"] == "http" else call["via"]


def _authorization(call):
    for name, value in call.get("headers", {}).items():
        if name.lower() == "authorization":
            return value
    return None


def _carries_a_setting_value(call):
    carried = repr(call)
    return any(value in carried for value in SETTING_STAND_INS.values())


def _carries_no_token(call):
    if call["via"] == "http":
        return _authorization(call) is None
    # huggingface_hub and datasets read a locally saved login for None;
    # False is their "no token".
    return call["token"] is False


def _carries_the_saved_key(call):
    if call["via"] == "http":
        return _authorization(call) == f"Bearer {SAVED_KEY}"
    return call["token"] == SAVED_KEY


def _assert_every_hub_call(hub, carries, what):
    calls = hub.hub_calls()
    made = len(calls)
    assert made, "the flow made no Hub call"
    with_a_setting_value = [_describe(c) for c in calls if _carries_a_setting_value(c)]
    assert with_a_setting_value == [], "Hub calls carried a Hub token setting value"
    others = [_describe(c) for c in calls if not carries(c)]
    assert others == [], f"Hub calls that did not carry {what}"


def _assert_no_token(hub):
    _assert_every_hub_call(hub, _carries_no_token, "no token")


def _assert_saved_key(hub):
    _assert_every_hub_call(hub, _carries_the_saved_key, "the saved key")


@pytest.fixture
def hub(monkeypatch):
    recorder = HubRecorder()
    monkeypatch.setattr(requests, "get", recorder.get)
    monkeypatch.setattr(
        "model_hub.services.huggingface_revision.HfApi", recorder.hf_api
    )
    monkeypatch.setitem(sys.modules, "datasets", recorder.datasets_package())
    utils = importlib.import_module("model_hub.utils.utils")
    monkeypatch.setattr(utils, "time", SimpleNamespace(sleep=lambda seconds: None))
    for module_name in MODULES_THAT_MAY_HOLD_THE_SETTINGS:
        module = importlib.import_module(module_name)
        for name, value in SETTING_STAND_INS.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    return recorder


@pytest.fixture
def saved_key(organization, user, workspace):
    return ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        workspace=workspace,
        provider="huggingface",
        key=SAVED_KEY,
    )


# --- the import flows ------------------------------------------------------


@dataclass
class Scene:
    client: object
    organization: object
    workspace: object
    user: object
    monkeypatch: object
    started: list = field(default_factory=list)


@pytest.fixture
def scene(auth_client, organization, workspace, user, monkeypatch):
    # Usage entries stay out of these flows.
    for module_name in (
        "model_hub.views.datasets.create.huggingface",
        "model_hub.views.datasets.add_rows.huggingface",
    ):
        module = importlib.import_module(module_name)
        for name in list(vars(module)):
            if name.startswith("log_and_deduct_"):
                monkeypatch.setattr(module, name, None)
    service = importlib.import_module("model_hub.services.dataset_service")
    monkeypatch.setattr(service, "_check_resource_limit", lambda *a, **k: True)
    current = Scene(auth_client, organization, workspace, user, monkeypatch)

    def start_activity(activity_name, args, queue):
        current.started.append((activity_name, args, queue))
        return "activity-id"

    monkeypatch.setattr("tfc.temporal.drop_in.start_activity", start_activity)
    return current


def _endpoint(response):
    return response.status_code == 200, json.dumps(response.json())


def _load(scene, **kwargs):
    from model_hub.utils.utils import load_hf_dataset_with_retries

    result = load_hf_dataset_with_retries(
        DATASET, "default", "train", str(scene.organization.id), **kwargs
    )
    return bool(result), ""


def flow_preview(scene):
    return _load(scene, streaming=False)


def flow_streaming(scene):
    return _load(scene, streaming=True)


def flow_revision_preview(scene):
    return _load(scene, streaming=False, revision="resolved-commit")


def flow_config_ml(scene):
    return _endpoint(
        scene.client.post(CONFIG_URL, {"dataset_path": DATASET}, format="json")
    )


def flow_config_base(scene):
    # A base-only install has no `datasets` package.
    scene.monkeypatch.setitem(sys.modules, "datasets", None)
    return _endpoint(
        scene.client.post(CONFIG_URL, {"dataset_path": DATASET}, format="json")
    )


def _create_payload(**extra):
    return {
        "name": "Hub import",
        "model_type": "GenerativeLLM",
        "num_rows": len(ROWS),
        "huggingface_dataset_name": DATASET,
        "huggingface_dataset_config": "default",
        "huggingface_dataset_split": "train",
        **extra,
    }


def flow_create(scene):
    return _endpoint(scene.client.post(CREATE_URL, _create_payload(), format="json"))


def flow_create_at_revision(scene):
    return _endpoint(
        scene.client.post(CREATE_URL, _create_payload(revision="v1"), format="json")
    )


def flow_add_rows(scene):
    dataset = Dataset.objects.create(
        name="Hub rows",
        organization=scene.organization,
        workspace=scene.workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    dataset.column_order = []
    dataset.save()
    # No row count: the import asks the Hub for the split's size.
    return _endpoint(
        scene.client.post(
            f"/model-hub/develops/{dataset.id}/add_rows_from_huggingface/",
            {
                "huggingface_dataset_name": DATASET,
                "huggingface_dataset_config": "default",
                "huggingface_dataset_split": "train",
            },
            format="json",
        )
    )


def flow_service(scene):
    from model_hub.services.dataset_service import create_dataset_from_huggingface

    result = create_dataset_from_huggingface(
        hf_dataset_name=DATASET,
        hf_config="default",
        hf_split="train",
        name="Hub service import",
        organization=scene.organization,
        workspace=scene.workspace,
        user=scene.user,
        model_type="GenerativeLLM",
        num_rows=len(ROWS),
    )
    if isinstance(result, dict):
        return True, ""
    return False, result.message


def flow_detail(scene):
    return _endpoint(
        scene.client.post(DETAIL_URL, {"dataset_id": DATASET}, format="json")
    )


FLOWS = {
    "preview": flow_preview,
    "streaming": flow_streaming,
    "revision-preview": flow_revision_preview,
    "config-ml": flow_config_ml,
    "config-base": flow_config_base,
    "create": flow_create,
    "create-at-revision": flow_create_at_revision,
    "add-rows": flow_add_rows,
    "service": flow_service,
    "detail": flow_detail,
}
NEEDS_TOKEN_FLOWS = tuple(flow for flow in FLOWS if flow != "detail")


# --- no saved key: no token ------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("flow", list(FLOWS))
def test_without_a_saved_key_no_hub_call_sends_a_token(flow, hub, scene):
    succeeded, answer = FLOWS[flow](scene)
    assert succeeded, answer
    _assert_no_token(hub)


# --- a saved key: that key -------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("flow", list(FLOWS))
def test_with_a_saved_key_every_hub_call_sends_that_key(flow, hub, scene, saved_key):
    succeeded, answer = FLOWS[flow](scene)
    assert succeeded, answer
    _assert_saved_key(hub)


# --- a retry keeps the same token ------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "flow,with_saved_key",
    [
        pytest.param(flow, with_key, id=f"{prefix}-{flow}")
        for prefix, with_key in (("saved-key", True), ("no-key", False))
        for flow in ("preview", "streaming", "revision-preview")
    ],
)
def test_a_retry_after_a_hub_failure_keeps_the_same_token(
    flow, with_saved_key, hub, scene, request
):
    if with_saved_key:
        request.getfixturevalue("saved_key")
    hub.failures_left = 1

    succeeded, answer = FLOWS[flow](scene)

    assert succeeded, answer
    made = len(hub.hub_calls())
    assert made == 2, "one failed Hub call, then one retry"
    if with_saved_key:
        _assert_saved_key(hub)
    else:
        _assert_no_token(hub)


# --- a dataset that needs a token, with no saved key ----------------------


@pytest.mark.django_db
@pytest.mark.parametrize("flow", NEEDS_TOKEN_FLOWS)
def test_a_dataset_that_needs_a_token_answers_the_fixed_sentence_without_a_saved_key(
    flow, hub, scene
):
    hub.refuse_status = 401

    try:
        succeeded, answer = FLOWS[flow](scene)
    except Exception as error:  # noqa: BLE001 - the raised message is the answer
        succeeded, answer = False, str(error)

    assert not succeeded
    assert NEEDS_TOKEN_SENTENCE in answer, answer
    made = len(hub.hub_calls())
    assert made == 1, "the refusal is answered at once, without a retry"
    _assert_no_token(hub)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "flow,error_code",
    [
        ("config-ml", "FAILED_TO_FETCH_DATASET"),
        ("config-base", "FAILED_TO_FETCH_DATASET"),
        ("create", "FAILED_TO_PREVIEW_DATASET"),
    ],
)
def test_a_refusal_with_a_saved_key_keeps_its_existing_answer(
    flow, error_code, hub, scene, saved_key
):
    hub.refuse_status = 401

    succeeded, answer = FLOWS[flow](scene)

    assert not succeeded
    assert json.dumps(get_error_message(error_code))[1:-1] in answer, answer
    assert NEEDS_TOKEN_SENTENCE not in answer


# --- public datasets import as before ---------------------------------------


@pytest.mark.django_db
def test_a_public_dataset_without_a_saved_key_imports_as_before(hub, scene):
    succeeded, answer = flow_create(scene)

    assert succeeded, answer
    dataset = Dataset.objects.get(id=json.loads(answer)["result"]["dataset_id"])
    assert Row.objects.filter(dataset=dataset, deleted=False).count() == len(ROWS)
    assert [c.name for c in Column.objects.filter(dataset=dataset)] == ["text"]
    assert [name for name, _args, _queue in scene.started] == [
        "process_huggingface_dataset_activity"
    ]
    assert scene.started[0][1][1:4] == (DATASET, "default", "train")


@pytest.mark.django_db
def test_a_public_dataset_streams_its_rows_without_a_saved_key(hub, scene):
    from model_hub.utils.utils import load_hf_dataset_with_retries

    loaded = load_hf_dataset_with_retries(
        DATASET, "default", "train", str(scene.organization.id)
    )

    assert list(loaded) == ROWS


# --- only settings reads the Hub token settings -----------------------------

_SKIPPED_DIRECTORIES = {"tests", "node_modules", "__pycache__", "site-packages", "venv"}


def _reads_of_the_hub_token_settings(root):
    names = set(HUB_TOKEN_SETTINGS)
    found = []
    for directory, subdirectories, files in os.walk(root):
        relative = Path(directory).relative_to(root)
        if relative.parts[:2] == ("tfc", "settings"):
            subdirectories[:] = []
            continue
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if not name.startswith(".") and name not in _SKIPPED_DIRECTORIES
        )
        for file_name in sorted(files):
            if not file_name.endswith(".py") or file_name.startswith("test_"):
                continue
            if file_name == "conftest.py":
                continue
            path = Path(directory) / file_name
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "HUGGINGFACE_API_TOKEN" not in text:
                continue
            where = (relative / file_name).as_posix()
            try:
                tree = ast.parse(text, filename=where)
            except SyntaxError:
                found.append(f"{where}: names a Hub token setting and does not parse")
                continue
            for node in ast.walk(tree):
                line = getattr(node, "lineno", "?")
                if isinstance(node, ast.Name) and node.id in names:
                    found.append(f"{where}:{line} {node.id}")
                elif isinstance(node, ast.Attribute) and node.attr in names:
                    found.append(f"{where}:{line} .{node.attr}")
                elif isinstance(node, ast.alias) and node.name in names:
                    found.append(f"{where}:{line} import {node.name}")
                elif (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "HUGGINGFACE_API_TOKEN" in node.value
                ):
                    found.append(f"{where}:{line} string {node.value!r}")
    return found


def test_no_module_outside_settings_reads_the_hub_token_settings():
    import model_hub

    root = Path(model_hub.__file__).resolve().parent.parent
    assert (root / "tfc" / "settings").is_dir()

    found = _reads_of_the_hub_token_settings(root)

    assert found == []
