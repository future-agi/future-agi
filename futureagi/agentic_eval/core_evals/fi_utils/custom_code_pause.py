"""A deployment can pause custom code evaluations (CUSTOM_CODE_EVALS_PAUSED).

While the pause is on, code that a customer wrote or changed is refused before
any call to the code executor, with a sentence the customer reads. Built-in
evaluators keep running, except a short list that is withheld by name.

Built-in means the code is a system template's own: a saved EvalTemplate with
owner "system" and no organization or workspace (the test native_run_binding
uses), or one of that template's saved versions that no organization or
workspace owns either. Any other code is a customer's, including an unchanged
copy of a built-in saved under an organization and an unsaved draft (an
EvalTemplate built in memory has owner "system" by default, so the row is
always read from the database).

refuse_paused_template_code runs where the evaluator is built, with the
template at hand. refuse_paused_code and paused_code_refusal run where only the
code is known (custom_code_eval and the executor client): there, code runs only
when it is byte-identical to a stored built-in's that is not withheld.
"""

from __future__ import annotations

import hashlib
import os
import re
import time

from agentic_eval.core_evals.fi_utils.exceptions import CustomCodeEvalPausedError

PAUSED_MESSAGE = (
    "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
)
BUILTIN_PAUSED_MESSAGE = "{name} is temporarily unavailable on Future AGI Cloud."
PAUSED_ERROR_CODE = "CUSTOM_CODE_EVALS_PAUSED"

# Built-in code that uses any of these is withheld while paused.
_WITHHELD_CALLS = re.compile(
    r"\b(?:eval|exec|compile|__import__|open)\s*\("
    r"|\bimportlib\b|\bsubprocess\b|\bos\.(?:system|popen)\b|\bctypes\b"
    r"|\b(?:pickle|marshal)\.loads\b"
    r"|\burlopen\b|\burllib\.request\b|\bhttp\.client\b|\bsocket\b"
    r"|\bhttpx\b|\baiohttp\b|\brequests\.\w+\s*\(|\bimport\s+requests\b"
    r"|\bfrom\s+requests\b"
)

_SYSTEM_CODE_TTL_SECONDS = 300
# (when read, digests of built-in code that runs, {digest: name} of withheld built-ins)
_system_code_cache: tuple[float, frozenset[str], dict[str, str]] | None = None


def custom_code_paused() -> bool:
    """Whether this deployment pauses custom code evaluations."""
    try:
        from django.conf import settings

        return bool(getattr(settings, "CUSTOM_CODE_EVALS_PAUSED", False))
    except Exception:  # Django settings not configured (a script outside the app)
        return os.getenv("CUSTOM_CODE_EVALS_PAUSED", "false").lower() in (
            "true",
            "1",
            "yes",
        )


def is_withheld_builtin_code(code: object) -> bool:
    """Whether built-in ``code`` is withheld while paused."""
    return isinstance(code, str) and bool(_WITHHELD_CALLS.search(code))


def _digest(code: object) -> str | None:
    if not isinstance(code, str):
        return None
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _system_templates():
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.no_workspace_objects.filter(
        owner="system", organization__isnull=True, workspace__isnull=True
    )


def _version_codes(templates):
    """(template id, code) of every saved version of ``templates`` that no
    organization or workspace owns; a version stamped with one is never trusted."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    rows = EvalTemplateVersion._base_manager.filter(
        eval_template__in=templates,
        organization__isnull=True,
        workspace__isnull=True,
    ).values_list("eval_template_id", "config_snapshot")
    for template_id, snapshot in rows:
        if isinstance(snapshot, dict):
            yield template_id, snapshot.get("code")


def _system_code_index() -> tuple[frozenset[str], dict[str, str]]:
    """Digests of stored built-in code that runs, and {digest: template name}
    of stored built-in code that is withheld."""
    global _system_code_cache
    now = time.monotonic()
    if (
        _system_code_cache is None
        or now - _system_code_cache[0] > _SYSTEM_CODE_TTL_SECONDS
    ):
        templates = _system_templates()
        names = {}
        codes = []
        for pk, name, config in templates.order_by("name").values_list(
            "pk", "name", "config"
        ):
            names[pk] = name
            if isinstance(config, dict):
                codes.append((name, config.get("code")))
        codes.extend(
            (names.get(pk, ""), code) for pk, code in _version_codes(templates)
        )
        runs, withheld = set(), {}
        for name, code in codes:
            digest = _digest(code)
            if digest is None:
                continue
            if is_withheld_builtin_code(code):
                withheld.setdefault(digest, name)
            else:
                runs.add(digest)
        _system_code_cache = (now, frozenset(runs), withheld)
    return _system_code_cache[1], _system_code_cache[2]


def _stored_builtin(template, code: object) -> str | None:
    """The saved built-in ``template``'s name when ``code`` is its own stored code
    or one of its saved versions', read from the database; None otherwise."""
    pk = getattr(template, "pk", None)
    if pk is None or not isinstance(code, str):
        return None
    row = _system_templates().filter(pk=pk)
    stored = row.values_list("name", "config").first()
    if stored is None:
        return None
    name, config = stored
    if isinstance(config, dict) and config.get("code") == code:
        return name
    if any(version_code == code for _, version_code in _version_codes(row)):
        return name
    return None


def is_system_template_code(template, code: object) -> bool:
    """Whether ``code`` is the built-in ``template``'s own code and may run while paused."""
    return _stored_builtin(template, code) is not None and not is_withheld_builtin_code(
        code
    )


def _refusal_message(code: object) -> str | None:
    """The sentence for code only the code is known of, or None when it may run."""
    runs, withheld = _system_code_index()
    digest = _digest(code)
    if digest in runs:
        return None
    if digest in withheld:
        return BUILTIN_PAUSED_MESSAGE.format(name=withheld[digest])
    return PAUSED_MESSAGE


def refuse_paused_template_code(template, code: object) -> None:
    """Raise CustomCodeEvalPausedError, while paused, unless ``code`` is the
    built-in ``template``'s own and not withheld."""
    if not custom_code_paused():
        return
    name = _stored_builtin(template, code)
    if name is None:
        raise CustomCodeEvalPausedError(PAUSED_MESSAGE)
    if is_withheld_builtin_code(code):
        raise CustomCodeEvalPausedError(BUILTIN_PAUSED_MESSAGE.format(name=name))


def refuse_paused_custom_code_template(template) -> None:
    """For callers that charge before they build the evaluator: raise
    CustomCodeEvalPausedError, while paused, for a CustomCodeEval template that
    may not run, so the refusal comes before anything is charged."""
    config = getattr(template, "config", None)
    if isinstance(config, dict) and config.get("eval_type_id") == "CustomCodeEval":
        refuse_paused_template_code(template, config.get("code"))


def refuse_paused_code(code: object) -> None:
    """Raise CustomCodeEvalPausedError, while paused, for code that is not a
    stored built-in's that may run."""
    if custom_code_paused():
        message = _refusal_message(code)
        if message is not None:
            raise CustomCodeEvalPausedError(message)


def paused_code_refusal(code: object) -> dict | None:
    """The executor client's answer, while paused, for code that may not run:
    the setup error custom_code_eval shows as is."""
    if custom_code_paused():
        message = _refusal_message(code)
        if message is not None:
            return {
                "status": "error",
                "data": message,
                "setup_error": True,
                "error_code": PAUSED_ERROR_CODE,
            }
    return None
