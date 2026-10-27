"""The `List` feature-type alias for datasets 3.6.0 (model_hub/utils/utils.py).

The slim backend and the standalone image leave `datasets` out, and every
process imports this module, so its absence must not log a traceback.
"""

import sys
import types
from unittest.mock import patch

from model_hub.utils import utils


def _without_datasets(monkeypatch):
    for name in [m for m in sys.modules if m.split(".")[0] == "datasets"]:
        monkeypatch.delitem(sys.modules, name)


class _NotInstalled:
    """A meta path finder for which `datasets` is not installed."""

    @staticmethod
    def find_spec(name, path=None, target=None):
        if name == "datasets":
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


def test_without_datasets_it_skips_quietly(monkeypatch):
    _without_datasets(monkeypatch)
    monkeypatch.setattr(sys, "meta_path", [_NotInstalled, *sys.meta_path])

    with patch.object(utils, "logger") as logger:
        utils._alias_hf_list_feature_type()

    logger.warning.assert_not_called()
    logger.debug.assert_called_once()


def test_a_broken_datasets_install_is_still_reported(monkeypatch):
    # `datasets` is there, but not the module the alias needs.
    _without_datasets(monkeypatch)
    monkeypatch.setitem(sys.modules, "datasets", types.ModuleType("datasets"))

    with patch.object(utils, "logger") as logger:
        utils._alias_hf_list_feature_type()

    logger.warning.assert_called_once()
    assert logger.warning.call_args.kwargs["exc_info"] is True


def test_the_alias_is_installed_when_datasets_has_large_list(monkeypatch):
    features = types.ModuleType("datasets.features.features")
    features._FEATURE_TYPES = {}
    features.LargeList = object()
    package = types.ModuleType("datasets.features")
    package.features = features
    _without_datasets(monkeypatch)
    monkeypatch.setitem(sys.modules, "datasets", types.ModuleType("datasets"))
    monkeypatch.setitem(sys.modules, "datasets.features", package)
    monkeypatch.setitem(sys.modules, "datasets.features.features", features)

    utils._alias_hf_list_feature_type()

    assert features._FEATURE_TYPES["List"] is features.LargeList
