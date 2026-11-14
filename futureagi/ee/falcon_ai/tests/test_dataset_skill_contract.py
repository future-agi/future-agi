"""Keep the dataset skill's executable examples aligned with generated tools."""

from jsonschema import Draft7Validator

from ee.falcon_ai.builtin_skills_loader import load_builtin_skills
from mcp_server.generated_registry import registry


def test_dataset_skill_examples_match_tool_contracts():
    skill = next(s for s in load_builtin_skills() if s["slug"] == "build-dataset")
    for trajectory in skill["example_trajectories"]:
        for step in trajectory["steps"]:
            tool = registry.get(step["tool"])
            assert tool is not None, step["tool"]
            Draft7Validator(tool.input_schema).validate(step["params"])
    assert all(registry.get(name) is not None for name in skill["tool_names"])
