"""A call's World state as the call detail serves it: every decision named, its fallback classified by the editor's
rule, an approval's verdict and a write after a refusal said outright, whether the changes could be read said once,
approvals and record changes typed, every list present, and whether the person spoke."""

from __future__ import annotations

from types import SimpleNamespace

from simulate.selectors.call_detail import has_customer, trial_state
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
from simulate.views.run_results_v3 import TrialStateSerializer

LISTS = (
    "state_changes",
    "duplicates",
    "decisions",
    "pre_run",
    "approvals",
    "usage",
    "usage_summary",
    "notices",
)


def _shown(state, **metadata):
    return trial_state(
        SimpleNamespace(id="call", call_metadata={TRIAL_STATE_KEY: state, **metadata})
    )


def _decisions(*decisions, **metadata):
    return _shown({"decisions": list(decisions)}, **metadata)["decisions"]


def test_a_fallback_is_classified_as_the_editor_classifies_it():
    resolved = {"harness_call_actors": {"resolved": [{"id": "boss", "name": "Dana"}]}}
    action, actor, silent, scheduled = _decisions(
        {
            "actor": "clerk",
            "play": "timeout",
            "fallback": "refunds.decline",
            "action": "refunds.decline",
            "outcome": "fallback_action",
        },
        {
            "actor": "clerk",
            "play": "timeout",
            "fallback": "boss",
            "outcome": "answered_by",
        },
        {
            "actor": "clerk",
            "play": "timeout",
            "fallback": "silent",
            "outcome": "timed_out",
        },
        {"actor": "clerk", "play": "mocked", "action": "orders.put"},
        **resolved,
    )

    # An action the world took for the actor is an action, never another actor.
    assert [
        (one["fallback"]["kind"], one["fallback"]["value"])
        for one in (action, actor, silent)
    ] == [("action", "refunds.decline"), ("actor", "boss"), ("silent", "silent")]
    assert scheduled["fallback"] is None


def test_an_action_fallback_named_like_an_actor_is_still_the_action():
    resolved = {
        "harness_call_actors": {"resolved": [{"id": "approve", "name": "Approver"}]}
    }
    [decision] = _decisions(
        {
            "actor": "clerk",
            "play": "timeout",
            "fallback": "approve",
            "outcome": "fallback_action",
        },
        **resolved,
    )

    assert decision["fallback"]["kind"] == "action"


def _approval(by, action, verdict, after_refusal):
    return {
        "request": {"action": "discounts.apply", "at": 1, "action_id": "a1"},
        "decision": {
            "by": by,
            "action": action,
            "outcome": "committed",
            "at": 2,
            "verdict": verdict,
        },
        "before_decision": [],
        "after_decision": [{"action": "discounts.apply", "outcome": "committed"}]
        if after_refusal
        else [],
        "acted_before_decision": False,
        "verdict": verdict,
        "committed_after_refusal": after_refusal,
    }


def test_an_approval_s_verdict_and_a_write_after_a_refusal_are_said_on_the_approval_and_its_decision():
    shown = _shown(
        {
            "decisions": [
                {
                    "actor": "boss",
                    "play": "live",
                    "action": "approvals.change",
                    "verdict": "refused",
                },
                {
                    "actor": "finance",
                    "play": "live",
                    "action": "approvals.change",
                    "verdict": "approved",
                },
                {"actor": "clerk", "play": "mocked", "action": "orders.put"},
            ],
            "approvals": [
                _approval("boss", "approvals.change", "refused", True),
                _approval("finance", "approvals.change", "approved", False),
            ],
        }
    )
    refused, approved = shown["approvals"]

    assert (refused["verdict"], refused["committed_after_refusal"]) == ("refused", True)
    assert refused["decision"]["verdict"] == "refused"
    assert (approved["verdict"], approved["committed_after_refusal"]) == (
        "approved",
        False,
    )
    # A decision takes the flag from the approval it answered: the same actor, by the same action.
    boss, finance, clerk = shown["decisions"]
    assert (boss["verdict"], boss["committed_after_refusal"]) == ("refused", True)
    assert (finance["verdict"], finance["committed_after_refusal"]) == (
        "approved",
        False,
    )
    assert (clerk["verdict"], clerk["committed_after_refusal"]) == (None, False)
    served = TrialStateSerializer(shown).data
    assert served["approvals"][0]["committed_after_refusal"] is True
    assert served["decisions"][0]["committed_after_refusal"] is True


def test_a_trial_from_before_verdicts_reads_none_and_no_refusal():
    old_line = _approval("boss", "approvals.change", None, False)
    for key in ("verdict", "committed_after_refusal"):
        old_line.pop(key)
    old_line["decision"].pop("verdict")
    shown = _shown(
        {
            "decisions": [
                {"actor": "boss", "play": "live", "action": "approvals.change"}
            ],
            "approvals": [old_line, {"request": "junk", "verdict": "maybe"}],
        }
    )

    line, junk = shown["approvals"]
    assert (
        line["verdict"],
        line["committed_after_refusal"],
        line["decision"]["verdict"],
    ) == (None, False, None)
    assert (junk["verdict"], junk["committed_after_refusal"]) == (None, False)
    assert (
        shown["decisions"][0]["verdict"],
        shown["decisions"][0]["committed_after_refusal"],
    ) == (None, False)
    assert TrialStateSerializer(data=shown).is_valid(), TrialStateSerializer(
        data=shown
    ).errors


