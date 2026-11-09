import importlib.machinery
import sys
import types
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest

from model_hub.services.huggingface_revision import import_revision, revision_info
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401

CONFIG_URL = "/model-hub/develops/get-huggingface-dataset-config/"


def test_metadata_is_loaded_at_the_resolved_commit():
    info = SimpleNamespace(splits={"train": SimpleNamespace(num_examples=3)})
    with (
        patch(
            "model_hub.services.huggingface_revision.huggingface_token", return_value=""
        ),
        patch("model_hub.services.huggingface_revision.HfApi") as hub,
        patch("model_hub.services.huggingface_revision.load_extra") as extra,
    ):
        hub.return_value.dataset_info.return_value.sha = "immutable-commit"
        extra.return_value.get_dataset_infos.return_value = {"default": info}
        result = revision_info("fixture/revisions", "organization", "v2")
        hub.return_value.dataset_info.assert_called_once_with(
            "fixture/revisions", revision="v2", token="", timeout=30
        )
        extra.return_value.get_dataset_infos.assert_called_once_with(
            "fixture/revisions", revision="immutable-commit", token=""
        )
        assert result == {
            "revision": "immutable-commit",
            "splits": {"default": ["train"]},
            "num_rows": {"default": {"train": 3}},
        }


@pytest.mark.parametrize("requested,expected", [(None, 3), (0, 3), (1, 1), (50, 3)])
def test_count_comes_from_the_selected_subset_and_revision(requested, expected):
    with patch(
        "model_hub.services.huggingface_revision.revision_info",
        return_value={
            "revision": "commit",
            "num_rows": {"default": {"train": 3}, "other": {"train": 99}},
        },
    ):
        assert import_revision(
            "fixture/revisions", "default", "train", "org", "v2", requested
        ) == ("commit", "default", expected)
        with pytest.raises(ValueError, match="subset or split"):
            import_revision(
                "fixture/revisions", "missing", "train", "org", "v2", requested
            )


def test_unknown_split_size_requires_an_explicit_count():
    with patch(
        "model_hub.services.huggingface_revision.revision_info",
        return_value={"revision": "commit", "num_rows": {"default": {"train": None}}},
    ):
        assert import_revision(
            "fixture/revisions", "default", "train", "org", "v2", 2
        ) == ("commit", "default", 2)
        with pytest.raises(ValueError, match="Specify a row count"):
            import_revision("fixture/revisions", "default", "train", "org", "v2", None)


def test_preview_uses_the_same_loader_revision_as_streaming():
    from model_hub.utils.utils import load_hf_dataset_with_retries

    with (
        patch("model_hub.utils.utils.ApiKey") as keys,
        patch("tfc.utils.lazy_extras.load_extra") as extra,
    ):
        keys.objects.filter.return_value.first.return_value = None
        dataset = extra.return_value.load_dataset.return_value
        dataset.features.to_dict.return_value = {
            "text": {"dtype": "string", "_type": "Value"}
        }
        preview = load_hf_dataset_with_retries(
            "fixture/revisions",
            "default",
            "train",
            "org",
            streaming=False,
            revision="commit",
        )
        assert preview == {
            "features": [
                {"name": "text", "type": {"dtype": "string", "_type": "Value"}}
            ]
        }
        assert extra.return_value.load_dataset.call_args.kwargs["revision"] == "commit"
        assert extra.return_value.load_dataset.call_args.kwargs["streaming"] is True
        assert (
            load_hf_dataset_with_retries(
                "fixture/revisions", "default", "train", "org", revision="commit"
            )
            is dataset
        )


@pytest.mark.django_db
@pytest.mark.parametrize("revision_kwargs", [{}, {"revision": None}, {"revision": ""}])
def test_activity_preserves_new_revision_and_accepts_old_payload(revision_kwargs):
    from tfc.temporal.background_tasks.activities import (
        process_huggingface_dataset_activity,
    )

    args = ("dataset", "fixture/revisions", "default", "train", "org", 1, [], {})
    with patch(
        "model_hub.views.utils.hugginface.process_huggingface_dataset"
    ) as process:
        process_huggingface_dataset_activity(*args, revision="commit")
        process.assert_called_once_with(*args, revision="commit")
        process.reset_mock()
        process_huggingface_dataset_activity(*args, **revision_kwargs)
        process.assert_called_once_with(*args)


