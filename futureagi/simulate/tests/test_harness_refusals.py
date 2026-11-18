"""Every refusal the ALK sandbox can return on the connect and read path reads as a
sentence that says what went wrong and what to do about it (TH-8978).

The codes and where the kit raises them are listed in
~/Desktop/TH-8978/birch-refusal-codes.md. A code the user can fix names the field and
the one action to take; a code only the platform can cause says so and keeps the code
for support; any other message is shown as the kit wrote it.
"""

import re

import pytest

from simulate.services import harness_refusals
from simulate.services.harness_refusals import refusal_sentence

# Codes the user can fix on the page, as the kit sends them, with the value (a name the
# caller chose, never a secret) the sentence must keep.
USER_CODES = [
    ("A subfolder needs a GitHub repository to read it from.", None),
    ("subfolder_requires_github_source", None),
    ("github_repository_invalid", None),
    ("github_repository_url_must_point_to_repository_or_branch", None),
    ("github_ref_invalid", None),
    ("github_ref_conflicts_with_repository_url", None),
    ("environment_values_limit_exceeded", None),
    ("environment_values_size_exceeded", None),
    ("environment_value_conflicts_with_secret_reference", None),
    ("environment_name_invalid: MY-KEY", "MY-KEY"),
    (
        "environment_name_reserved: PATH (the name steers a process the runner starts, "
        "or is the runner's own; send the value under another name and read that name "
        "in the agent)",
        "PATH",
    ),
    ("environment_value_contains_nul: API_URL", "API_URL"),
    ("secret_reference_name_invalid: my key", "my key"),
    ("secret_alias_reserved: HOME", "HOME"),
    (
        "connector_config_name_reserved: PYTHONPATH (the name steers a process the "
        "runner starts, or is the runner's own; send the value under another name and "
        "read that name in the agent)",
        "PYTHONPATH",
    ),
    ("source may contain at most 5000 files", None),
    ("source exceeds the 50 MiB file or 200 MiB bundle limit", None),
    ("duplicate source path: app/main.py", "app/main.py"),
    ("unsafe source path: ../etc/passwd", "../etc/passwd"),
    (
        "config/.env.local may contain secrets; upload it through the .env control",
        "config/.env.local",
    ),
    ("uploaded source was not found", None),
    ("uploaded source is incomplete", None),
    ("sandbox_secret_reference_refused: OPENAI_API_KEY", "OPENAI_API_KEY"),
    ("sandbox_vault_reference_unknown: OPENAI_API_KEY", "OPENAI_API_KEY"),
    ("secret_file_reference_unavailable_or_consumed", None),
    ("environment_name is invalid", None),
    ("environment_name is reserved", None),
    ("credential file may not exceed 5 MiB", None),
    ("credential file is empty", None),
    ("Google application credentials must be a valid JSON object", None),
    ("provider_phone_requires_e164_phone_number", None),
    ("phone_connect_only_requires_e164_phone_number", None),
    ("phone_connect_only_requires_target_system_prompt", None),
    ("livekit_connect_only_requires_agent_name", None),
    ("livekit_connect_only_requires_target_system_prompt", None),
    ("connect_only_requires_assistant_id", None),
    ("connect_only_requires_agent_id", None),
    ("provider_import_requires_assistant_id", None),
    ("provider_import_requires_agent_id", None),
    ("provider_import_event_path_invalid", None),
    ("provider_import_tool_path_invalid", None),
]

# Codes only the platform's own request can cause: the user can't fix them.
PLATFORM_CODES = [
    "exactly_one_source_required",
    "provider_only_requires_connect_only_provider",
    "A subfolder needs the generic harness, which this job didn't ask for.",
    "github_commit_sha_required",
    "github_commit_sha_invalid",
    "source_locator_missing: archive",
    "one relative path is required per file",
    "source_id is invalid",
    "secret_file_reference_invalid",
    "provider_phone_requires_connect_only_voice",
    "phone_dialer_config_is_platform_owned",
    "retell_chat_environment_backed_not_supported",
    "environment_backed_requires_vapi_or_retell",
    "environment_backed_target_is_provision_output: assistant_id",
    "provider_lifecycle_manifest_must_be_source_relative",
    "provider_import_requires_vapi_or_retell",
    "provider_mode_only_supported_for_vapi_retell_or_phone_connect_only",
    "retry_domain_unsafe: provider",
    "retry_backoff_invalid",
    "target_model_must_be_nonempty_string",
    "public_material_admission_local_operator_required",
    "harness_job_version_unsupported: 9",
    "execution_manifest_invalid",
    "execution_manifest_duplicate_key",
    "execution_manifest_count_mismatch",
    "local_job_cannot_use_private_platform_github_source",
    "hosted_job_cannot_use_local_path",
    "image_source_not_hosted",
    "hosted_scenario_count_out_of_range",
    "hosted_isolation_must_be_dedicated_vm",
    "hosted_parallelism_exceeds_cpu",
    "local_only_not_hosted",
    "hosted_secret_manager_unsupported: OPENAI_API_KEY",
    "hosted_secret_purpose_invalid: OPENAI_API_KEY",
    "hosted_privileged_execution_forbidden",
    "hosted_runtime_control_forbidden",
    "hosted_source_must_be_read_only",
    "resolved_secret_forbidden: agent.secret_refs",
    "source_path must be an existing directory",
    "source_path is outside allowed roots",
]

_SNAKE_CODE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+){2,}\b")


_SECRET_FILE_SUFFIX = " may contain secrets; upload it through the .env control"


def _code_of(message):
    if message.endswith(_SECRET_FILE_SUFFIX):
        return "may contain secrets"
    return message.split(": ", 1)[0].split(" (", 1)[0]


@pytest.mark.parametrize(
    ("message", "value"), USER_CODES, ids=[_code_of(m)[:60] for m, _ in USER_CODES]
)
def test_a_code_the_user_can_fix_reads_as_what_failed_and_what_to_do(message, value):
    sentence = refusal_sentence(message)

    assert sentence != message
    # Two sentences at least: what went wrong, then the repair.
    assert sentence.endswith(".") and sentence.count(". ") >= 1
    assert not _SNAKE_CODE.search(sentence.replace(value or "\0", ""))
    if value is not None:
        assert value in sentence
    # The kit's own hint is replaced by the sentence's repair, never doubled.
    assert "steers a process the runner starts" not in sentence


@pytest.mark.parametrize("message", PLATFORM_CODES, ids=lambda m: _code_of(m)[:60])
def test_a_code_only_the_platform_causes_says_so_and_keeps_the_code(message):
    sentence = refusal_sentence(message)

    assert sentence.startswith(
        "We couldn't start this because of a problem on our side"
    )
    assert f"({_code_of(message)})" in sentence
    assert sentence.endswith(".")


@pytest.mark.parametrize(
    "message",
    [
        "a_code_the_kit_adds_later",
        "a_code_the_kit_adds_later: SOME_NAME",
        "The sandbox said something new.",
        "",
    ],
)
def test_an_unknown_message_is_shown_as_the_kit_wrote_it(message):
    assert refusal_sentence(message) == message


def test_every_code_is_classified_once():
    user = {_code_of(message) for message, _ in USER_CODES}
    platform = {_code_of(message) for message in PLATFORM_CODES}

    assert not user & platform
    assert user <= harness_refusals.known_codes()
    assert platform <= harness_refusals.known_codes()
    assert harness_refusals.known_codes() == user | platform
