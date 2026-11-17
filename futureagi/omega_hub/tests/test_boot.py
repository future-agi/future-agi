import pytest


@pytest.mark.django_db
def test_app_tables_exist():
    from omega_hub.models import Publisher

    assert Publisher.objects.count() == 0
