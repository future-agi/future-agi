"""What the page says when the ALK sandbox refuses a request on the connect and read path.

The sandbox answers with a code (``github_ref_invalid``), a code and the name it is about
(``environment_name_invalid: MY-KEY``), or a short phrase (``uploaded source was not
found``). Each is turned into a sentence that says what went wrong and the one thing to do
about it, in the shape of the product plan's Connect failures (TH-8978):

- a refusal the user can fix names the field and keeps the name the caller chose (a name,
  never a value);
- a refusal only the platform's own request can cause says so, and keeps the code so
  support can find it;
- any other message is shown as the kit wrote it.

The codes and where the kit raises them are listed in ~/Desktop/TH-8978/birch-refusal-codes.md.
"""

from __future__ import annotations

# A refusal the user can fix: what went wrong, then the repair. ``{name}`` is the name the
# refusal is about, as the caller sent it.
_USER = {
    # Choosing the source.
    "A subfolder needs a GitHub repository to read it from.": (
        "A subfolder can only be read from a GitHub repository. Choose a GitHub "
        "repository, or clear the subfolder."
    ),
    "subfolder_requires_github_source": (
        "A subfolder can only be read from a GitHub repository. Choose a GitHub "
        "repository, or clear the subfolder."
    ),
    "github_repository_invalid": (
        "The repository isn't a GitHub repository we can read. Enter it as owner/name "
        "or as its github.com link."
    ),
    "github_repository_url_must_point_to_repository_or_branch": (
        "The repository link points inside the repository, not at it. Use the "
        "repository's link or a branch link, not a file or commit link."
    ),
    "github_ref_invalid": (
        "The branch name isn't valid. Choose the branch again from the list."
    ),
    "github_ref_conflicts_with_repository_url": (
        "The repository link names a different branch from the one chosen. Choose one: "
        "remove the branch from the link, or pick that branch."
    ),
    # Uploading the source.
    "source may contain at most 5000 files": (
        "The upload has more than 5000 files. Upload only the agent's folder, without "
        "dependencies or build output."
    ),
    "source exceeds the 50 MiB file or 200 MiB bundle limit": (
        "The upload is too large: a file is over 50 MiB or the whole upload is over "
        "200 MiB. Leave out large data, dependencies and build output, and upload again."
    ),
    "duplicate source path": (
        "Two uploaded files have the same path, {name}. Remove one and upload again."
    ),
    "unsafe source path": (
        "The uploaded file {name} points outside the agent's folder. Upload the folder "
        "again without it."
    ),
    "may contain secrets": (
        "The uploaded file {name} may hold secrets. Leave it out of the upload and add "
        "its values under Environment values or Secrets instead."
    ),
    "uploaded source was not found": (
        "The uploaded source is no longer available. Upload the agent's source again."
    ),
    "uploaded source is incomplete": (
        "The upload didn't finish. Upload the agent's source again."
    ),
    # Environment values and secrets.
    "environment_values_limit_exceeded": (
        "There are too many environment values. Remove the ones the agent doesn't read."
    ),
    "environment_values_size_exceeded": (
        "The environment values are too large together. Shorten or remove the largest "
        "values; upload a credential file as a file instead."
    ),
    "environment_value_conflicts_with_secret_reference": (
        "The same name is set both as an environment value and as a secret. Keep it in "
        "one place."
    ),
    "environment_name_invalid": (
        "The environment value name {name} isn't valid. Use letters, digits and "
        "underscores, starting with a letter or underscore."
    ),
    "environment_name is invalid": (
        "The environment value name isn't valid. Use letters, digits and underscores, "
        "starting with a letter or underscore."
    ),
    "environment_name_reserved": (
        "The name {name} is used by the runner itself. Send the value under another "
        "name and read that name in the agent."
    ),
    "environment_name is reserved": (
        "That environment value name is used by the runner itself. Choose another name "
        "and read that name in the agent."
    ),
    "environment_value_contains_nul": (
        "The environment value {name} contains a null character. Remove it and save "
        "again."
    ),
    "secret_reference_name_invalid": (
        "The secret name {name} isn't valid. Use letters, digits and underscores, "
        "starting with a letter or underscore."
    ),
    "secret_alias_reserved": (
        "The secret name {name} is used by the runner itself. Store the secret under "
        "another name and read that name in the agent."
    ),
    "connector_config_name_reserved": (
        "The connection setting {name} is used by the runner itself. Rename the setting "
        "and read the new name in the agent."
    ),
    "sandbox_secret_reference_refused": (
        "The secret {name} can't be used in the cloud check. Add it again under Secrets "
        "and select it there."
    ),
    "sandbox_vault_reference_unknown": (
        "The secret {name} wasn't found. Add it under Secrets, or select another one."
    ),
    "secret_file_reference_unavailable_or_consumed": (
        "The uploaded credential file was already used or has expired. Upload it again."
    ),
    "credential file may not exceed 5 MiB": (
        "The credential file is over 5 MiB. Upload the credential file itself, not a "
        "folder or archive."
    ),
    "credential file is empty": (
        "The credential file is empty. Upload the file that holds the credentials."
    ),
    "Google application credentials must be a valid JSON object": (
        "The Google credentials file isn't a JSON key file. Upload the service account "
        "key you downloaded from Google Cloud."
    ),
    # Calling a provider's agent.
    "provider_phone_requires_e164_phone_number": (
        "The phone number isn't in international format. Enter it with + and the "
        "country code, for example +14155550100."
    ),
    "phone_connect_only_requires_e164_phone_number": (
        "The phone number isn't in international format. Enter it with + and the "
        "country code, for example +14155550100."
    ),
    "phone_connect_only_requires_target_system_prompt": (
        "The agent's system prompt is missing or too long. Paste the agent's prompt, up "
        "to 65,536 characters."
    ),
    "livekit_connect_only_requires_agent_name": (
        "The LiveKit agent name is missing. Enter the name the agent registers with "
        "LiveKit."
    ),
    "livekit_connect_only_requires_target_system_prompt": (
        "The agent's system prompt is missing or too long. Paste the agent's prompt, up "
        "to 65,536 characters."
    ),
    "connect_only_requires_assistant_id": (
        "The Vapi assistant ID is missing. Enter the assistant's ID from Vapi."
    ),
    "connect_only_requires_agent_id": (
        "The Retell agent ID is missing. Enter the agent's ID from Retell."
    ),
    "provider_import_requires_assistant_id": (
        "The Vapi assistant ID is missing. Enter the assistant's ID from Vapi."
    ),
    "provider_import_requires_agent_id": (
        "The Retell agent ID is missing. Enter the agent's ID from Retell."
    ),
    "provider_import_event_path_invalid": (
        "The event path isn't a path on your server. Enter it starting with /, for "
        "example /events."
    ),
    "provider_import_tool_path_invalid": (
        "The tool path isn't a path on your server. Enter it starting with /, for "
        "example /tools."
    ),
}

