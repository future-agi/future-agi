"""Exact reviewed non-row/separate-phase effects; never runtime permissions.

A new write, changed receiver or moved transaction boundary invalidates the full
function AST binding. Only the listed call is removed from row-write effects;
issuance edges and every other call remain subject to the completeness gate.
"""

import ast
from hashlib import sha256

EFFECT_CLASSIFICATIONS = {
    "ee.evals.localizer.error_localizer._build_full_image_block": {
        "function": "4b2144472727bfa6836f9c482264d7f8ac764f44f80c805dd5ca61b316a28590",
        "call": "image.save(buf, format='JPEG')",
        "justification": "PIL Image.open/convert/resize saves JPEG bytes into BytesIO, not a database row.",
    },
    "ee.evals.localizer.error_localizer._create_overlapping_patches": {
        "function": "5aa24d8d710088eeff6524880bdd63742d85f3c416d9b4f038884451238a452e",
        "call": "image.crop((x, y, x + patch_w, y + patch_h)).save(buf, format='JPEG')",
        "justification": "PIL Image.crop saves JPEG bytes into BytesIO, not a database row.",
    },
    "accounts.gcp_marketplace_utils.process_signup": {
        "function": "e34b91a930898baf31efb7db405ed51c5c1adac4a9dde5cce6c999c5a125458d",
        "call": "_finish_signup(gcp_account, onboarding_token)",
        "callee": "accounts.gcp_marketplace_utils._finish_signup",
        "callee_function": "acf8ddaad8196cdec4b38b82ea285422502758d6f589267688542a6b2bc03a3b",
        "justification": "Procurement finishing runs either in the existing-owner branch that returns without issuance, or after the explicit issuance transaction exits. It must not hold identity locks across provider calls. Only these two callsites are separate phases; every identity write remains checked.",
    },
    "ee.falcon_ai.consumers.FalconAIConsumer._handle_chat": {
        "function": "a536ec71a5141635178000e5d41c4a0bdd24242bb487d7a1f35ab2146fa52655",
        "call": "self._save_user_message(conversation_id, message_content, files_metadata)",
        "callee": "ee.falcon_ai.consumers.FalconAIConsumer._save_user_message",
        "callee_function": "864d963a892bb8f35b22a113074a95f19f5768c2f46af7f3dcf88578d8293cea",
        "justification": "database_sync_to_async persists the message in its completed autocommit DB invocation before asyncio.create_task starts the agent. No enclosing atomic transaction spans the message and tool issuance. Only this exact preparation call is separate.",
    },
}


def _canonical(node):
    """Return ast.dump text as Python 3.13 prints it by default, on every Python.

    Fields that are None or an empty list are left out, as 3.13 leaves them out,
    and a constant None is kept. The pins above are hashes of this text.
    """
    if isinstance(node, ast.AST):
        cls = type(node)
        parts = []
        for name in node._fields:
            try:
                value = getattr(node, name)
            except AttributeError:
                continue
            if value is None and getattr(cls, name, ...) is None:
                continue
            if (value is None or value == []) and not isinstance(
                node, (ast.Constant, ast.MatchSingleton)
            ):
                continue
            parts.append(f"{name}={_canonical(value)}")
        return f"{cls.__name__}({', '.join(parts)})"
    if isinstance(node, list):
        return "[" + ", ".join(_canonical(item) for item in node) + "]"
    return repr(node)


def fingerprint(function):
    return sha256(_canonical(function).encode()).hexdigest()


def qualify(key, function, functions, nodes):
    row = EFFECT_CLASSIFICATIONS.get(key)
    if not row or fingerprint(function) != row["function"]:
        return
    if "callee" in row and (
        row["callee"] not in functions
        or fingerprint(functions[row["callee"]][1]) != row["callee_function"]
    ):
        return
    for node in nodes:
        if isinstance(node, ast.Call) and ast.unparse(node) == row["call"]:
            node.issuance_non_row_effect = True
