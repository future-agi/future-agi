"""Real PostgreSQL interleavings with legacy writers, without replacing queries."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import close_old_connections, connection

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_r3 import HubSkillIntakeReceipt
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.import_plans import preview_import
from model_hub.services.skills_hub.versions import create_skill

from .test_intake import accept, uploads


@pytest.mark.django_db(transaction=True)
def test_legacy_create_wins_between_intake_name_read_and_insert(
    principal, organization, workspace, hub_actor, make_skill_files
):
    plan = preview_import(principal=principal, uploads=uploads(make_skill_files()))
    name_read = Event()
    legacy_committed = Event()

    def pause_after_real_name_read(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if (
            not name_read.is_set()
            and sql.lstrip().startswith("SELECT")
            and '"model_hub_hubskill"' in sql
            and "LIMIT 1" in sql
            and "help-desk" in (params or ())
        ):
            name_read.set()
            assert legacy_committed.wait(
                10
            ), "Legacy insert must not deadlock on the intake workspace FK lock"
        return result

    def intake():
        close_old_connections()
        try:
            with connection.execute_wrapper(pause_after_real_name_read):
                return accept(principal, plan)
        except SkillsHubError as error:
            return error.code
        finally:
            close_old_connections()

    def legacy():
        close_old_connections()
        try:
            assert name_read.wait(10), "Intake must inspect the live namespace first"
            skill = create_skill(
                organization=organization,
                workspace=workspace,
                actor=hub_actor,
                name="help-desk",
                description="Legacy creation wins without being overwritten.",
                tags=["legacy"],
            )
            legacy_committed.set()
            return skill.pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        import_future = pool.submit(intake)
        legacy_future = pool.submit(legacy)
        assert import_future.result(timeout=20) == "name_taken"
        winner = legacy_future.result(timeout=20)
    assert HubSkill._base_manager.count() == 1
    assert HubSkill._base_manager.get(pk=winner).tags == ["legacy"]
    assert HubSkillIntakeReceipt.objects.count() == 0