# A refusal only the platform's own request can cause: the user can't fix it.
_PLATFORM = frozenset(
    {
        "exactly_one_source_required",
        "provider_only_requires_connect_only_provider",
        "A subfolder needs the generic harness, which this job didn't ask for.",
        "github_commit_sha_required",
        "github_commit_sha_invalid",
        "source_locator_missing",
        "one relative path is required per file",
        "source_id is invalid",
        "secret_file_reference_invalid",
        "provider_phone_requires_connect_only_voice",
        "phone_dialer_config_is_platform_owned",
        "retell_chat_environment_backed_not_supported",
        "environment_backed_requires_vapi_or_retell",
        "environment_backed_target_is_provision_output",
        "provider_lifecycle_manifest_must_be_source_relative",
        "provider_import_requires_vapi_or_retell",
        "provider_mode_only_supported_for_vapi_retell_or_phone_connect_only",
        "retry_domain_unsafe",
        "retry_backoff_invalid",
        "target_model_must_be_nonempty_string",
        "public_material_admission_local_operator_required",
        "harness_job_version_unsupported",
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
        "hosted_secret_manager_unsupported",
        "hosted_secret_purpose_invalid",
        "hosted_privileged_execution_forbidden",
        "hosted_runtime_control_forbidden",
        "hosted_source_must_be_read_only",
        "resolved_secret_forbidden",
        "source_path must be an existing directory",
        "source_path is outside allowed roots",
    }
)

_PLATFORM_SENTENCE = (
    "We couldn't start this because of a problem on our side ({code}). Try again, and "
    "contact support if it keeps happening."
)

# The one refusal whose name comes before the phrase: "{path} may contain secrets; ...".
_SECRET_FILE_SUFFIX = " may contain secrets; upload it through the .env control"


def known_codes() -> frozenset[str]:
    """Every code this module has a sentence for."""
    return frozenset(_USER) | _PLATFORM


def _split(message: str) -> tuple[str, str | None]:
    """The code a refusal names, and the name it is about, if it carries one."""
    if message.endswith(_SECRET_FILE_SUFFIX):
        return "may contain secrets", message.removesuffix(_SECRET_FILE_SUFFIX)
    if message in _USER or message in _PLATFORM:
        return message, None
    code, separator, rest = message.partition(": ")
    if not separator:
        return message, None
    # The kit adds a hint in brackets after a reserved name; the sentence has its own.
    return code, rest.split(" (", 1)[0].strip()


def refusal_sentence(message: str) -> str:
    """The sentence the page shows for one refusal message from the sandbox."""
    code, name = _split(message)
    if code in _USER:
        template = _USER[code]
        if "{name}" in template:
            if not name:
                return message
            return template.format(name=name)
        return template
    if code in _PLATFORM:
        return _PLATFORM_SENTENCE.format(code=code)
    return message
