"""Model hub request handlers resolve referenced ids in the caller's scope.

Two synthetic tenants (``make_tenant``). Each rejection test sends a
handler an id owned by the other tenant, or by another workspace of the
caller's organization, and checks the answer an unknown id gets and that
nothing was written. Controls (names ending ``_control``) send the caller's
own ids. External work (Temporal ``.delay``, LLM and HTTP calls) is mocked.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.api_key import SecretModel
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_annotations import Annotations
from model_hub.models.develop_dataset import (
    Cell,
    Column,
    Dataset,
    KnowledgeBaseFile,
    Row,
)
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import make_tenant, same_answer

BASE = "/model-hub"

pytestmark = [pytest.mark.integration, pytest.mark.api]


def _other_workspace(tenant):
    clear_workspace_context()
    return Workspace.no_workspace_objects.create(
        name=f"{tenant.organization.name} second",
        organization=tenant.organization,
        is_default=False,
        is_active=True,
        created_by=tenant.user,
    )


def _dataset(tenant, name, workspace=None):
    clear_workspace_context()
    return Dataset.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=workspace or tenant.workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


def _column(dataset, name="input", source=SourceChoices.OTHERS.value):
    clear_workspace_context()
    column = Column.no_workspace_objects.create(
        name=name,
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=source,
    )
    dataset.column_order = [*(dataset.column_order or []), str(column.id)]
    dataset.save(update_fields=["column_order"])
    return column


def _row(dataset, order=0):
    clear_workspace_context()
    return Row.no_workspace_objects.create(dataset=dataset, order=order)


def _cell(dataset, row, column, value):
    clear_workspace_context()
    return Cell.no_workspace_objects.create(
        dataset=dataset, row=row, column=column, value=value
    )


def _kb(tenant, workspace=None):
    clear_workspace_context()
    return KnowledgeBaseFile.no_workspace_objects.create(
        name=f"kb-{uuid.uuid4().hex[:6]}",
        organization=tenant.organization,
        workspace=workspace or tenant.workspace,
    )


def _user_template(tenant, name, workspace=None):
    clear_workspace_context()
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=workspace or tenant.workspace,
        owner=OwnerChoices.USER.value,
        criteria="Evaluate {{output}}",
        config={
            "required_keys": [],
            "mapping": {},
            "custom_eval": True,
            "output": "Pass/Fail",
            "eval_type_id": "CustomPromptEvaluator",
        },
    )


def _secret(tenant):
    clear_workspace_context()
    secret = SecretModel(
        name=f"secret-{uuid.uuid4().hex[:6]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
    )
    # KeysAtRestMixin (RF19, integration): a key set in memory is clear and is
    # encrypted on save; SecretModel.encrypt_key no longer exists.
    secret.key = "synthetic-test-secret"
    secret.save()
    return secret


@pytest.fixture
def tenants(db, settings):
    settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
    a = make_tenant("A")
    b = make_tenant("B")
    return a, b


def _post(client, url, payload):
    clear_workspace_context()
    return client.post(url, payload, format="json")


# --- knowledge base ids (model_hub/confirmed/12, 13, 14, 15, 16) -----------


class TestKnowledgeBaseIds:
    def test_add_user_eval_refuses_another_organizations_kb(self, tenants):
        a, b = tenants
        dataset = _dataset(a, "kb eval")
        template = _user_template(a, "kb-check-template")
        url = f"{BASE}/develops/{dataset.id}/add_user_eval/"
        payload = {
            "name": "kb-check",
            "template_id": str(template.id),
            "config": {"mapping": {}},
        }
        foreign = _post(a.client, url, {**payload, "kb_id": str(_kb(b).id)})
        unknown_id = uuid.uuid4()
        unknown = _post(a.client, url, {**payload, "kb_id": str(unknown_id)})
        assert foreign.status_code == status.HTTP_404_NOT_FOUND
        assert unknown.status_code == status.HTTP_404_NOT_FOUND
        assert not UserEvalMetric.no_workspace_objects.filter(dataset=dataset).exists()

    def test_add_user_eval_refuses_another_workspace_kb(self, tenants):
        a, _ = tenants
        dataset = _dataset(a, "kb eval ws")
        template = _user_template(a, "kb-ws-template")
        hidden_kb = _kb(a, workspace=_other_workspace(a))
        response = _post(
            a.client,
            f"{BASE}/develops/{dataset.id}/add_user_eval/",
            {
                "name": "kb-ws-check",
                "template_id": str(template.id),
                "config": {"mapping": {}},
                "kb_id": str(hidden_kb.id),
            },
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not UserEvalMetric.no_workspace_objects.filter(dataset=dataset).exists()

    def test_add_user_eval_own_kb_control(self, tenants):
        a, _ = tenants
        dataset = _dataset(a, "kb eval own")
        template = _user_template(a, "kb-own-template")
        kb = _kb(a)
        response = _post(
            a.client,
            f"{BASE}/develops/{dataset.id}/add_user_eval/",
            {
                "name": "kb-own-check",
                "template_id": str(template.id),
                "config": {"mapping": {}},
                "kb_id": str(kb.id),
            },
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert UserEvalMetric.no_workspace_objects.filter(
            dataset=dataset, kb_id=kb.id
        ).exists()

    def test_edit_and_run_refuses_another_organizations_kb(self, tenants):
        a, b = tenants
        dataset = _dataset(a, "kb edit")
        template = _user_template(a, "kb-edit-template")
        clear_workspace_context()
        metric = UserEvalMetric.no_workspace_objects.create(
            name="kb-edit",
            organization=a.organization,
            workspace=a.workspace,
            dataset=dataset,
            template=template,
            config={"mapping": {}},
            user=a.user,
        )
        response = _post(
            a.client,
            f"{BASE}/develops/{dataset.id}/edit_and_run_user_eval/{metric.id}/",
            {"config": {"mapping": {}}, "kb_id": str(_kb(b).id)},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        metric.refresh_from_db()
        assert metric.kb_id is None

    @patch("model_hub.views.separate_evals.run_eval_func")
    def test_eval_playground_refuses_another_organizations_kb(
        self, run_eval_func, tenants
    ):
        a, b = tenants
        template = _user_template(a, "kb-playground-template")
        # Name a model outside the Turing family: the serializer otherwise
        # defaults to turing_large, whose deployment gate answers before
        # the knowledge base is looked up. The gate itself is unchanged.
        response = _post(
            a.client,
            f"{BASE}/eval-playground/",
            {
                "template_id": str(template.id),
                "model": "gpt-4o-mini",
                "kb_id": str(_kb(b).id),
            },
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        run_eval_func.assert_not_called()

    @patch("model_hub.views.eval_group.apply_eval_group")
    def test_apply_eval_group_refuses_another_organizations_kb(
        self, apply_eval_group, tenants
    ):
        from model_hub.models.eval_groups import EvalGroup

        a, b = tenants
        clear_workspace_context()
        group = EvalGroup.no_workspace_objects.create(
            name="kb group",
            organization=a.organization,
            workspace=a.workspace,
            created_by=a.user,
        )
        response = _post(
            a.client,
            f"{BASE}/eval-groups/apply-eval-group/",
            {
                "eval_group_id": str(group.id),
                "page_id": "DATASET",
                "mapping": {},
                "filters": {"dataset_id": str(uuid.uuid4()), "kb_id": str(_kb(b).id)},
            },
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        apply_eval_group.assert_not_called()


# --- datasets, rows, cells and columns (confirmed/0, 3, 4, 6, 9, 10) -------


class TestCompareAndCellIds:
    def test_preview_run_eval_refuses_foreign_template_and_dataset(self, tenants):
        a, b = tenants
        own = _dataset(a, "preview own")
        foreign_template = _user_template(b, "preview-foreign")
        url = f"{BASE}/datasets/compare/preview-run-eval/"
        info = {"base": {str(own.id): str(_row(own).id)}}
        by_template = _post(
            a.client,
            url,
            {
                "config": {"config": {}, "mapping": {}},
                "template_id": str(foreign_template.id),
                "dataset_ids": [str(own.id)],
                "dataset_info": info,
            },
        )
        assert by_template.status_code == status.HTTP_404_NOT_FOUND
        own_template = _user_template(a, "preview-own")
        by_dataset = _post(
            a.client,
            url,
            {
                "config": {"config": {}, "mapping": {}},
                "template_id": str(own_template.id),
                "dataset_ids": [str(own.id), str(_dataset(b, "preview b").id)],
                "dataset_info": info,
            },
        )
        assert by_dataset.status_code == status.HTTP_404_NOT_FOUND

    def test_compare_stats_refuses_foreign_and_other_workspace_dataset(self, tenants):
        a, b = tenants
        own = _dataset(a, "stats own")
        url = f"{BASE}/datasets/{own.id}/compare-stats/"
        for other in (
            _dataset(b, "stats b"),
            _dataset(a, "stats hidden", _other_workspace(a)),
        ):
            response = _post(
                a.client,
                url,
                {"base_column_name": "input", "dataset_ids": [str(other.id)]},
            )
            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_download_comparison_holds_no_foreign_cell(self, tenants):
        a, b = tenants
        own = _dataset(a, "download own")
        own_column = _column(own, "input")
        own_row = _row(own)
        _cell(own, own_row, own_column, "own-value")
        foreign = _dataset(b, "download b")
        foreign_row = _row(foreign)
        _cell(foreign, foreign_row, _column(foreign, "input"), "foreign-secret-value")
        response = _post(
            a.client,
            f"{BASE}/datasets/{own.id}/compare-datasets/download/",
            {
                "base_column_name": "input",
                "dataset_ids": [str(own.id)],
                "dataset_info": {"k": {str(own.id): str(foreign_row.id)}},
                "common_column_names": ["input"],
            },
        )
        body = (
            b"".join(response.streaming_content)
            if response.streaming
            else response.content
        )
        assert b"foreign-secret-value" not in body

    def test_add_compare_eval_refuses_foreign_dataset_and_template(self, tenants):
        a, b = tenants
        own = _dataset(a, "add-eval own")
        _column(own, "input")
        foreign = _dataset(b, "add-eval b")
        order_before = list(foreign.column_order or [])
        url = f"{BASE}/datasets/{own.id}/compare-datasets/add-eval/"
        template = _user_template(a, "compare-own")
        by_dataset = _post(
            a.client,
            url,
            {
                "name": "cmp-eval",
                "template_id": str(template.id),
                "config": {"mapping": {}},
                "dataset_ids": [str(foreign.id)],
                "run": True,
            },
        )
        assert by_dataset.status_code == status.HTTP_404_NOT_FOUND
        foreign.refresh_from_db()
        assert list(foreign.column_order or []) == order_before
        assert not UserEvalMetric.no_workspace_objects.filter(dataset=foreign).exists()
        by_template = _post(
            a.client,
            url,
            {
                "name": "cmp-eval-2",
                "template_id": str(_user_template(b, "compare-foreign").id),
                "config": {"mapping": {}},
                "save_as_template": True,
            },
        )
        assert by_template.status_code == status.HTTP_404_NOT_FOUND
        assert not EvalTemplate.no_workspace_objects.filter(name="cmp-eval-2").exists()
        assert not UserEvalMetric.no_workspace_objects.filter(dataset=own).exists()

    def test_start_evals_refuses_foreign_dataset(self, tenants):
        a, b = tenants
        own = _dataset(a, "start own")
        response = _post(
            a.client,
            f"{BASE}/datasets/{own.id}/compare-datasets/start-eval/",
            {"user_eval_names": ["x"], "dataset_ids": [str(_dataset(b, "start b").id)]},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_cell_value_refuses_foreign_cell(self, tenants):
        a, b = tenants
        own = _dataset(a, "cell own")
        foreign = _dataset(b, "cell b")
        foreign_column = _column(foreign, "input")
        foreign_row = _row(foreign)
        foreign_cell = _cell(foreign, foreign_row, foreign_column, "keep-me")
        for url_dataset in (own, foreign):
            response = _post(
                a.client,
                f"{BASE}/develops/{url_dataset.id}/update_cell_value/",
                {
                    "row_id": str(foreign_row.id),
                    "column_id": str(foreign_column.id),
                    "new_value": "changed",
                },
            )
            assert response.status_code == status.HTTP_404_NOT_FOUND
        foreign_cell.refresh_from_db()
        assert foreign_cell.value == "keep-me"

    def test_update_cell_value_control(self, tenants):
        a, _ = tenants
        own = _dataset(a, "cell control")
        column = _column(own, "input")
        row = _row(own)
        cell = _cell(own, row, column, "before")
        response = _post(
            a.client,
            f"{BASE}/develops/{own.id}/update_cell_value/",
            {"row_id": str(row.id), "column_id": str(column.id), "new_value": "after"},
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        cell.refresh_from_db()
        assert cell.value == "after"

    def test_column_config_refuses_another_workspace_column(self, tenants):
        a, b = tenants
        hidden = _column(
            _dataset(a, "cfg hidden", _other_workspace(a)),
            "eval",
            source=SourceChoices.EVALUATION.value,
        )
        foreign = _column(
            _dataset(b, "cfg b"), "eval", source=SourceChoices.EVALUATION.value
        )
        for column in (hidden, foreign):
            clear_workspace_context()
            response = a.client.get(f"{BASE}/column-config/{column.id}/")
            assert response.status_code == status.HTTP_404_NOT_FOUND


# --- secrets and rerun sources (confirmed/1, 2, missed/0, missed/1) ---------


class TestSecretsAndRerun:
    @patch("model_hub.views.dynamic_columns.add_api_column_async")
    def test_add_api_column_refuses_foreign_secret(self, task, tenants):
        a, b = tenants
        own = _dataset(a, "api own")
        config = {
            "url": "https://api.example.test/echo",
            "method": "GET",
            "output_type": "string",
            "headers": {
                "Authorization": {"type": "Secret", "value": str(_secret(b).id)}
            },
        }
        response = _post(
            a.client,
            f"{BASE}/datasets/{own.id}/add-api-column/",
            {"column_name": "api", "config": config},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        task.delay.assert_not_called()
        assert not Column.no_workspace_objects.filter(dataset=own, name="api").exists()

    @patch("model_hub.views.dynamic_columns.add_api_column_async")
    def test_add_api_column_own_secret_control(self, task, tenants):
        a, _ = tenants
        own = _dataset(a, "api control")
        config = {
            "url": "https://api.example.test/echo",
            "method": "GET",
            "output_type": "string",
            "headers": {
                "Authorization": {"type": "Secret", "value": str(_secret(a).id)}
            },
        }
        response = _post(
            a.client,
            f"{BASE}/datasets/{own.id}/add-api-column/",
            {"column_name": "api", "config": config},
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        task.delay.assert_called_once()

    def test_api_call_resolves_secrets_only_in_the_dataset_organization(self, tenants):
        from model_hub.views.dynamic_columns import AddApiColumnView

        a, b = tenants
        own = _dataset(a, "api activity")
        row = _row(own)
        cell = Cell(dataset=own, row=row, value=None)
        config = {
            "url": "https://api.example.test/echo",
            "method": "GET",
            "output_type": "string",
            "headers": {"X-Key": {"type": "Secret", "value": str(_secret(b).id)}},
        }
        with patch("model_hub.views.dynamic_columns.requests.request") as http:
            value, info = AddApiColumnView()._make_api_call(cell, config)
        http.assert_not_called()
        assert info == {"response_status": 400}

    def test_duplicate_api_activity_resolves_secrets_in_the_dataset_organization(
        self, tenants
    ):
        from model_hub.views.develop_dataset import AddApiColumnView

        a, b = tenants
        own = _dataset(a, "api activity dup")
        cell = Cell(dataset=own, row=_row(own), value=None)
        config = {
            "url": "https://api.example.test/echo",
            "method": "GET",
            "output_type": "string",
            "params": {"key": {"type": "Secret", "value": str(_secret(b).id)}},
        }
        with patch("model_hub.views.develop_dataset.requests.request") as http:
            AddApiColumnView()._make_api_call(cell, config)
        http.assert_not_called()

    def test_vector_db_query_refuses_foreign_secret(self, tenants):
        from model_hub.views.dynamic_columns import AddVectorDBColumnView

        a, b = tenants
        fake = MagicMock()
        with patch("tfc.utils.lazy_extras.load_extra", return_value=fake):
            with pytest.raises(SecretModel.DoesNotExist):
                AddVectorDBColumnView()._query_pinecone(
                    [0.1],
                    {"api_key": str(_secret(b).id), "index_name": "i"},
                    a.organization.id,
                )
        fake.Pinecone.assert_not_called()

    @patch("model_hub.views.dynamic_columns.add_vector_db_column_async")
    @patch("model_hub.views.dynamic_columns.serving_available", return_value=True)
    def test_add_vector_db_column_refuses_foreign_secret(self, _serving, task, tenants):
        a, b = tenants
        own = _dataset(a, "vdb own")
        column = _column(own, "input")
        response = _post(
            a.client,
            f"{BASE}/datasets/{own.id}/add_vector_db_column/",
            {
                "column_id": str(column.id),
                "sub_type": "pinecone",
                "api_key": str(_secret(b).id),
                "index_name": "i",
                "new_column_name": "vdb",
            },
        )
        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        )
        task.delay.assert_not_called()
        assert not Column.no_workspace_objects.filter(dataset=own, name="vdb").exists()

    @pytest.mark.parametrize(
        "operation_type,config",
        [
            ("classify", {"labels": ["a", "b"]}),
            ("extract_entities", {"instruction": "names"}),
            ("extract_json", {"json_key": "k"}),
        ],
    )
    def test_rerun_refuses_a_source_column_of_another_dataset(
        self, tenants, operation_type, config
    ):
        a, b = tenants
        own = _dataset(a, f"rerun {operation_type}")
        target = _column(own, "out", source=SourceChoices.OTHERS.value)
        foreign_source = _column(_dataset(b, "rerun b"), "input")
        sibling_source = _column(_dataset(a, "rerun sibling"), "input")
        for source in (foreign_source, sibling_source):
            with patch("model_hub.views.dynamic_columns.Cell.objects.filter") as cells:
                response = _post(
                    a.client,
                    f"{BASE}/columns/{target.id}/rerun-operation/",
                    {
                        "operation_type": operation_type,
                        "config": {**config, "column_id": str(source.id)},
                    },
                )
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            cells.return_value.update.assert_not_called()


# --- templates (confirmed/6 save path, missed/2) ----------------------------


class TestTemplateReads:
    def test_user_structure_hides_a_foreign_template(self, tenants):
        a, b = tenants
        own = _dataset(a, "structure own")
        foreign_template = _user_template(b, "structure-foreign")
        foreign_template.description = "foreign-template-description"
        foreign_template.save(update_fields=["description"])
        clear_workspace_context()
        metric = UserEvalMetric.no_workspace_objects.create(
            name="structure",
            organization=a.organization,
            workspace=a.workspace,
            dataset=own,
            template=foreign_template,
            config={"mapping": {}},
            user=a.user,
        )
        clear_workspace_context()
        response = a.client.get(
            f"{BASE}/develops/{own.id}/get_eval_structure/{metric.id}/",
            {"eval_type": "user"},
        )
        assert b"foreign-template-description" not in response.content


# --- annotations (confirmed/11, 18, 19) --------------------------------------


def _annotation(tenant, dataset, columns=()):
    clear_workspace_context()
    annotation = Annotations.no_workspace_objects.create(
        name=f"ann-{uuid.uuid4().hex[:6]}",
        organization=tenant.organization,
        workspace=tenant.workspace,
        dataset=dataset,
    )
    annotation.assigned_users.add(tenant.user)
    if columns:
        annotation.columns.set(columns)
    return annotation


class TestAnnotationIds:
    def test_update_cells_refuses_foreign_row_and_column(self, tenants):
        a, b = tenants
        own = _dataset(a, "ann own")
        own_row = _row(own)
        own_column = _column(own, "resp")
        annotation = _annotation(a, own, [own_column])
        foreign = _dataset(b, "ann b")
        foreign_row = _row(foreign)
        foreign_column = _column(foreign, "resp")
        url = f"{BASE}/annotations/{annotation.id}/update_cells/"
        for row_id, column_id in (
            (foreign_row.id, own_column.id),
            (own_row.id, foreign_column.id),
        ):
            response = _post(
                a.client,
                url,
                {
                    "response_field_values": [
                        {
                            "row_id": str(row_id),
                            "column_id": str(column_id),
                            "value": "x",
                        }
                    ]
                },
            )
            assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not Cell.no_workspace_objects.filter(row=foreign_row).exists()
        assert not Cell.no_workspace_objects.filter(column=foreign_column).exists()

    def test_update_cells_control(self, tenants):
        a, _ = tenants
        own = _dataset(a, "ann control")
        row = _row(own)
        column = _column(own, "resp")
        annotation = _annotation(a, own, [column])
        response = _post(
            a.client,
            f"{BASE}/annotations/{annotation.id}/update_cells/",
            {
                "response_field_values": [
                    {"row_id": str(row.id), "column_id": str(column.id), "value": "ok"}
                ]
            },
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert Cell.no_workspace_objects.get(row=row, column=column).value == "ok"

    def test_annotation_columns_must_belong_to_its_dataset(self, tenants):
        a, b = tenants
        own = _dataset(a, "ann cols")
        own_column = _column(own, "c1")
        annotation = _annotation(a, own, [own_column])
        for column in (
            _column(_dataset(b, "ann cols b"), "c2"),
            _column(_dataset(a, "ann sibling"), "c3"),
        ):
            clear_workspace_context()
            response = a.client.patch(
                f"{BASE}/annotations/{annotation.id}/",
                {"columns": [str(column.id)]},
                format="json",
            )
            assert response.status_code != status.HTTP_200_OK
            assert list(annotation.columns.values_list("id", flat=True)) == [
                own_column.id
            ]

    def test_reset_annotations_foreign_row_answers_like_unknown(self, tenants):
        a, b = tenants
        own = _dataset(a, "reset own")
        annotation = _annotation(a, own)
        annotation.lowest_unfinished_row = 5
        annotation.save(update_fields=["lowest_unfinished_row"])
        foreign_row = _row(_dataset(b, "reset b"), order=0)
        url = f"{BASE}/annotations/{annotation.id}/reset_annotations/"
        unknown_id = uuid.uuid4()
        foreign = _post(a.client, url, {"row_id": str(foreign_row.id)})
        unknown = _post(a.client, url, {"row_id": str(unknown_id)})
        assert same_answer(foreign, foreign_row.id, unknown, unknown_id)
        annotation.refresh_from_db()
        assert annotation.lowest_unfinished_row == 5


# --- synthetic datasets (confirmed/16) and manager-only lookups (missed/3) --


class TestSyntheticAndDatasetLookups:
    @patch("model_hub.views.datasets.create.synthetic.create_synthetic_dataset")
    def test_update_synthetic_config_refuses_foreign_kb(self, task, tenants):
        a, b = tenants
        own = _dataset(a, "synthetic own")
        own.synthetic_dataset_config = {"num_rows": 10}
        own.save(update_fields=["synthetic_dataset_config"])
        response = a.client.put(
            f"{BASE}/develops/{own.id}/update-synthetic-config/",
            {
                "dataset": {"name": "synthetic own", "description": "d"},
                "num_rows": 10,
                "columns": [{"name": "q", "data_type": "text", "description": "q"}],
                "kb_id": str(_kb(b).id),
                "regenerate": True,
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
        )
        task.delay.assert_not_called()

    def test_update_column_type_refuses_a_foreign_dataset(self, tenants):
        a, b = tenants
        foreign = _dataset(b, "coltype b")
        column = _column(foreign, "input")
        clear_workspace_context()
        response = a.client.put(
            f"{BASE}/develops/{foreign.id}/update_column_type/{column.id}/",
            {"new_column_type": DataTypeChoices.INTEGER.value, "preview": False},
            format="json",
        )
        assert response.status_code != status.HTTP_200_OK
        column.refresh_from_db()
        assert column.data_type == DataTypeChoices.TEXT.value
