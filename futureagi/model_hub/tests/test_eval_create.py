"""
Tests for Phase 3: Single Eval Creation API.

Covers:
- Validation (name format, instructions variables, output type, choice_scores)
- Successful creation with different output types
- Duplicate name rejection
"""

import pytest

from model_hub.models.evals_metric import EvalTemplate

# =============================================================================
# E2E API Tests: EvalTemplateCreateV2View
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestEvalTemplateCreateV2API:
    url = "/model-hub/eval-templates/create-v2/"

    def _valid_payload(self, **overrides):
        """Base valid payload for creating an eval."""
        payload = {
            "name": "my-test-eval",
            "eval_type": "llm",
            "instructions": "Evaluate if {{response}} matches {{expected}}.",
            "model": "turing_large",
            "output_type": "pass_fail",
            "pass_threshold": 0.5,
            "description": "A test eval",
            "tags": ["test"],
            "check_internet": False,
        }
        payload.update(overrides)
        return payload

    # --- Happy path ---

    def test_create_pass_fail_eval(self, auth_client):
        """Create a Pass/Fail eval successfully."""
        response = auth_client.post(self.url, self._valid_payload(), format="json")
        assert response.status_code == 200
        assert response.data["status"] is True
        result = response.data["result"]
        assert "id" in result
        assert result["name"] == "my-test-eval"
        assert result["version"] == "V1"

        # Verify DB record
        template = EvalTemplate.objects.get(id=result["id"])
        assert template.output_type_normalized == "pass_fail"
        assert template.pass_threshold == 0.5
        assert template.choice_scores is None

    def test_create_percentage_eval(self, auth_client):
        """Create a Percentage eval successfully."""
        response = auth_client.post(
            self.url,
            self._valid_payload(output_type="percentage", pass_threshold=0.7),
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]

        template = EvalTemplate.objects.get(id=result["id"])
        assert template.output_type_normalized == "percentage"
        assert template.pass_threshold == 0.7

    def test_create_deterministic_eval(self, auth_client):
        """Create a Deterministic Choices eval with choice_scores.

        Note: DRF camelCase middleware transforms dict keys in request body,
        so choice labels get lowercased with underscore prefix in the DB.
        Use lowercase keys in choice_scores to avoid this transformation.
        """
        response = auth_client.post(
            self.url,
            self._valid_payload(
                name="deterministic-eval",
                output_type="deterministic",
                choice_scores={"yes": 1.0, "no": 0.0, "maybe": 0.5},
                pass_threshold=0.6,
            ),
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]

        template = EvalTemplate.objects.get(id=result["id"])
        assert template.output_type_normalized == "deterministic"
        assert template.choice_scores == {"yes": 1.0, "no": 0.0, "maybe": 0.5}
        assert template.pass_threshold == 0.6

    def test_create_sets_backward_compat_fields(self, auth_client):
        """New eval also sets backward-compatible config fields."""
        response = auth_client.post(self.url, self._valid_payload(), format="json")
        assert response.status_code == 200
        result = response.data["result"]

        template = EvalTemplate.objects.get(id=result["id"])
        assert template.config["output"] == "Pass/Fail"
        assert "response" in template.config["required_keys"]
        assert "expected" in template.config["required_keys"]
        assert template.criteria == "Evaluate if {{response}} matches {{expected}}."
        # Backward compat fields
        assert template.config.get("custom_eval") is True
        assert template.config.get("rule_prompt") == template.criteria

    def test_create_creates_v1_version(self, auth_client):
        """Creating an eval should also create a V1 version."""
        from model_hub.models.evals_metric import EvalTemplateVersion

        response = auth_client.post(
            self.url, self._valid_payload(name="versioned-eval"), format="json"
        )
        assert response.status_code == 200
        template_id = response.data["result"]["id"]

        versions = EvalTemplateVersion.objects.filter(eval_template_id=template_id)
        assert versions.count() == 1
        assert versions.first().version_number == 1
        assert versions.first().is_default is True

    def test_create_deterministic_sets_choices_map(self, auth_client):
        """Deterministic eval sets choices_map for backward compat."""
        response = auth_client.post(
            self.url,
            self._valid_payload(
                name="choices-eval",
                output_type="deterministic",
                choice_scores={"yes": 1.0, "no": 0.0, "maybe": 0.5},
            ),
            format="json",
        )
        assert response.status_code == 200
        template = EvalTemplate.objects.get(id=response.data["result"]["id"])
        assert "choices_map" in template.config
        assert template.config["choices_map"]["yes"] == "pass"
        assert template.config["choices_map"]["no"] == "fail"
        assert template.config["choices_map"]["maybe"] == "neutral"
        assert isinstance(template.choices, list)

    def test_create_with_tags(self, auth_client):
        """Tags are stored in eval_tags."""
        response = auth_client.post(
            self.url,
            self._valid_payload(tags=["safety", "llm"]),
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]

        template = EvalTemplate.objects.get(id=result["id"])
        assert "safety" in template.eval_tags
        assert "llm" in template.eval_tags

    # --- Validation: Name ---

    def test_create_uppercase_name_rejected(self, auth_client):
        """Names with uppercase letters are rejected."""
        response = auth_client.post(
            self.url,
            self._valid_payload(name="MyEval"),
            format="json",
        )
        assert response.status_code == 400

    def test_create_name_with_spaces_rejected(self, auth_client):
        """Names with spaces are rejected."""
        response = auth_client.post(
            self.url,
            self._valid_payload(name="my eval"),
            format="json",
        )
        assert response.status_code == 400

    def test_create_name_starting_with_hyphen_rejected(self, auth_client):
        response = auth_client.post(
            self.url,
            self._valid_payload(name="-my-eval"),
            format="json",
        )
        assert response.status_code == 400

    def test_create_duplicate_name_rejected(self, auth_client):
        """Duplicate names within org are rejected."""
        # Create first
        auth_client.post(
            self.url, self._valid_payload(name="unique-eval"), format="json"
        )
        # Try duplicate
        response = auth_client.post(
            self.url, self._valid_payload(name="unique-eval"), format="json"
        )
        assert response.status_code == 400
        assert "already exists" in str(response.data["result"]).lower()

    # --- Validation: Instructions ---

    def test_create_no_variables_rejected(self, auth_client):
        """Instructions without template variables are rejected."""
        response = auth_client.post(
            self.url,
            self._valid_payload(instructions="No variables here."),
            format="json",
        )
        assert response.status_code == 400
        assert "template variable" in str(response.data["result"]).lower()

    def test_create_empty_instructions_rejected(self, auth_client):
        """Empty instructions are rejected."""
        response = auth_client.post(
            self.url,
            self._valid_payload(instructions=""),
            format="json",
        )
        assert response.status_code == 400

    # --- Validation: Scoring ---

    def test_create_deterministic_without_choices_rejected(self, auth_client):
        """Deterministic output_type requires choice_scores."""
        response = auth_client.post(
            self.url,
            self._valid_payload(
                name="no-choices-eval",
                output_type="deterministic",
                choice_scores=None,
            ),
            format="json",
        )
        assert response.status_code == 400
        assert "choice_scores" in str(response.data["result"]).lower()

    def test_create_choice_scores_out_of_range_rejected(self, auth_client):
        """Choice scores outside 0-1 are rejected."""
        response = auth_client.post(
            self.url,
            self._valid_payload(
                name="bad-choices-eval",
                output_type="deterministic",
                choice_scores={"yes": 1.5, "no": -0.1},
            ),
            format="json",
        )
        assert response.status_code == 400

    def test_create_pass_threshold_out_of_range_rejected(self, auth_client):
        """Pass threshold outside 0-1 is rejected."""
        response = auth_client.post(
            self.url,
            self._valid_payload(pass_threshold=1.5),
            format="json",
        )
        assert response.status_code == 400

    # --- Agent eval creation ---

    def test_create_agent_eval(self, auth_client):
        """Agent evals route to AgentEvaluator and persist agent-specific config."""
        response = auth_client.post(
            self.url,
            self._valid_payload(
                name="agent-eval",
                eval_type="agent",
                mode="quick",
                tools={"internet": True, "connectors": []},
                knowledge_bases=[],
                data_injection={"variables_only": True},
                summary={"type": "short"},
            ),
            format="json",
        )
        assert response.status_code == 200
        template = EvalTemplate.objects.get(id=response.data["result"]["id"])
        assert template.eval_type == "agent"
        assert template.config["eval_type_id"] == "AgentEvaluator"
        assert template.config["agent_mode"] == "quick"
        assert template.config["tools"] == {"internet": True, "connectors": []}
        assert template.config["knowledge_bases"] == []
        assert template.config["summary"] == {"type": "short"}
        assert template.config["rule_prompt"] == template.criteria

    # --- Draft creation (TH-4076) ---

    def test_create_draft_snake_case(self, auth_client):
        """is_draft=true skips name + instructions validation and returns a
        temp-named draft template."""
        response = auth_client.post(
            self.url,
            {
                "is_draft": True,
                "eval_type": "agent",
                "output_type": "pass_fail",
                "model": "turing_large",
                "pass_threshold": 0.5,
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        result = response.data["result"]
        assert result["name"].startswith("draft-")
        template = EvalTemplate.objects.get(id=result["id"])
        assert template.name.startswith("draft-")

    def test_create_draft_camel_case_alias(self, auth_client):
        """isDraft (camelCase) must be accepted as an alias for is_draft.

        The frontend's camelCase compatibility bridge installs enumerable
        camelCase twins on every response object. If a spread of response-
        derived state lands in a create-v2 POST body without the snake_case
        original, only `isDraft` arrives — without this alias, the request
        is treated as a non-draft and fails with "Instructions are
        required." (TH-4076).
        """
        response = auth_client.post(
            self.url,
            {
                "isDraft": True,
                "eval_type": "agent",
                "output_type": "pass_fail",
                "model": "turing_large",
                "pass_threshold": 0.5,
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["status"] is True
        assert response.data["result"]["name"].startswith("draft-")

    # --- Code eval creation ---

    def test_create_code_eval(self, auth_client):
        """Code evals route to CustomCodeEval and store code + language."""
        code = "def evaluate(response, expected):\n    return response == expected\n"
        response = auth_client.post(
            self.url,
            self._valid_payload(
                name="code-eval",
                eval_type="code",
                instructions="",
                code=code,
                code_language="python",
            ),
            format="json",
        )
        assert response.status_code == 200
        template = EvalTemplate.objects.get(id=response.data["result"]["id"])
        assert template.eval_type == "code"
        assert template.config["eval_type_id"] == "CustomCodeEval"
        assert template.config["code"] == code
        assert template.config["language"] == "python"


# =============================================================================
# E2E API Tests: DuplicateEvalTemplateView
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestDuplicateEvalTemplateAPI:
    create_url = "/model-hub/eval-templates/create-v2/"
    url = "/model-hub/duplicate-eval-template/"

    def _create_eval(self, auth_client, name):
        response = auth_client.post(
            self.create_url,
            {
                "name": name,
                "eval_type": "llm",
                "instructions": "Evaluate if {{response}} matches {{expected}}.",
                "model": "turing_large",
                "output_type": "pass_fail",
                "pass_threshold": 0.5,
                "tags": ["test"],
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        return response.data["result"]["id"]

    def test_duplicate_returns_eval_template_id(self, auth_client):
        """Duplicate returns the new template's id under `eval_template_id` —
        the key the eval detail page reads to navigate to the copy."""
        src_id = self._create_eval(auth_client, "dup-source")
        response = auth_client.post(
            self.url,
            {"eval_template_id": src_id, "name": "dup-source-copy-1"},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["status"] is True
        new_id = response.data["result"]["eval_template_id"]
        assert new_id
        assert new_id != src_id
        assert EvalTemplate.objects.get(id=new_id).name == "dup-source-copy-1"

    def test_duplicate_rejects_existing_name(self, auth_client):
        """A name that already exists is rejected, so the copy name must be unique."""
        src_id = self._create_eval(auth_client, "dup-dupe")
        first = auth_client.post(
            self.url,
            {"eval_template_id": src_id, "name": "dup-dupe-copy"},
            format="json",
        )
        assert first.status_code == 200, first.data
        again = auth_client.post(
            self.url,
            {"eval_template_id": src_id, "name": "dup-dupe-copy"},
            format="json",
        )
        assert again.status_code == 400
