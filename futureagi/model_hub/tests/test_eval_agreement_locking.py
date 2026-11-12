"""Queue submissions hold existing votes until their bulk write completes."""

import pytest
from django.db import OperationalError, connection

from model_hub.models.score import Score
from model_hub.tests.test_eval_agreement import AgreementTenant


@pytest.mark.django_db(transaction=True)
def test_queue_vote_is_locked_before_bulk_update():
    tenant = AgreementTenant("locked-vote")
    base = "/model-hub/annotation-queues/"
    created = tenant.client.post(
        base,
        {
            "name": "Locked vote queue",
            "label_ids": [str(tenant.label.id)],
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    queue = created.data["id"]
    assert (
        tenant.client.post(
            f"{base}{queue}/update-status/", {"status": "active"}, format="json"
        ).status_code
        == 200
    )
    assert (
        tenant.client.post(
            f"{base}{queue}/items/add-items/",
            {
                "items": [
                    {
                        "source_type": "dataset_row",
                        "source_id": str(tenant.rows[0].id),
                    }
                ]
            },
            format="json",
        ).status_code
        == 200
    )
    from model_hub.models.annotation_queues import QueueItem

    item = QueueItem.no_workspace_objects.get(queue_id=queue)
    path = f"{base}{queue}/items/{item.id}/annotations/submit/"
    body = {
        "annotations": [
            {"label_id": str(tenant.label.id), "value": {"selected": ["Pass"]}}
        ]
    }
    first = tenant.client.post(path, body, format="json")
    assert first.status_code == 200, first.content
    score = Score.no_workspace_objects.get(queue_item=item, annotator=tenant.user)
    observed = []

    def observe_read(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if (
            sql.lstrip().startswith('SELECT "model_hub_score".')
            and 'FROM "model_hub_score"' in sql
            and '"queue_item_id"' in sql
        ):
            other = connection.copy(alias="vote_lock_probe")
            try:
                with other.cursor() as cursor:
                    cursor.execute("BEGIN")
                    try:
                        cursor.execute(
                            "SELECT id FROM model_hub_score WHERE id=%s FOR UPDATE NOWAIT",
                            [score.id],
                        )
                        observed.append(False)
                    except OperationalError as error:
                        code = getattr(error.__cause__, "sqlstate", None) or getattr(
                            error.__cause__, "pgcode", None
                        )
                        assert code == "55P03"
                        observed.append(True)
                    finally:
                        cursor.execute("ROLLBACK")
            finally:
                other.close()
        return result

    try:
        with connection.execute_wrapper(observe_read):
            response = tenant.client.post(path, body, format="json")
        assert response.status_code == 200, response.content
        assert observed and all(observed), (
            "The vote was readable for update by another transaction before the bulk write"
        )
        score.refresh_from_db()
        assert score.value == {"selected": ["Pass"]}
    finally:
        tenant.client.stop_workspace_injection()
