"""
Test for TH-6410: Verify variable_names from WS message persist to execution.

Bug: prompt run ignores variable_names sent over WebSocket. The WS consumer's
execute_template_async method never extracted variable_names from the content
and never persisted them to the execution before calling run_template_async.

The REST path (prompt_template.py:1503-1504) does this correctly. This test
validates the WS path mirrors that behavior.
"""

import pytest
from unittest.mock import MagicMock, patch

from channels.db import database_sync_to_async


@pytest.mark.django_db(transaction=True)
class TestWSVariableNamesPersistence:
    """Tests that variable_names sent over WS are persisted to execution."""

    async def _create_minimal_data(self):
        """Create minimal test data without requiring user auth setup."""
        from accounts.models import Organization
        from model_hub.models.run_prompt import PromptTemplate, PromptVersion

        org = await database_sync_to_async(Organization.objects.create)(
            name="test-org-th6410"
        )
        template = await database_sync_to_async(PromptTemplate.objects.create)(
            name="test-template-th6410",
            organization=org,
        )
        execution = await database_sync_to_async(PromptVersion.objects.create)(
            original_template=template,
            template_version="1.0",
            variable_names={},
            prompt_config_snapshot={"configuration": {"output_format": "string"}},
        )
        return org, template, execution

    @patch("sockets.prompt_stream_consumer.run_template_async")
    @patch("sockets.prompt_stream_consumer.PromptStreamConsumer.get_workspace_id")
    @patch("sockets.prompt_stream_consumer.PromptStreamConsumer.validate_template_access")
    async def test_variable_names_persisted_from_ws_content(
        self, mock_validate, mock_get_ws_id, mock_run_template
    ):
        """
        When execute_template_async receives content with variable_names,
        it should save them to execution.variable_names before calling
        run_template_async.
        """
        from model_hub.models.run_prompt import PromptVersion

        mock_validate.return_value = True
        mock_get_ws_id.return_value = None

        org, template, execution = await self._create_minimal_data()

        from sockets.prompt_stream_consumer import PromptStreamConsumer

        consumer = PromptStreamConsumer.__new__(PromptStreamConsumer)
        consumer.organization_id = str(org.id)
        consumer.session_uuid = "test-session-uuid"
        consumer.channel_name = "test-channel"
        consumer.channel_layer = None
        consumer.send_json = MagicMock()

        ws_variable_names = {
            "name": ["Alice", "Bob"],
            "product": ["Phone"],
        }

        content = {
            "template_id": str(template.id),
            "version": "1.0",
            "variable_names": ws_variable_names,
            "is_run": "prompt",
            "run_index": None,
        }

        await consumer.execute_template_async(content, str(template.id))

        # Verify execution.variable_names was updated in DB
        execution_refreshed = await database_sync_to_async(PromptVersion.objects.get)(
            id=execution.id
        )
        assert execution_refreshed.variable_names == ws_variable_names, (
            f"Expected variable_names={ws_variable_names}, "
            f"got {execution_refreshed.variable_names}"
        )

    @patch("sockets.prompt_stream_consumer.run_template_async")
    @patch("sockets.prompt_stream_consumer.PromptStreamConsumer.get_workspace_id")
    @patch("sockets.prompt_stream_consumer.PromptStreamConsumer.validate_template_access")
    async def test_no_variable_names_does_not_overwrite(
        self, mock_validate, mock_get_ws_id, mock_run_template
    ):
        """
        When content has no variable_names key, execution.variable_names
        should not be overwritten (keeps its existing DB value).
        """
        from model_hub.models.run_prompt import PromptVersion

        mock_validate.return_value = True
        mock_get_ws_id.return_value = None

        org, template, execution = await self._create_minimal_data()

        existing_vars = {"existing_key": ["existing_val"]}
        execution.variable_names = existing_vars
        await database_sync_to_async(execution.save)()

        from sockets.prompt_stream_consumer import PromptStreamConsumer

        consumer = PromptStreamConsumer.__new__(PromptStreamConsumer)
        consumer.organization_id = str(org.id)
        consumer.session_uuid = "test-session-uuid"
        consumer.channel_name = "test-channel"
        consumer.channel_layer = None
        consumer.send_json = MagicMock()

        content = {
            "template_id": str(template.id),
            "version": "1.0",
            "is_run": "prompt",
            "run_index": None,
        }

        await consumer.execute_template_async(content, str(template.id))

        execution_refreshed = await database_sync_to_async(PromptVersion.objects.get)(
            id=execution.id
        )
        assert execution_refreshed.variable_names == existing_vars

    @patch("sockets.prompt_stream_consumer.run_template_async")
    @patch("sockets.prompt_stream_consumer.PromptStreamConsumer.get_workspace_id")
    @patch("sockets.prompt_stream_consumer.PromptStreamConsumer.validate_template_access")
    async def test_empty_variable_names_does_not_overwrite(
        self, mock_validate, mock_get_ws_id, mock_run_template
    ):
        """
        When content has empty variable_names dict, it should not overwrite
        the existing DB value.
        """
        from model_hub.models.run_prompt import PromptVersion

        mock_validate.return_value = True
        mock_get_ws_id.return_value = None

        org, template, execution = await self._create_minimal_data()

        existing_vars = {"name": ["Alice"]}
        execution.variable_names = existing_vars
        await database_sync_to_async(execution.save)()

        from sockets.prompt_stream_consumer import PromptStreamConsumer

        consumer = PromptStreamConsumer.__new__(PromptStreamConsumer)
        consumer.organization_id = str(org.id)
        consumer.session_uuid = "test-session-uuid"
        consumer.channel_name = "test-channel"
        consumer.channel_layer = None
        consumer.send_json = MagicMock()

        content = {
            "template_id": str(template.id),
            "version": "1.0",
            "variable_names": {},
            "is_run": "prompt",
            "run_index": None,
        }

        await consumer.execute_template_async(content, str(template.id))

        execution_refreshed = await database_sync_to_async(PromptVersion.objects.get)(
            id=execution.id
        )
        assert execution_refreshed.variable_names == existing_vars
