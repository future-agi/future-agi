"""Disconnect must also scrub a row soft-deleted before F5 shipped."""

import pytest
from django.db import router

from ee.falcon_ai.tests import test_disconnect_credentials as regression
from ee.falcon_ai.tests.test_disconnect_credentials import (
    cleared,
    expected_call,
    transport,
)

connected = regression.connected
pytestmark = pytest.mark.django_db


def test_delete_already_deleted_row_scrubs_credentials(
    connected, django_capture_on_commit_callbacks
):
    obj, platform, values = connected
    alias = router.db_for_write(type(obj))
    type(obj)._base_manager.using(alias).filter(pk=obj.pk).update(deleted=True)
    with transport() as posts:
        with django_capture_on_commit_callbacks(using=alias, execute=True):
            obj.delete()
            cleared(obj)
        expected_call(platform, values, posts)
