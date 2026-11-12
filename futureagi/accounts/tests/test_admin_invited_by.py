"""The registered user admin can promote a self-signed-up account without an inviter."""

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import RequestFactory

from accounts.admin import CustomUserAdmin
from accounts.models import User

pytestmark = pytest.mark.django_db


def test_admin_can_save_operator_with_empty_invited_by():
    person = User.objects.create_user(
        email="person@example.com", name="Person", password="test-password"
    )
    administrator = User.objects.create_superuser(
        email="administrator@example.com", name="Administrator"
    )
    permission = Permission.objects.get(
        content_type__app_label="omega_rollout", codename="operate_rollout"
    )
    assert person.invited_by_id is None
    password_before = person.password
    request = RequestFactory().post("/admin/accounts/user/change/")
    request.user = administrator
    registered_admin = admin.site._registry[User]
    assert isinstance(registered_admin, CustomUserAdmin)
    form_class = registered_admin.get_form(request, obj=person, change=True)
    form = form_class(
        instance=person,
        data={
            "email": person.email,
            "name": person.name,
            "organization": "",
            "organization_role": person.organization_role,
            "invited_by": "",
            "is_active": "on",
            "is_staff": "on",
            "is_superuser": "",
            "groups": [],
            "user_permissions": [str(permission.pk)],
            "config": "{}",
        },
    )

    assert form.is_valid(), form.errors.as_json()
    saved = registered_admin.save_form(request, form, change=True)
    registered_admin.save_model(request, saved, form, change=True)
    registered_admin.save_related(request, form, [], change=True)

    person.refresh_from_db()
    assert person.invited_by_id is None
    assert person.is_staff is True
    assert person.is_superuser is False
    assert person.password == password_before
    assert person.user_permissions.filter(pk=permission.pk).exists()
