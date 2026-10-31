"""Verify dataset row validation at the authenticated MCP protocol boundary."""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async

from mcp_server.tests.test_streamable_transport import (
    protocol_client as protocol_client,
)
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.asyncio
async def test_mcp_rejects_invalid_rows_and_preserves_75_valid_rows(
    protocol_client, user, workspace
):
    @sync_to_async
    def setup_dataset():
        dataset = Dataset.objects.create(
            name="MCP row validation",
            user=user,
            organization=user.organization,
            workspace=workspace,
        )
        Column.objects.create(
            dataset=dataset,
            name="trace_id",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        return str(dataset.id)

    @sync_to_async
    def contents(dataset_id):
        return (
            Row.objects.filter(dataset_id=dataset_id).count(),
            list(
                Cell.objects.filter(dataset_id=dataset_id)
                .order_by("row__order")
                .values_list("value", flat=True)
            ),
        )

    dataset_id = await setup_dataset()
    valid = {"cells": [{"column_name": "trace_id", "value": "first"}]}
    invalid_rows = [
        {"trace_id": "legacy flat row"},
        {"cells": [{"column_name": "trace_id", "values": "typo"}]},
        {"cells": [{"column_name": "missing", "value": "lost"}]},
        {
            "cells": [
                {"column_name": "trace_id", "value": "first"},
                {"column_name": "trace_id", "value": "second"},
            ]
        },
    ]
    with patch(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request", None
    ):
        async with protocol_client() as client:
            for invalid in invalid_rows:
                rejected = await client.call_tool(
                    "add_dataset_rows",
                    {
                        "dataset_id": dataset_id,
                        "rows": [valid, invalid],
                    },
                )
                assert rejected.isError, rejected
                assert await contents(dataset_id) == (0, [])

            expected = [f"trace-{i}" for i in range(75)]
            accepted = await client.call_tool(
                "add_dataset_rows",
                {
                    "dataset_id": dataset_id,
                    "rows": [
                        {"cells": [{"column_name": "trace_id", "value": value}]}
                        for value in expected
                    ],
                },
            )
            assert not accepted.isError, accepted
            assert await contents(dataset_id) == (75, expected)

            rejected = await client.call_tool(
                "add_dataset_rows",
                {
                    "dataset_id": dataset_id,
                    "rows": [valid, invalid_rows[2]],
                },
            )
            assert rejected.isError, rejected
            assert await contents(dataset_id) == (75, expected)
