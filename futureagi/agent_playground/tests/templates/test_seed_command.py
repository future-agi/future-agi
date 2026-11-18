"""Tests for the seed_node_templates management command and post_migrate signal."""

import io

import pytest
from django.core.management import call_command

from agent_playground.models.node_template import NodeTemplate


@pytest.mark.unit
class TestSeedNodeTemplatesCommand:
    """Tests for seed_node_templates management command."""

    def test_creates_template(self, db):
        """Running the command creates the template record."""
        call_command("seed_node_templates")
        assert NodeTemplate.no_workspace_objects.filter(name="llm_prompt").exists()

    def test_idempotent(self, db):
        """Running the command twice produces exactly one record."""
        call_command("seed_node_templates")
        call_command("seed_node_templates")
        assert NodeTemplate.no_workspace_objects.filter(name="llm_prompt").count() == 1

    def test_updates_safe_fields(self, db):
        """Re-running after a safe field change updates the record."""
        call_command("seed_node_templates")
        template = NodeTemplate.no_workspace_objects.get(name="llm_prompt")
        # Manually change a safe field
        template.display_name = "Old Name"
        template.save()

        # Re-seed should restore the correct value
        call_command("seed_node_templates")
        template.refresh_from_db()
        assert template.display_name == "LLM Prompt"

    def test_protected_fields_not_updated(self, db):
        """Protected structural fields are not updated on re-seed."""
        call_command("seed_node_templates")
        template = NodeTemplate.no_workspace_objects.get(name="llm_prompt")

        # Manually change a protected field
        original_config_schema = template.config_schema
        modified_schema = {"type": "object", "properties": {"modified": True}}
        template.config_schema = modified_schema
        template.save()

        # Re-seed should NOT overwrite the protected field
        call_command("seed_node_templates")
        template.refresh_from_db()
        assert template.config_schema == modified_schema
        assert template.config_schema != original_config_schema

    def test_warns_on_protected_field_mismatch(self, db):
        """Command warns when protected fields differ from definition."""
        call_command("seed_node_templates")
        template = NodeTemplate.no_workspace_objects.get(name="llm_prompt")

        # Manually change a protected field
        template.config_schema = {"type": "object", "properties": {"modified": True}}
        template.save()

        # Re-seed should warn about the mismatch
        stderr = io.StringIO()
        call_command("seed_node_templates", stderr=stderr)
        warning_output = stderr.getvalue()
        assert "Cannot update protected field 'config_schema'" in warning_output

    def test_dry_run_creates_nothing(self, db):
        """--dry-run prints info but creates no records."""
        call_command("seed_node_templates", dry_run=True)
        assert not NodeTemplate.no_workspace_objects.filter(name="llm_prompt").exists()

    def test_created_template_passes_clean(self, db):
        """The seeded template passes model validation."""
        call_command("seed_node_templates")
        template = NodeTemplate.no_workspace_objects.get(name="llm_prompt")
        template.clean()  # should not raise

    def test_template_filter(self, db):
        """--template flag seeds only the specified template."""
        call_command("seed_node_templates", template="llm_prompt")
        assert NodeTemplate.no_workspace_objects.filter(name="llm_prompt").exists()

    def test_template_filter_unknown(self, db):
        """--template with unknown name prints error and creates nothing."""
        call_command("seed_node_templates", template="nonexistent")
        assert NodeTemplate.no_workspace_objects.count() == 0


@pytest.mark.unit
class TestPostMigrateSignalSeed:
    """Tests for the post_migrate auto-seed in AgentPlaygroundConfig."""

    def test_post_migrate_creates_missing_templates(self, db):
        from agent_playground.apps import _seed_after_migrate

        _seed_after_migrate(sender=None)
        assert NodeTemplate.no_workspace_objects.filter(name="llm_prompt").exists()

    def test_post_migrate_is_idempotent(self, db):
        from agent_playground.apps import _seed_after_migrate

        _seed_after_migrate(sender=None)
        _seed_after_migrate(sender=None)
        assert NodeTemplate.no_workspace_objects.filter(name="llm_prompt").count() == 1

    def test_post_migrate_updates_safe_fields(self, db):
        from agent_playground.apps import _seed_after_migrate

        _seed_after_migrate(sender=None)
        template = NodeTemplate.no_workspace_objects.get(name="llm_prompt")
        template.display_name = "Stale Name"
        template.save()

        _seed_after_migrate(sender=None)
        template.refresh_from_db()
        assert template.display_name == "LLM Prompt"