def test_egress_refusals_a_start_s_baseline_and_both_notice_shapes_are_typed():
    shown = _shown(
        {
            "state_changes": [
                {
                    "who": "agent",
                    "name": "the agent",
                    "attempted": 2,
                    "committed": 0,
                    "refused": 2,
                    "faulted": 0,
                    "duplicate": 0,
                    "committed_actions": [],
                    "blocked": [
                        {"host": "refunds.example.com", "method": "CONNECT", "count": 2}
                    ],
                }
            ],
            "start": {
                "state": "differs",
                "tables": ["orders"],
                "against": "sealed_seed",
            },
            "notices": [
                {
                    "code": "fault_unknown_action",
                    "message": "m",
                    "action": "x",
                    "on_call": 1,
                    "mode": "error",
                },
                {
                    "code": "evidence_incomplete",
                    "count": 2,
                    "message": "Two pieces of evidence never arrived.",
                },
            ],
        }
    )
    served = TrialStateSerializer(shown).data

    assert served["state_changes"][0]["blocked"] == [
        {"host": "refunds.example.com", "method": "CONNECT", "count": 2}
    ]
    assert served["start"] == {
        "state": "differs",
        "tables": ["orders"],
        "against": "sealed_seed",
        "line": None,
    }
    assert {one["code"]: one for one in served["notices"]}["evidence_incomplete"][
        "count"
    ] == 2
    by_code = {one["code"]: one for one in shown["notices"]}
    assert by_code["evidence_incomplete"] == {
        "code": "evidence_incomplete",
        "message": "Two pieces of evidence never arrived.",
        "count": 2,
    }
    assert by_code["fault_unknown_action"]["action"] == "x"
    assert TrialStateSerializer(data=shown).is_valid(), TrialStateSerializer(
        data=shown
    ).errors


def test_every_decision_is_named_by_its_id_when_nameless():
    named, nameless = _decisions(
        {"actor": "lead", "name": "Sam Lee", "play": "mocked"},
        {"actor": "manager", "play": "mocked"},
    )

    assert (named["name"], nameless["name"]) == ("Sam Lee", "manager")


def test_whether_the_changes_could_be_read_is_said_once():
    unreadable = _shown({"unreadable": True})
    # A record stored before the flag moved to the top reads the same.
    nested = _shown({"records": {"unreadable": True}})
    readable = _shown({"records": {"changes": [], "skipped": [], "truncated": 0}})

    assert unreadable["unreadable"] is True and "records" not in unreadable
    assert nested["unreadable"] is True and "records" not in nested
    assert readable["unreadable"] is False
    assert readable["records"] == {"changes": [], "skipped": [], "truncated": 0}


def test_every_list_is_present_on_a_trial_that_had_none():
    shown = _shown({"notices": [{"code": "fault_not_applied", "message": "x"}]})

    assert {key: shown[key] for key in LISTS if key != "notices"} == {
        key: [] for key in LISTS if key != "notices"
    }
    assert shown["unreadable"] is False
    assert shown["notices"] == [{"code": "fault_not_applied", "message": "x"}]


def test_approvals_and_record_changes_are_typed():
    shown = _shown(
        {
            "approvals": [
                {
                    "request": {
                        "action": "discounts.apply",
                        "at": 10,
                        "action_id": "a1",
                        "extra": 1,
                    },
                    "decision": {
                        "by": "boss",
                        "action": "approvals.change",
                        "outcome": "committed",
                        "at": 12.5,
                    },
                    "before_decision": [],
                    "after_decision": [
                        {"action": "discounts.apply", "outcome": "committed"}
                    ],
                    "acted_before_decision": False,
                },
                {"request": "junk"},
            ],
            "records": {
                "changes": [
                    {
                        "table": "deals",
                        "key": [8],
                        "kind": "changed",
                        "before": {"discount": 0},
                        "after": {"discount": 10},
                        "fields": ["discount"],
                    },
                    {"table": "deals", "kind": "renamed"},
                ],
                "skipped": [{"table": "big", "why": "too many rows"}],
                "truncated": 2,
            },
        }
    )

    first, junk = shown["approvals"]
    assert first["request"] == {
        "action": "discounts.apply",
        "at": 10.0,
        "action_id": "a1",
    }
    assert first["decision"] == {
        "by": "boss",
        "verdict": None,
        "name": "boss",
        "action": "approvals.change",
        "outcome": "committed",
        "at": 12.5,
    }
    assert first["after_decision"] == [
        {"action": "discounts.apply", "outcome": "committed"}
    ]
    assert junk["request"] == {"action": None, "at": None, "action_id": None}
    assert junk["decision"]["name"] is None
    assert junk["acted_before_decision"] is False
    assert shown["records"]["changes"] == [
        {
            "table": "deals",
            "key": [8],
            "kind": "changed",
            "before": {"discount": 0},
            "after": {"discount": 10},
            "fields": ["discount"],
        }
    ]
    assert shown["records"]["truncated"] == 2
    served = TrialStateSerializer(shown).data
    assert served["approvals"][0]["decision"]["by"] == "boss"
    assert served["records"]["changes"][0]["kind"] == "changed"


