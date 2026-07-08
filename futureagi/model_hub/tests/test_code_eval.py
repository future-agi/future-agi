"""
Tests for Phase 8: Code Evals.
"""

import pytest

from model_hub.models.evals_metric import EvalTemplate


@pytest.mark.e2e
@pytest.mark.django_db
class TestCodeEvalCreateAPI:
    url = "/model-hub/eval-templates/create-v2/"

    def test_create_code_eval(self, auth_client):
        response = auth_client.post(
            self.url,
            {
                "name": "my-code-eval",
                "eval_type": "code",
                "code": "def evaluate(output, expected):\n    return output == expected",
                "code_language": "python",
                "output_type": "pass_fail",
            },
            format="json",
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert result["name"] == "my-code-eval"

        template = EvalTemplate.objects.get(id=result["id"])
        assert template.config["eval_type_id"] == "CustomCodeEval"
        assert "code" in template.config
        assert template.config["language"] == "python"

    def test_create_code_eval_without_code_rejected(self, auth_client):
        response = auth_client.post(
            self.url,
            {
                "name": "no-code-eval",
                "eval_type": "code",
                "output_type": "pass_fail",
            },
            format="json",
        )
        assert response.status_code == 400
        assert "code" in str(response.data["result"]).lower()

    def test_create_code_eval_javascript(self, auth_client):
        response = auth_client.post(
            self.url,
            {
                "name": "js-code-eval",
                "eval_type": "code",
                "code": "function evaluate(output, expected) { return output === expected; }",
                "code_language": "javascript",
                "output_type": "pass_fail",
            },
            format="json",
        )
        assert response.status_code == 200
        template = EvalTemplate.objects.get(id=response.data["result"]["id"])
        assert template.config["language"] == "javascript"

    def test_create_llm_eval_without_instructions_rejected(self, auth_client):
        """LLM evals still require instructions."""
        response = auth_client.post(
            self.url,
            {
                "name": "no-instructions",
                "eval_type": "llm",
                "output_type": "pass_fail",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_create_code_eval_derives_required_keys_from_signature(self, auth_client):
        """A user's `evaluate(input, output, ...)` signature drives
        ``required_keys`` on the saved template. Standard row-derived
        names (input/output/expected) land there so the binding UI can
        map them to dataset columns; nothing else does.
        """
        response = auth_client.post(
            self.url,
            {
                "name": "code-derives-keys",
                "eval_type": "code",
                "code": (
                    "def evaluate(input, output, expected, threshold):\n"
                    "    return {'score': 1.0}\n"
                ),
                "code_language": "python",
                "output_type": "pass_fail",
            },
            format="json",
        )

        assert response.status_code == 200
        template = EvalTemplate.objects.get(id=response.data["result"]["id"])
        assert template.config["required_keys"] == ["input", "output", "expected"]

    def test_create_code_eval_ticket_case_populates_function_params_schema(
        self, auth_client
    ):
        """TH-6671 exact repro. ``max_words_length`` is a config constant,
        not a mapping variable, so it must appear in
        ``function_params_schema`` (which drives the FE Parameters text
        input) and stay out of ``required_keys``.
        """
        response = auth_client.post(
            self.url,
            {
                "name": "code-config-params",
                "eval_type": "code",
                "code": (
                    "def evaluate(max_words_length, input):\n"
                    "    text = str(input).strip()\n"
                    "    return {'score': 1.0}\n"
                ),
                "code_language": "python",
                "output_type": "pass_fail",
            },
            format="json",
        )

        assert response.status_code == 200
        template = EvalTemplate.objects.get(id=response.data["result"]["id"])
        assert template.config["required_keys"] == ["input"]
        schema = template.config.get("function_params_schema") or {}
        assert "max_words_length" in schema
        assert schema["max_words_length"] == {
            "type": "string",
            "default": None,
            "nullable": True,
            "required": False,
        }

    def test_update_code_eval_re_derives_when_code_changes(self, auth_client):
        """Editing the code (e.g. adding a new param) after the first save
        must refresh ``required_keys`` and ``function_params_schema`` so
        the binding UI reflects the new signature. Without this the FE
        would keep asking for a mapping for a param the user just removed
        or silently hide a new one.
        """
        create_res = auth_client.post(
            self.url,
            {
                "name": "code-update-derives",
                "eval_type": "code",
                "code": "def evaluate(input): pass\n",
                "code_language": "python",
                "output_type": "pass_fail",
            },
            format="json",
        )
        assert create_res.status_code == 200
        template_id = create_res.data["result"]["id"]

        update_res = auth_client.put(
            f"/model-hub/eval-templates/{template_id}/update/",
            {
                "code": (
                    "def evaluate(input, output, tolerance):\n"
                    "    return {'score': 1.0}\n"
                ),
            },
            format="json",
        )
        assert update_res.status_code == 200

        template = EvalTemplate.objects.get(id=template_id)
        assert template.config["required_keys"] == ["input", "output"]
        schema = template.config.get("function_params_schema") or {}
        assert set(schema.keys()) == {"tolerance"}
