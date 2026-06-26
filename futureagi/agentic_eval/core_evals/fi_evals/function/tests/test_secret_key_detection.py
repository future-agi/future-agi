from futureagi.agentic_eval.core_evals.fi_evals.function.functions import (
    secret_key_detection,
)


class TestSecretKeyDetection:
    def test_clean_text_passes(self):
        out = secret_key_detection("The quarterly report is ready for review.")
        assert out["result"] is True

    def test_empty_text_passes(self):
        assert secret_key_detection("")["result"] is True
        assert secret_key_detection("   ")["result"] is True

    def test_aws_access_key_detected(self):
        out = secret_key_detection("creds: AKIAQ7Z3X9P2L5M8N4R1 do not commit")
        assert out["result"] is False
        assert "AWS Access Key ID" in out["reason"]

    def test_github_token_detected(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        out = secret_key_detection(f"export GITHUB_TOKEN={token}")
        assert out["result"] is False
        assert "GitHub Token" in out["reason"]

    def test_anthropic_key_detected(self):
        out = secret_key_detection("key=sk-ant-api03-abc123ABC456def789GHI012jkl")
        assert out["result"] is False
        assert "Anthropic API Key" in out["reason"]

    def test_openai_key_not_misclassified_as_anthropic(self):
        out = secret_key_detection("key=sk-proj-abcdefghijklmnopqrstuvwxyz1234")
        assert out["result"] is False
        assert "OpenAI API Key" in out["reason"]
        assert "Anthropic API Key" not in out["reason"]

    def test_stripe_key_detected(self):
        # Low-entropy fixture: matches our regex but is obviously not a real key.
        out = secret_key_detection("sk_live_aaaaaaaaaaaaaaaa")
        assert out["result"] is False
        assert "Stripe Key" in out["reason"]

    def test_slack_token_detected(self):
        # Low-entropy fixture: matches our regex but is obviously not a real token.
        out = secret_key_detection("xoxb-aaaaaaaaaa-aaaaaaaaaaaaaaaaaaa")
        assert out["result"] is False
        assert "Slack Token" in out["reason"]

    def test_google_api_key_detected(self):
        key = "AIza" + "C" * 35
        out = secret_key_detection(f"maps key {key}")
        assert out["result"] is False
        assert "Google API Key" in out["reason"]

    def test_jwt_detected(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.abc123XYZ_-def456"
        out = secret_key_detection(f"Authorization: Bearer {jwt}")
        assert out["result"] is False
        assert "JWT" in out["reason"]

    def test_private_key_block_detected(self):
        out = secret_key_detection("-----BEGIN RSA PRIVATE KEY-----\nMII...")
        assert out["result"] is False
        assert "Private Key Block" in out["reason"]

    def test_placeholder_aws_key_not_flagged(self):
        # Classic AWS docs example; contains EXAMPLE -> treated as placeholder.
        out = secret_key_detection("AKIAIOSFODNN7EXAMPLE")
        assert out["result"] is True

    def test_detect_types_subset_only_checks_requested(self):
        # AWS key present but only github_token requested -> passes.
        out = secret_key_detection(
            "AKIAQ7Z3X9P2L5M8N4R1", detect_types=["github_token"]
        )
        assert out["result"] is True

    def test_detect_types_accepts_comma_string(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        out = secret_key_detection(token, detect_types="github_token,slack_token")
        assert out["result"] is False

    def test_multiple_secrets_counted(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        out = secret_key_detection(f"{token} and AKIAQ7Z3X9P2L5M8N4R1")
        assert out["result"] is False
        assert "GitHub Token" in out["reason"]
        assert "AWS Access Key ID" in out["reason"]
