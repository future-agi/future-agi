"""What the page says when the ALK sandbox refuses a request on the connect and read path.

The kit answers with a code (``github_ref_invalid``), a code and the name it is about
(``environment_value_contains_nul: API_URL``), or a short phrase (``uploaded source was
not found``). The platform owns every sentence a user reads (ADR-010,
~/Desktop/coordination/architecture/decisions/ADR-010-refusal-codes-and-sentences.md):

- a refusal the user can fix says what failed, naming the field by its label on the
  Connect page, then the one thing to do;
- a refusal only the platform's own request can cause shares one sentence that keeps the
  code for support, and is logged as an error: it is our bug;
- any other code gets a generic sentence and its code. The kit's own message never
  reaches the page, because it can repeat what was typed; it goes to the server log.

A name the refusal carries is shown only when it passes the check for a setting name or
a relative path, and is short: it came from what the user typed, and a secret pasted
where a name belongs must not be echoed.

The codes and where the kit raises them are listed in ~/Desktop/TH-8978/birch-refusal-codes.md.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import structlog

logger = structlog.get_logger(__name__)

# The kinds of name a refusal can carry, and the check each must pass to be shown.
_SETTING = "setting"
_PATH = "path"
_SETTING_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_PATH_PART = re.compile(r"[A-Za-z0-9._@+() -]{1,128}")
_PATH_MAX = 200

# A refusal the user can fix: (sentence, kind of name it carries or None). ``{name}``
# stands for " <name>" when the name passes its check, and for nothing when it doesn't.
_USER: dict[str, tuple[str, str | None]] = {
    # Agent source: GitHub repository.
    "github_repository_invalid": (
        "The Repository URL isn't a GitHub repository we can read. Enter it as "
        "https://github.com/owner/repository.",
        None,
    ),
    "github_repository_url_must_point_to_repository_or_branch": (
        "The Repository URL points at a file or commit inside the repository. Use the "
        "repository's link, or its .../tree/branch link.",
        None,
    ),
    "github_ref_invalid": (
        "The branch in the Repository URL isn't a valid branch name. Copy the branch's "
        "link from GitHub and paste it again.",
        None,
    ),
    # Agent source: Upload folder.
    "source may contain at most 5000 files": (
        "The uploaded folder has more than 5000 files. Upload only the agent's folder, "
        "without dependencies or build output.",
        None,
    ),
    "source exceeds the 50 MiB file or 200 MiB bundle limit": (
        "The uploaded folder is too large: a file is over 50 MiB or the folder is over "
        "200 MiB. Leave out large data, dependencies and build output, and upload again.",
        None,
    ),
    "duplicate source path": (
        "Two files in the uploaded folder have the same path{name}. Remove one and "
        "upload the folder again.",
        _PATH,
    ),
    "unsafe source path": (
        "A file in the uploaded folder{name} points outside the folder. Upload the "
        "folder again without it.",
        _PATH,
    ),
    "may contain secrets": (
        "The uploaded file{name} may hold secrets. Leave it out of the folder and add "
        "its values under Additional environment values instead.",
        _PATH,
    ),
    "uploaded source was not found": (
        "The uploaded folder is no longer available. Upload the folder again.",
        None,
    ),
    "uploaded source is incomplete": (
        "The folder upload didn't finish. Upload the folder again.",
        None,
    ),
    # Additional environment values.
    "environment_values_limit_exceeded": (
        "There are too many Additional environment values. Remove the ones the agent "
        "doesn't read.",
        None,
    ),
    "environment_values_size_exceeded": (
        "The Additional environment values are too large together. Shorten or remove "
        "the largest ones, and use Upload credential file for a credential file.",
        None,
    ),
    "environment_value_conflicts_with_secret_reference": (
        "The same name is set both in Additional environment values and as a "
        "credential. Keep it in one place.",
        None,
    ),
    "environment_name_invalid": (
        "The environment value name{name} isn't valid. Use letters, digits and "
        "underscores, starting with a letter or underscore.",
        _SETTING,
    ),
    "environment_name is invalid": (
        "The environment value name isn't valid. Use letters, digits and underscores, "
        "starting with a letter or underscore.",
        None,
    ),
    "environment_name_reserved": (
        "The environment value name{name} is used by the runner itself. Send the value "
        "under another name and read that name in the agent.",
        _SETTING,
    ),
    "environment_name is reserved": (
        "That environment value name is used by the runner itself. Choose another name "
        "and read that name in the agent.",
        None,
    ),
    "environment_value_contains_nul": (
        "The environment value{name} contains a null character. Remove it and save "
        "again.",
        _SETTING,
    ),
    # Credentials.
    "secret_reference_name_invalid": (
        "The credential name{name} isn't valid. Use letters, digits and underscores, "
        "starting with a letter or underscore.",
        _SETTING,
    ),
    "secret_alias_reserved": (
        "The credential name{name} is used by the runner itself. Store the credential "
        "under another name and read that name in the agent.",
        _SETTING,
    ),
    "sandbox_secret_reference_refused": (
        "The credential{name} can't be used in the cloud check. Enter its value again "
        "or upload it with Upload credential file.",
        _SETTING,
    ),
    "sandbox_vault_reference_unknown": (
        "The credential{name} wasn't found. Enter its value again or choose another "
        "saved credential.",
        _SETTING,
    ),
    "secret_file_reference_unavailable_or_consumed": (
        "The uploaded credential file was already used or has expired. Use Upload "
        "credential file to upload it again.",
        None,
    ),
    "credential file may not exceed 5 MiB": (
        "The credential file is over 5 MiB. Upload the credential file itself, not a "
        "folder or archive.",
        None,
    ),
    "credential file is empty": (
        "The credential file is empty. Upload the file that holds the credentials.",
        None,
    ),
    "Google application credentials must be a valid JSON object": (
        "The Google credentials file isn't a JSON key file. Upload the service account "
        "key you downloaded from Google Cloud.",
        None,
    ),
}

# A refusal only the platform's own request can cause: the user can't fix it.
_PLATFORM = frozenset(
    {
        "exactly_one_source_required",
        "provider_only_requires_connect_only_provider",
        # The page has no subfolder field, and takes the branch from the Repository URL.
        "A subfolder needs a GitHub repository to read it from.",
        "subfolder_requires_github_source",
        "A subfolder needs the generic harness, which this job didn't ask for.",
        "github_ref_conflicts_with_repository_url",
        # The page resolves the commit before it sends the job.
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
        # Only a local sandbox reads a source path; the hosted page never sends one.
        "source_path must be an existing directory",
        "source_path is outside allowed roots",
        # The sandbox has no token configured: our deployment, not the user's request.
        "sandbox_token_not_configured",
    }
)

# The our-side entries that are kit phrases, not codes: the brackets show a slug instead, so no
# kit text is shown (ADR-010, 7).
_PLATFORM_SLUGS = {
    "A subfolder needs a GitHub repository to read it from.": (
        "subfolder_needs_github_repository"
    ),
    "A subfolder needs the generic harness, which this job didn't ask for.": (
        "subfolder_needs_generic_harness"
    ),
    "one relative path is required per file": "source_path_per_file_required",
    "source_id is invalid": "source_id_invalid",
    "source_path must be an existing directory": "source_path_not_a_directory",
    "source_path is outside allowed roots": "source_path_outside_allowed_roots",
}

_PLATFORM_SENTENCE = (
    "We couldn't start the check because of a problem on our side ({code}). Try again; "
    "if it keeps happening, contact support."
)
_UNKNOWN_SENTENCE = (
    "The agent sandbox refused this{code}. Check the agent source and its settings, "
    "then try again; if it keeps happening, contact support."
)

# The one refusal whose name comes before the phrase: "{path} may contain secrets; ...".
_SECRET_FILE_SUFFIX = " may contain secrets; upload it through the .env control"
# What an unknown refusal's code may look like to be shown at all.
_CODE = re.compile(r"[a-z][a-z0-9_]{0,99}")


def known_codes() -> frozenset[str]:
    """Every code this module has its own sentence for."""
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
    return code, rest.strip()


def _shown_name(name: str | None, kind: str | None) -> str | None:
    """The name as the page may show it, or None when it isn't plainly a name."""
    if not name or kind is None:
        return None
    if kind == _SETTING:
        # The kit adds a hint in brackets after a reserved name; the sentence has its own.
        name = name.split(" (", 1)[0].strip()
        return name if _SETTING_NAME.fullmatch(name) else None
    path = PurePosixPath(name)
    if (
        len(name) > _PATH_MAX
        or path.is_absolute()
        or not path.parts
        or any(part == ".." or not _PATH_PART.fullmatch(part) for part in path.parts)
    ):
        return None
    return name


def refusal_sentence(message: str) -> str:
    """The sentence the page shows for one refusal message from the sandbox."""
    code, name = _split(message)
    if code in _USER:
        template, kind = _USER[code]
        shown = _shown_name(name, kind)
        return template.format(name=f" {shown}" if shown else "")
    if code in _PLATFORM:
        shown = _PLATFORM_SLUGS.get(code, code)
        logger.error(
            "harness_sandbox_refusal_on_our_side", code=shown, kit_message=message[:500]
        )
        return _PLATFORM_SENTENCE.format(code=shown)
    logger.warning("harness_sandbox_refusal_unknown", kit_message=message[:500])
    shown_code = code if _CODE.fullmatch(code) else None
    return _UNKNOWN_SENTENCE.format(code=f" ({shown_code})" if shown_code else "")
