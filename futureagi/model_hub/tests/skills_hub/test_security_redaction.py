"""Diagnostic detection must run after YAML decoding without rewriting content."""

import copy
import json

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.skills_hub import HubSkillVersion
from model_hub.services.skills_hub import checks
from model_hub.views import skills_hub as views

# Synthetic entropy-bearing values, never credentials for an external service.
VALUE = "AbCd" + "0123EfGh4567IjKl89Mn"
TAIL = VALUE[1:]
DESCRIPTION = "Use when checking credential-safe diagnostics for portable skill files."


def files(header):
    return [
        {
            "path": "SKILL.md",
            "content": (
                f"---\nname: escaped-secret\ndescription: {DESCRIPTION}\n{header}\n---\nFollow the policy.\n"
            ),
        }
    ]


@pytest.mark.parametrize("encoded", [r"\x41", r"A", r"\U00000041"])
@pytest.mark.parametrize(
    "key",
    ["api_key", "api-key", "APIKEY", "secret", "password", "token", r'"api\x5fkey"'],
)
def test_decoded_credentials_and_credential_keys_are_detected_and_redacted(
    encoded, key
):
    source = files(f'metadata: {{{key}: "{encoded}{TAIL}"}}')
    original = copy.deepcopy(source)
    result = checks.run_checks(source)
    assert result["summary"]["errors"] == 0
    findings = [item for item in result["items"] if item["code"] == "secret_suspected"]
    assert len(findings) == 1 and findings[0]["severity"] == "warning"
    assert findings[0]["path"] == "SKILL.md"
    assert findings[0]["detail"]["redacted"] == f"[redacted, {len(VALUE)} chars]"
    assert VALUE not in json.dumps(result)
    assert TAIL not in json.dumps(result)
    assert set(result["frontmatter"]["metadata"].values()) == {
        f"[redacted, {len(VALUE)} chars]"
    }
    assert source == original
    parsed, _, _ = checks.parse_frontmatter(source[0]["content"])
    assert parsed is not None
    assert VALUE in parsed["metadata"].values()


@pytest.mark.parametrize(
    "header",
    [
        f"metadata:\n  api_key: >-\n    {VALUE}",
        f'metadata: {{reference: &ref "\\x41{TAIL}", api_key: *ref, copy: *ref}}',
        f'custom: [{{nested: {{token: "\\x41{TAIL}"}}}}]',
        f'custom: {{token: ["\\x41{TAIL}"]}}',
    ],
)
def test_block_scalars_aliases_and_nested_values_do_not_bypass_diagnostics(header):
    result = checks.run_checks(files(header))
    assert any(item["code"] == "secret_suspected" for item in result["items"])
    assert VALUE not in json.dumps(result)
    assert TAIL not in json.dumps(result)


def test_decoded_provider_tokens_emit_warnings_not_only_redaction():
    provider = "ghp_" + "syntheticexample0123456789"
    source = files('metadata: {reference: "\\x67' + provider[1:] + '"}')
    result = checks.run_checks(source)
    assert any(item["code"] == "secret_suspected" for item in result["items"])
    assert provider not in json.dumps(result)
    assert "[redacted," in result["frontmatter"]["metadata"]["reference"]


def test_source_and_decoded_scans_do_not_duplicate_the_same_warning():
    result = checks.run_checks(files(f"metadata:\n  api_key: {VALUE}"))
    assert (
        len([item for item in result["items"] if item["code"] == "secret_suspected"])
        == 1
    )


def test_noncredential_values_keep_existing_false_positive_policy():
    result = checks.run_checks(
        files(f"metadata: {{reference: {VALUE}, api_key: {'a' * 32}}}")
    )
    assert not any(item["code"] == "secret_suspected" for item in result["items"])
    assert result["frontmatter"]["metadata"] == {
        "reference": VALUE,
        "api_key": "a" * 32,
    }


@pytest.mark.django_db
def test_checks_api_and_publish_warning_gate_use_decoded_secrets(
    user, organization, workspace
):
    source = files(f'metadata: {{api_key: "\\x41{TAIL}"}}')

    def post(view, payload):
        request = APIRequestFactory().post("/model-hub/skills/", payload, format="json")
        force_authenticate(request, user=user)
        request.__dict__["organization"] = organization
        request.__dict__["workspace"] = workspace
        return view.as_view()(request)

    checked = post(views.SkillChecksView, {"files": source})
    assert checked.status_code == 200, checked.data
    assert VALUE not in json.dumps(checked.data)
    assert checked.data["result"]["summary"]["warnings"] == 1
    refused = post(views.SkillPushView, {"files": source})
    assert refused.status_code == 422, refused.data
    assert refused.data["code"] == "warnings_unacknowledged"
    assert VALUE not in json.dumps(refused.data)
    assert refused.data["detail"]["items"][0]["code"] == "secret_suspected"
    assert not HubSkillVersion.objects.exists()
    accepted = post(
        views.SkillPushView, {"files": source, "acknowledge_warnings": True}
    )
    assert accepted.status_code == 201, accepted.data
    version = HubSkillVersion.objects.get()
    # Diagnostics are not the source of truth for publication or identity.
    assert version.frontmatter["metadata"]["api_key"] == VALUE
    assert version.files.get(path="SKILL.md").blob.content == source[0]["content"]
    assert VALUE not in json.dumps(version.checks)
