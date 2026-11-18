"""Each amendment-line format ALK writes (agent-learning-kit ``fi/alk/harness/amend.py`` and ``builder.py``), read
by the platform's one parser. A new ALK format must be added here and in ``review_compose._AMENDMENT_SUBJECTS``."""

import pytest

from simulate.services.harness_versioning.review_compose import (
    _amendment_owners,
    amendment_subject,
)

DASH = "—"

ALK_LINES = [
    (
        f"rule added {DASH} Refunds over 500 need a manager: asked in chat",
        ("rule", ["Refunds over 500 need a manager"]),
    ),
    (
        f"rule removed {DASH} No refunds on Sundays: dropped by the owner",
        ("rule", ["No refunds on Sundays"]),
    ),
    (f"tool removed {DASH} legacy_lookup: no seam", ("tool", ["legacy_lookup"])),
    (
        f"tool corrected {DASH} issue_refund: amount is cents",
        ("tool", ["issue_refund"]),
    ),
    (
        "issue_refund.reason widened by damaged, late: seen in the logs",
        ("tool", ["issue_refund"]),
    ),
    (
        "cancel_order added from orders.py: the agent calls it",
        ("tool", ["cancel_order"]),
    ),
    ("modality chat -> voice: it is a phone agent", ("contract_field", ["modality"])),
    # A participant's permission follows the tools it names.
    (
        "billing may use issue_refund, refund_status, as asked: the owner said so",
        ("tool", ["issue_refund", "refund_status"]),
    ),
    ("free text no format reads", None),
]


@pytest.mark.parametrize("line, subject", ALK_LINES)
def test_each_alk_amendment_format_is_read(line, subject):
    assert amendment_subject(line) == subject


def test_a_worker_permission_line_goes_with_the_tool_change_it_names():
    changes = [
        {"id": "tool:issue_refund", "kind": "tool", "key": "issue_refund", "patch": []},
        {
            "id": "rule:x",
            "kind": "rule",
            "key": "x",
            "patch": [{"op": "add", "path": "", "value": "Be kind"}],
        },
    ]
    line = "billing may use issue_refund, as asked: the owner said so"
    assert _amendment_owners(line, changes) == ["tool:issue_refund"]
