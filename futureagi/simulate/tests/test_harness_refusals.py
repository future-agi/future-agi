"""Every refusal the ALK sandbox can return on the connect and read path reads as a
sentence that says what went wrong and what to do about it (TH-8978).

A code the user can fix names the field and the one action to take; a code only the
platform can cause says so and keeps the code for support; any other code gets a generic
sentence and its code, never the kit's own message.
"""

import re

import pytest

from simulate.services import harness_refusals
from simulate.services.harness_refusals import refusal_sentence

# Codes the user can fix on the page, as the kit sends them, with the value (a name the
# caller chose, never a secret) the sentence must keep.
USER_CODES = [
    ("github_repository_invalid", None),
    ("github_repository_url_must_point_to_repository_or_branch", None),
    ("github_ref_invalid", None),
    ("environment_values_limit_exceeded", None),
    # A name that fails the check for a setting name or a path is left out (ADR-010).
    ("environment_name_invalid: MY-KEY", None),
    ("secret_reference_name_invalid: my key", None),
    ("unsafe source path: ../etc/passwd", None),
    ("environment_values_size_exceeded", None),
    ("environment_value_conflicts_with_secret_reference", None),
    (
        "environment_name_reserved: PATH (the name steers a process the runner starts, "
        "or is the runner's own; send the value under another name and read that name "
        "in the agent)",
        "PATH",
    ),
    ("environment_value_contains_nul: API_URL", "API_URL"),
    ("secret_alias_reserved: HOME", "HOME"),
    ("source may contain at most 5000 files", None),
    ("source exceeds the 50 MiB file or 200 MiB bundle limit", None),
    ("duplicate source path: app/main.py", "app/main.py"),
    (
        "config/.env.local may contain secrets; upload it through the .env control",
        "config/.env.local",
    ),
    ("uploaded source was not found", None),
    ("uploaded source is incomplete", None),
    ("sandbox_secret_reference_refused: OPENAI_API_KEY", "OPENAI_API_KEY"),
    # Dileep, #4184: a reserved name typed into Additional environment values.
    (
        "connector_config_name_reserved: HTTP_PROXY (the name steers a process the runner "
        "starts, or is the runner's own; send the value under another name and read that "
        "name in the agent)",
        "HTTP_PROXY",
    ),
    ("secret_file_reference_unavailable_or_consumed", None),
    ("environment_name is invalid", None),
    ("environment_name is reserved", None),
    ("credential file may not exceed 5 MiB", None),
    ("credential file is empty", None),
    ("Google application credentials must be a valid JSON object", None),
]

# Codes only the platform's own request can cause: the user can't fix them.
PLATFORM_CODES = [
    "exactly_one_source_required",
    "provider_only_requires_connect_only_provider",
    "A subfolder needs the generic harness, which this job didn't ask for.",
    "github_commit_sha_required",
    "github_commit_sha_invalid",
    # The page has no subfolder field, and it takes the branch from the repository URL.
    "A subfolder needs a GitHub repository to read it from.",
    "subfolder_requires_github_source",
    "github_ref_conflicts_with_repository_url",
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
    # Hawk L6 on #4184: a sandbox with no token configured is our configuration fault.
    "sandbox_token_not_configured",
    # Dileep, #4184: the platform sends platform-vault refs the sandbox refuses on a hosted job.
    "sandbox_vault_reference_unknown: OPENAI_API_KEY",
]

# Our-side refusals a retry can't fix (Dileep, #4184): the page sends no commit on the sandbox
# path, and the sandbox refuses the platform's vault refs. Their sentence never says to retry.
NO_RETRY = [
    "github_commit_sha_required",
    "github_commit_sha_invalid",
    "sandbox_vault_reference_unknown: OPENAI_API_KEY",
]


@pytest.fixture
def logged(monkeypatch):
    seen = []

    class _Log:
        def error(self, event, **fields):
            seen.append(("error", event, fields.get("code"), fields.get("kit_message")))

        def warning(self, event, **fields):
            seen.append(("warning", event, fields.get("kit_message")))

    monkeypatch.setattr(harness_refusals, "logger", _Log())
    return seen


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
    elif ": " in message:
        # A name that fails the check is left out, never echoed.
        assert message.split(": ", 1)[1].split(" (", 1)[0] not in sentence
    # The kit's own hint is replaced by the sentence's repair, never doubled.
    assert "steers a process the runner starts" not in sentence


