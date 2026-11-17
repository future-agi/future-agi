"""The wallet suite's module stand-ins restore only their own sys.modules names.

``_install_checkout`` stands in for cloud billing's checkout module, which is
absent from this tree. A stand-in that restores the whole of sys.modules on
exit (``patch.dict(sys.modules)``) also evicts every module first imported
inside it. When the block holds a worker's first request, that includes the
URLconf and every view module it imports. Django keeps serving the evicted
view classes, while a later string-target patch (the webhook test-send and
API-key bulk suites patch ``agentcc.views.<module>.<name>``) replaces the name
in a fresh copy of the module that the served view never reads. The view then
misses the mock: a real outbound call, or a 200 where a 500 was arranged.
"""

import importlib
import sys
import uuid
from unittest.mock import MagicMock

import pytest
from django.urls import resolve

from agentcc.tests import test_managed_credits_api as wallet

admin_client = wallet.admin_client
make_client = wallet.make_client

STAND_IN_NAMES = ("ee.cloud", "ee.cloud.billing", wallet.CHECKOUT_MODULE)
# The wallet routes, and the routes whose suites patch view modules by name.
SERVED_PATHS = (
    wallet.BASE,
    f"{wallet.BASE}checkout/",
    "/agentcc/webhooks/",
    "/agentcc/api-keys/bulk/",
    "/agentcc/org-configs/bulk/",
)
_ABSENT = object()


def test_a_module_first_imported_inside_the_checkout_stand_in_stays_imported(
    tmp_path, monkeypatch
):
    name = f"wallet_stand_in_probe_{uuid.uuid4().hex}"
    (tmp_path / f"{name}.py").write_text("LOADED = True\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with wallet._install_checkout(MagicMock()):
            imported = importlib.import_module(name)
        assert imported.LOADED is True
        assert sys.modules.get(name) is imported, (
            "the checkout stand-in evicted a module first imported inside it"
        )
    finally:
        sys.modules.pop(name, None)


def test_the_checkout_stand_in_restores_exactly_its_own_names():
    before = {name: sys.modules.get(name, _ABSENT) for name in STAND_IN_NAMES}
    create_session = MagicMock()
    with wallet._install_checkout(create_session):
        checkout = sys.modules[wallet.CHECKOUT_MODULE]
        assert checkout.create_session is create_session
        assert "ee.cloud" in sys.modules
        assert "ee.cloud.billing" in sys.modules
    after = {name: sys.modules.get(name, _ABSENT) for name in STAND_IN_NAMES}
    for name in STAND_IN_NAMES:
        assert after[name] is before[name], f"{name} was not restored"


@pytest.mark.integration
@pytest.mark.django_db
def test_a_request_inside_the_checkout_stand_in_leaves_served_views_in_place(
    admin_client,
):
    # Red only when this is the process's first request (run alone in a fresh
    # process): then the URLconf and every view module load inside the block.
    checkout = MagicMock()
    with wallet._install_checkout(checkout):
        response = admin_client.post(
            f"{wallet.BASE}checkout/",
            {**wallet.CHECKOUT_BODY, "amount_usd": "invalid"},
            format="json",
        )
    assert response.status_code == 400
    assert checkout.call_count == 0
    for path in SERVED_PATHS:
        view = resolve(path).func.cls
        served = sys.modules.get(view.__module__)
        assert served is not None, f"{view.__module__} was evicted from sys.modules"
        assert getattr(served, view.__name__) is view, (
            f"{view.__module__} in sys.modules is not the module Django serves"
        )
