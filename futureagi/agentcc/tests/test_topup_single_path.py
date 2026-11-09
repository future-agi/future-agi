"""Ruling 492 (money, double credit): one way in for every top-up.

Every Checkout session, PaymentIntent and top-up invoice credit goes through
the ledger's ``fulfil_topup`` with its ``payment_intent_id``, whose partial
unique index stops a session and its PaymentIntent from both crediting.
``apply_topup`` and ``apply_topup_pi`` are the ledger's internal pre-2a hooks:
apart from the ledger service that defines them and BL-LEDGER's own tests,
nothing in this tree names them: no view, task, adapter or test stand-in.
The one exception is BL-LEDGER-2A-c's own prose about the rows R's pre-2a
hook wrote: each of those exact lines is allowed in its own file, and no
other line there.
(BL-STRIPE's adapter lives in the private cloud tree and is main's own step.)
"""

import os
import re
from pathlib import Path

FUTUREAGI = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve().relative_to(FUTUREAGI).as_posix()
# The ledger service defines both hooks; BL-LEDGER's tests are its own.
ALLOWED_FILES = {"ee/usage/services/managed_credits.py", THIS_FILE}
ALLOWED_DIRS = ("ee/usage/tests/",)
# BL-LEDGER-2A-c's prose about the rows R's pre-2a apply_topup wrote (a model
# docstring, the read-only report's docstring and help text). Each exact line
# is allowed, in its own file only; a call, an import, a getattr string or any
# other line naming a hook in these files still fails.
DOCUMENTED_MENTIONS = {
    "ee/usage/models/managed_credits.py": frozenset(
        {"pre-2a ``apply_topup``) gets it when a replay names it, so that"}
    ),
    "ee/usage/management/commands/ledger_legacy_topup_report.py": frozenset(
        {
            "BL-LEDGER-2A-c, ruling 513.3. R's pre-2a ``apply_topup`` credited"
            " a Checkout",
            "\"Read-only, one-time report of pre-2a top-ups (R's apply_topup)"
            ' credited "',
        }
    ),
}
# Never walked: environments and caches, and the private cloud tree.
SKIPPED_DIRS = {"node_modules", "__pycache__", "migrations"}
SKIPPED_PATHS = {"ee/cloud"}
INTERNAL_HOOK = re.compile(r"\bapply_topup(?:_pi)?\b")


def _python_files():
    for root, dirs, files in os.walk(FUTUREAGI):
        rel_root = Path(root).relative_to(FUTUREAGI).as_posix()
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d not in SKIPPED_DIRS
            and (f"{rel_root}/{d}" if rel_root != "." else d) not in SKIPPED_PATHS
        )
        for name in sorted(files):
            if name.endswith(".py"):
                rel = name if rel_root == "." else f"{rel_root}/{name}"
                yield rel


def _hits(rel, text):
    allowed = DOCUMENTED_MENTIONS.get(rel, frozenset())
    return [
        f"{rel}:{number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        if INTERNAL_HOOK.search(line) and line.strip() not in allowed
    ]


def test_no_code_outside_the_ledger_names_apply_topup():
    hits = []
    for rel in _python_files():
        if rel in ALLOWED_FILES or rel.startswith(ALLOWED_DIRS):
            continue
        text = (FUTUREAGI / rel).read_text(encoding="utf-8", errors="replace")
        hits += _hits(rel, text)
    assert hits == [], (
        "credit a Checkout session, PaymentIntent or invoice through "
        "fulfil_topup(org, payment=TopupPayment(..., payment_intent_id=...)), "
        "never apply_topup or apply_topup_pi:\n" + "\n".join(hits)
    )


def test_a_documented_mention_allows_only_its_own_line_in_its_own_file():
    rel = "ee/usage/models/managed_credits.py"
    (mention,) = DOCUMENTED_MENTIONS[rel]
    text = (FUTUREAGI / rel).read_text(encoding="utf-8")

    assert any(line.strip() == mention for line in text.splitlines())
    assert _hits(rel, f"        {mention}") == []
    # A call, a getattr string or a changed line in the same file still fails,
    # and the same line in any other file fails too.
    assert _hits(rel, "        return mc.apply_topup(org, 5_000_000)") != []
    assert _hits(rel, '        getattr(mc, "apply_topup_pi")(org)') != []
    assert _hits(rel, f"        {mention}  # mc.apply_topup(org)") != []
    assert _hits("agentcc/views/managed_credits.py", f"        {mention}") != []
