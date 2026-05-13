"""Tests for the Evaluation node template definition."""

import jsonschema
import pytest
from django.core.exceptions import ValidationError

from agent_playground.models import Node, NodeTemplate
from agent_playground.models.choices import NodeType, PortMode
from agent_playground.templates import get_all_templates
from agent_playground.templates.evaluation import EVALUATION_TEMPLATE


@pytest.mark.unit
class TestEvaluationDefinitionStructure:
    def test_registered_in_registry(self):
        templates = get_all_templates()
        assert "evaluation" in templates
        assert templates["evaluation"] is EVALUATION_TEMPLATE

    def test_ports_are_strict_and_routeable(self):
        assert EVALUATION_TEMPLATE["input_mode"] == "strict"
        assert EVALUATION_TEMPLATE["output_mode"] == "strict"
        assert [port["key"] for port in EVALUATION_TEMPLATE["input_definition"]] == [
            "input",
            "reference",
            "context",
        ]
        assert [port["key"] for port in EVALUATION_TEMPLATE["output_definition"]] == [
            "evaluation_result",
            "passthrough",
            "fallback",
        ]

    def test_config_schema_enforces_evaluators_and_fail_action(self):
        schema = EVALUATION_TEMPLATE["config_schema"]
        jsonschema.validate(
            instance={"evaluators": [{"templateId": "tpl"}], "threshold": 0.7},
            schema=schema,
        )
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance={"threshold": 0.7}, schema=schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                instance={"evaluators": [{}], "fail_action": "retry"},
                schema=schema,
            )


@pytest.mark.unit
class TestEvaluationTemplateDBIntegration:
    def test_db_creation_and_clean(self, db):
        template = NodeTemplate.no_workspace_objects.create(**EVALUATION_TEMPLATE)
        template.clean()
        assert template.name == "evaluation"
        assert template.input_mode == PortMode.STRICT

    def test_node_config_validates(self, db, graph_version):
        template = NodeTemplate.no_workspace_objects.create(**EVALUATION_TEMPLATE)
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="evaluation_gate",
            config={
                "evaluators": [{"templateId": "tpl"}],
                "threshold": 0.7,
                "fail_action": "continue",
            },
            position={"x": 0, "y": 0},
        )
        node.clean()

    def test_node_requires_evaluator_config(self, db, graph_version):
        template = NodeTemplate.no_workspace_objects.create(**EVALUATION_TEMPLATE)
        node = Node(
            graph_version=graph_version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="evaluation_gate",
            config={"threshold": 0.7},
            position={"x": 0, "y": 0},
        )
        with pytest.raises(ValidationError):
            node.clean()