def test_the_served_trial_state_carries_every_declared_list_and_flag():
    shown = _shown(
        {
            "decisions": [
                {
                    "actor": "boss",
                    "play": "timeout",
                    "fallback": "decline",
                    "outcome": "declined",
                }
            ],
            "limits": {"max_messages": 4, "model": None},
        }
    )
    served = TrialStateSerializer(shown).data

    assert set(LISTS) | {"unreadable", "limits"} <= set(served)
    assert served["decisions"][0]["fallback"]["kind"] == "decline"
    assert served["limits"]["max_messages"] == 4


def test_an_approval_names_who_decided_as_the_decisions_do():
    shown = _shown(
        {
            "decisions": [{"actor": "boss", "name": "Dana Cole", "play": "live"}],
            "approvals": [
                {"request": {"action": "discounts.apply"}, "decision": {"by": "boss"}}
            ],
        }
    )

    assert shown["approvals"][0]["decision"]["name"] == "Dana Cole"
    assert (
        TrialStateSerializer(shown).data["approvals"][0]["decision"]["name"]
        == "Dana Cole"
    )


def test_whether_the_person_spoke_is_said_by_the_server():
    assert (
        has_customer([{"speaker_role": "assistant"}, {"speaker_role": "user"}]) is True
    )
    assert has_customer([{"speaker_role": "task"}, {"speaker_role": "actor"}]) is False
    assert has_customer([]) is False


def test_a_code_the_harness_does_not_write_is_left_out_and_the_rest_are_typed():
    [odd] = _decisions(
        {
            "actor": "x",
            "play": "live",
            "action": "a.put",
            "outcome": "exploded",
            "problem": "bad",
            "action_outcome": "?",
        }
    )

    assert not {"outcome", "problem", "action_outcome"} & set(odd)
    assert TrialStateSerializer(data=_shown({"decisions": [odd]})).is_valid()


RESOLVED = [
    {"id": "friend", "name": "Rowan", "role": "friend", "channel": "audio_mix"},
    {"id": "never", "name": "Never", "role": "lead", "channel": "audio_mix"},
    {"id": "clerk", "name": "Wendy", "role": "clerk"},
]
SUMMARY = [
    {"actor": "friend", "triggered": True, "delivered": 2},
    {"actor": "never", "triggered": False},
    {"actor": "clerk", "drawn": True},
]


def test_participants_are_who_spoke_then_who_acted_never_who_was_only_planned():
    from simulate.selectors.call_detail import participants

    metadata = {"harness_call_actors": {"resolved": RESOLVED, "summary": SUMMARY}}
    turns = [
        {"speaker_role": "user"},
        {"speaker_role": "actor", "actor_id": "friend", "speaker_name": "Rowan"},
        {"speaker_role": "actor", "actor_id": "friend", "speaker_name": "Rowan"},
    ]
    actions = [
        {"kind": "agent_call", "actor_id": None},
        {"kind": "world_actor_action", "actor_id": "clerk", "actor_name": "Wendy"},
    ]

    assert participants(metadata, turns, actions) == [
        {"id": "friend", "name": "Rowan", "role": "friend", "channel": "audio_mix"},
        {"id": "clerk", "name": "Wendy", "role": "clerk", "channel": None},
    ]


def test_a_call_where_only_a_world_actor_acted_lists_that_actor():
    from simulate.selectors.call_detail import participants

    metadata = {"harness_call_actors": {"resolved": RESOLVED, "summary": SUMMARY}}
    actions = [
        {"kind": "world_actor_action", "actor_id": "clerk", "actor_name": "Wendy"}
    ]

    assert participants(metadata, [{"speaker_role": "user"}], actions) == [
        {"id": "clerk", "name": "Wendy", "role": "clerk", "channel": None}
    ]


def test_a_call_stored_before_its_actors_were_recorded_reads_them_from_its_turns():
    from simulate.selectors.call_detail import participants

    turns = [
        {
            "speaker_role": "actor",
            "speaker_name": "Pat",
            "actor_role": "spouse",
            "channel": "audio_handoff",
        },
        {"speaker_role": "actor", "actor_id": "ivr", "speaker_name": ""},
        {"speaker_role": "actor", "speaker_name": "Pat"},
    ]

    assert participants({}, turns, []) == [
        {"id": None, "name": "Pat", "role": "spouse", "channel": "audio_handoff"},
        {"id": "ivr", "name": "ivr", "role": "", "channel": None},
    ]
