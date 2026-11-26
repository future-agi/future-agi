import structlog
from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = structlog.get_logger(__name__)


def _seed_after_migrate(sender, **kwargs):
    """Auto-seed NodeTemplate rows after every migrate.

    Mirrors the CH apply_schema pattern: new template definitions added to
    the Python registry are detected and created automatically on the next
    deploy. Existing templates get safe metadata refreshed. Idempotent.
    """
    try:
        from agent_playground.management.commands.seed_node_templates import (
            seed_node_templates,
        )
        from agent_playground.models.node_template import NodeTemplate

        result = seed_node_templates(NodeTemplate)
        if result["created"]:
            logger.info(
                "node_templates_seeded",
                created=result["created"],
                updated=result["updated"],
            )
    except Exception:
        logger.exception("node_template_seed_failed")


class AgentPlaygroundConfig(AppConfig):
    name = "agent_playground"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        post_migrate.connect(
            _seed_after_migrate,
            sender=self,
            dispatch_uid="agent_playground_seed_node_templates",
        )
