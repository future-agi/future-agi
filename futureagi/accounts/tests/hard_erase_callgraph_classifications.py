"""Exact reviewed non-row/separate-phase effects; never runtime permissions.

A new write, changed receiver or moved transaction boundary invalidates the full
function AST binding. Only the listed call is removed from row-write effects;
issuance edges and every other call remain subject to the completeness gate.
"""

import ast
import runpy
from pathlib import Path

fingerprint = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_ast_fingerprint.py"))
)["fingerprint"]

EFFECT_CLASSIFICATIONS = {
    "ee.evals.localizer.error_localizer._build_full_image_block": {
        "function": "7543d0c7511303ebc2eff6c934d200349cc2d7c1937dcf4c51f4e28562629a2d",
        "call": "image.save(buf, format='JPEG')",
        "justification": "PIL Image.open/convert/resize saves JPEG bytes into BytesIO, not a database row.",
    },
    "ee.evals.localizer.error_localizer._create_overlapping_patches": {
        "function": "1ed77f1ad96cf2559bd166c14d4d77ff5925caf9c9f4f359ac4b8747eba57ed3",
        "call": "image.crop((x, y, x + patch_w, y + patch_h)).save(buf, format='JPEG')",
        "justification": "PIL Image.crop saves JPEG bytes into BytesIO, not a database row.",
    },
    "accounts.gcp_marketplace_utils.process_signup": {
        "function": "df67e74f33de2c7f658baf6d68491def48cddfa04c1fe758bbeaca9cc76d9876",
        "call": "_finish_signup(gcp_account, onboarding_token)",
        "callee": "accounts.gcp_marketplace_utils._finish_signup",
        "callee_function": "d9879b7ae9f2bb0d60d1641191cdaf718e02eab3e02887cc48e5059c39c9b496",
        "justification": "gcp_marketplace_utils.py:520 returns the existing owner at :521 without issuance; :557 runs after the :529-554 entry exits. User creation (:531), membership (:541) and organization.save (:554) remain guarded and unclassified. _finish_signup (:562-571) performs procurement finishing, not identity issuance. Only these exact two separate-phase calls are classified; provider calls must not hold identity locks.",
    },
    "ee.falcon_ai.consumers.FalconAIConsumer._handle_chat": {
        "function": "a274c5914f90d14019595b199c478d1555e0dda84acec4a9b97e0877fbe242a0",
        "call": "self._save_user_message(conversation_id, message_content, files_metadata)",
        "callee": "ee.falcon_ai.consumers.FalconAIConsumer._save_user_message",
        "callee_function": "8ee57d9b018a1dbea11f92f97c9e79e82bebe068c6aa7abad4045058a9aa1cf4",
        "justification": "database_sync_to_async persists the message in its completed autocommit DB invocation before asyncio.create_task starts the agent. No enclosing atomic transaction spans the message and tool issuance. Only this exact preparation call is separate.",
    },
}


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