def test_token_reads_the_org_key_through_the_key_at_rest_accessor():
    from model_hub.models.api_key import ApiKey
    from model_hub.services.huggingface_revision import huggingface_token
    from tfc.settings.settings import HUGGINGFACE_API_TOKEN

    with patch("model_hub.services.huggingface_revision.ApiKey") as keys:
        first = keys.objects.filter.return_value.first
        first.return_value = ApiKey(provider="huggingface", key="org-key")
        assert huggingface_token("org") == "org-key"
        first.return_value = ApiKey(provider="huggingface", key=None)
        assert huggingface_token("org") == HUGGINGFACE_API_TOKEN
        first.return_value = None
        assert huggingface_token("org") == HUGGINGFACE_API_TOKEN


def test_an_unreadable_org_key_is_never_swapped_for_the_platform_token():
    from model_hub.models.api_key import ApiKey
    from model_hub.services.huggingface_revision import huggingface_token
    from model_hub.services.key_at_rest import KeyUnreadable

    stored = ApiKey(provider="huggingface", key="enc::stored")
    with (
        patch("model_hub.services.huggingface_revision.ApiKey") as keys,
        patch.object(
            ApiKey,
            "actual_key",
            new_callable=PropertyMock,
            side_effect=KeyUnreadable("huggingface key"),
        ),
    ):
        keys.objects.filter.return_value.first.return_value = stored
        with pytest.raises(KeyUnreadable):
            huggingface_token("org")


def _local_datasets_server(monkeypatch):
    """Answer the base's HTTP-only metadata call (datasets-server /info) locally."""
    urls = []

    def get(url, headers=None, timeout=None):
        urls.append(url)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "dataset_info": {"default": {"splits": {"train": {}, "test": {}}}}
            },
        )

    monkeypatch.setattr(
        "model_hub.views.utils.hugginface.requests", SimpleNamespace(get=get)
    )
    return urls


@pytest.mark.django_db
@pytest.mark.usefixtures("no_external_lookups")
def test_a_base_only_install_answers_a_config_request_without_revision(
    auth_client, monkeypatch
):
    # A base-only install (no `ml` extra) has no `datasets` package at all.
    monkeypatch.setitem(sys.modules, "datasets", None)
    urls = _local_datasets_server(monkeypatch)
    with patch("model_hub.services.huggingface_revision.HfApi") as hub:
        hub.return_value.dataset_info.return_value.sha = "immutable-commit"
        response = auth_client.post(
            CONFIG_URL, {"dataset_path": "fixture/legacy"}, format="json"
        )

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["dataset_info"] == {
        "splits": {"default": ["train", "test"]}
    }
    assert urls == [
        "https://datasets-server.huggingface.co/info?dataset=fixture/legacy"
    ]
    hub.assert_not_called()


@pytest.mark.django_db
@pytest.mark.usefixtures("no_external_lookups")
def test_an_ml_install_pins_a_config_request_without_revision_to_main(
    auth_client, monkeypatch
):
    info = SimpleNamespace(splits={"train": SimpleNamespace(num_examples=3)})
    loaded = []

    def get_dataset_infos(path, revision, token):
        loaded.append((path, revision))
        return {"default": info}

    # An ml install: `datasets` is importable, with a local metadata answer.
    package = types.ModuleType("datasets")
    package.__spec__ = importlib.machinery.ModuleSpec("datasets", None)
    package.get_dataset_infos = get_dataset_infos
    monkeypatch.setitem(sys.modules, "datasets", package)
    urls = _local_datasets_server(monkeypatch)
    with patch("model_hub.services.huggingface_revision.HfApi") as hub:
        hub.return_value.dataset_info.return_value.sha = "main-commit"
        response = auth_client.post(
            CONFIG_URL, {"dataset_path": "fixture/legacy"}, format="json"
        )

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["dataset_info"] == {
        "revision": "main-commit",
        "splits": {"default": ["train"]},
        "num_rows": {"default": {"train": 3}},
    }
    assert hub.return_value.dataset_info.call_args.args == ("fixture/legacy",)
    assert hub.return_value.dataset_info.call_args.kwargs["revision"] == "main"
    assert loaded == [("fixture/legacy", "main-commit")]
    assert urls == []