@pytest.mark.parametrize("message", PLATFORM_CODES, ids=lambda m: _code_of(m)[:60])
def test_a_code_only_the_platform_causes_says_so_keeps_the_code_and_is_logged(
    message, logged
):
    sentence = refusal_sentence(message)

    assert sentence.startswith(
        "We couldn't start the check because of a problem on our side"
    )
    # Hawk L5 on #4184: the brackets hold a code, never a kit phrase.
    shown = re.search(r"\(([^)]*)\)", sentence).group(1)
    assert re.fullmatch(r"[a-z][a-z0-9_]*", shown)
    code = _code_of(message)
    if re.fullmatch(r"[a-z][a-z0-9_]*", code):
        assert shown == code
    assert sentence.endswith(".")
    # Hawk L3: the kit's whole message goes to the log with the code.
    assert logged == [("error", "harness_sandbox_refusal_on_our_side", shown, message)]


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("a_code_the_kit_adds_later", "a_code_the_kit_adds_later"),
        ("a_code_the_kit_adds_later: SOME_NAME", "a_code_the_kit_adds_later"),
        # Out of this week's path (ADR-010, 5): these take the fallback for now.
        (
            "livekit_connect_only_requires_agent_name",
            "livekit_connect_only_requires_agent_name",
        ),
        ("connect_only_requires_assistant_id", "connect_only_requires_assistant_id"),
        ("The sandbox said something new: sk-live-pasted-here", None),
        ("", None),
    ],
)
def test_an_unknown_code_gets_a_generic_sentence_never_the_kits_message(
    message, code, logged
):
    sentence = refusal_sentence(message)

    assert sentence.startswith("The agent sandbox refused this")
    assert ("(" + code + ")" in sentence) if code else "(" not in sentence
    if message and message != code:
        assert message not in sentence
    assert "SOME_NAME" not in sentence and "sk-live" not in sentence
    # The kit's message goes to the server log, not to the page.
    assert logged == [("warning", "harness_sandbox_refusal_unknown", message)]


@pytest.mark.parametrize(
    "message",
    [
        "environment_value_contains_nul: " + "A" * 65,
        "environment_value_contains_nul: sk-live-0123456789",
        "secret_alias_reserved: bad name",
        "duplicate source path: /etc/passwd",
        "duplicate source path: app/../../secret",
        "duplicate source path: " + "a/" * 150 + "x",
    ],
)
def test_a_name_that_is_not_a_plain_setting_name_or_path_is_left_out(message):
    sentence = refusal_sentence(message)
    name = message.split(": ", 1)[1]

    assert name not in sentence
    assert "{name}" not in sentence and "  " not in sentence
    assert sentence.endswith(".")


def test_every_code_is_classified_once():
    user = {_code_of(message) for message, _ in USER_CODES}
    platform = {_code_of(message) for message in PLATFORM_CODES}

    assert not user & platform
    assert user <= harness_refusals.known_codes()
    assert platform <= harness_refusals.known_codes()
    assert harness_refusals.known_codes() == user | platform


# Hawk L1 on #4184: the bracketed-hint split is for reserved setting names only; a path keeps
# its brackets.
def test_a_path_with_brackets_is_shown_whole():
    sentence = refusal_sentence("duplicate source path: docs/Report (1).md")

    assert "docs/Report (1).md" in sentence


@pytest.mark.parametrize("message", NO_RETRY)
def test_an_our_side_refusal_a_retry_cannot_fix_never_says_try_again(message):
    sentence = refusal_sentence(message)

    assert sentence.startswith(
        "We couldn't start the check because of a problem on our side"
    )
    assert "Try again" not in sentence and "try again" not in sentence


def test_whether_a_message_is_our_side_is_known():
    assert harness_refusals.is_our_side("github_commit_sha_required") is True
    assert harness_refusals.is_our_side("hosted_isolation_must_be_dedicated_vm") is True
    assert harness_refusals.is_our_side("environment_name_invalid: A") is False
    assert harness_refusals.is_our_side("a_code_the_kit_adds_later") is False
