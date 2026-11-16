from datetime import UTC, datetime

from simulate.services.harness_worker_steps import (
    failure_placements,
    served_record,
    worker_steps,
)


def at(second):
    return f"2026-11-01T10:00:{second:02d}"


def handoffs(steps, parent=None):
    return [
        step for step in steps if step["kind"] == "handoff" and step["parent"] == parent
    ]


def answers(result):
    return [
        ((step["handoff"] or {}).get("task"), (step["returned"] or {}).get("result"))
        for step in handoffs(result["steps"])
    ]


HOTELS = {
    "seq": 1,
    "type": "delegated",
    "worker_id": "searcher",
    "task": "hotels",
    "delegation_id": "A",
    "at": at(1),
}
FLIGHTS = {**HOTELS, "seq": 2, "task": "flights", "delegation_id": "B"}


def answer(seq, delegation_id, result):
    return {
        "seq": seq,
        "type": "returned",
        "worker_id": "searcher",
        "delegation_id": delegation_id,
        "result": result,
        "at": at(seq + 2),
    }


def call(seq, delegation_id=None):
    event = {
        "seq": seq,
        "type": "tool_call",
        "worker_id": "searcher",
        "tool": "t",
        "at": at(seq),
    }
    return {**event, "delegation_id": delegation_id} if delegation_id else event


class TestWorkerSteps:
    def test_nests_a_hand_off_under_the_agent_that_made_it_with_its_calls_and_return(
        self,
    ):
        result = worker_steps(
            [
                {
                    "seq": 1,
                    "type": "delegated",
                    "worker_id": "lead",
                    "delegation_id": "L",
                    "at": at(1),
                },
                {
                    "seq": 2,
                    "type": "delegated",
                    "worker_id": "helper",
                    "by": "lead",
                    "delegation_id": "H",
                    "parent_delegation_id": "L",
                    "at": at(2),
                },
                {
                    "seq": 3,
                    "type": "tool_call",
                    "worker_id": "helper",
                    "delegation_id": "H",
                    "tool": "t",
                    "at": at(3),
                },
                {
                    "seq": 4,
                    "type": "returned",
                    "worker_id": "helper",
                    "delegation_id": "H",
                    "at": at(4),
                },
                {
                    "seq": 5,
                    "type": "returned",
                    "worker_id": "lead",
                    "delegation_id": "L",
                    "at": at(5),
                },
                {
                    "seq": 6,
                    "type": "tool_call",
                    "worker_id": None,
                    "tool": "reply",
                    "at": at(6),
                },
            ]
        )

        assert [
            (s["kind"], s["key"], s["parent"], s["depth"]) for s in result["steps"]
        ] == [
            ("handoff", 1, None, 1),
            ("handoff", 2, 1, 2),
            ("tool_call", 3, 2, 2),
            ("tool_call", 6, None, 0),
        ]
        lead, helper = result["steps"][0], result["steps"][1]
        assert lead["returned"]["seq"] == 5
        assert (lead["started_at"], lead["ended_at"]) == (
            "2026-11-01T10:00:01+00:00",
            "2026-11-01T10:00:05+00:00",
        )
        assert helper["ended_at"] == "2026-11-01T10:00:04+00:00"
        assert {
            k: result[k]
            for k in ("depth", "parallel", "overlap_unknown", "worker_depths")
        } == {
            "depth": 2,
            "parallel": 0,
            "overlap_unknown": False,
            "worker_depths": {"lead": 1, "helper": 2},
        }

    def test_marks_overlapping_siblings_parallel_and_keeps_a_return_with_no_hand_off_at_the_top(
        self,
    ):
        result = worker_steps(
            [
                {
                    "seq": 1,
                    "type": "delegated",
                    "worker_id": "a",
                    "delegation_id": "a1",
                    "at": at(1),
                },
                {
                    "seq": 2,
                    "type": "transferred",
                    "worker_id": "b",
                    "delegation_id": "b1",
                    "at": at(2),
                },
                {
                    "seq": 3,
                    "type": "returned",
                    "worker_id": "a",
                    "delegation_id": "a1",
                    "at": at(4),
                },
                {
                    "seq": 4,
                    "type": "returned",
                    "worker_id": "b",
                    "delegation_id": "b1",
                    "at": at(5),
                },
                {"seq": 5, "type": "returned", "worker_id": "c", "at": at(6)},
            ]
        )

        top = handoffs(result["steps"])
        assert [(s["worker_id"], s["parallel"]) for s in top] == [
            ("a", True),
            ("b", True),
            ("c", False),
        ]
        assert result["parallel"] == 2
        assert top[1]["transfer"] is True
        assert (top[2]["handoff"], top[2]["returned"]["seq"]) == (None, 5)

    def test_gives_each_of_two_open_hand_offs_its_own_return(self):
        result = worker_steps(
            [
                HOTELS,
                FLIGHTS,
                answer(3, "A", "some hotels"),
                answer(4, "B", "some flights"),
            ]
        )
        assert answers(result) == [
            ("hotels", "some hotels"),
            ("flights", "some flights"),
        ]

    def test_leaves_the_other_open_when_only_one_returned(self):
        assert answers(
            worker_steps([HOTELS, FLIGHTS, answer(3, "A", "some hotels")])
        ) == [
            ("hotels", "some hotels"),
            ("flights", None),
        ]

    def test_never_gives_a_hand_off_a_return_that_names_a_different_one(self):
        assert answers(worker_steps([HOTELS, answer(2, "gone", "someone else's")])) == [
            ("hotels", None),
            (None, "someone else's"),
        ]

    def test_files_a_tool_call_by_its_delegation_id_and_keeps_one_without_an_id_at_the_top_as_unknown(
        self,
    ):
        # Two hand-offs to one worker open at once: only a recorded id places a call under one.
        result = worker_steps([HOTELS, FLIGHTS, call(3, "A"), call(4)])
        assert [
            (s["key"], s["parent"], s["relationship"])
            for s in result["steps"]
            if s["kind"] == "tool_call"
        ] == [
            (3, 1, "observed"),
            (4, None, "unknown"),
        ]

    def test_a_call_with_no_id_stays_at_the_top_even_with_one_hand_off_open(self):
        # One open hand-off for its worker is not a record that the call ran in it.
        result = worker_steps([HOTELS, call(2)])
        assert [
            (s["key"], s["parent"], s["relationship"])
            for s in result["steps"]
            if s["kind"] == "tool_call"
        ] == [(2, None, "unknown")]

    def test_takes_a_missing_start_from_the_first_step_and_says_when_overlap_cannot_be_known(
        self,
    ):
        known = worker_steps(
            [
                {
                    "seq": 1,
                    "type": "delegated",
                    "worker_id": "a",
                    "delegation_id": "a1",
                    "at": at(1),
                },
                {
                    "seq": 2,
                    "type": "returned",
                    "worker_id": "a",
                    "delegation_id": "a1",
                    "at": at(5),
                },
                {
                    "seq": 3,
                    "type": "delegated",
                    "worker_id": "b",
                    "delegation_id": "b1",
                    "at": None,
                },
                {
                    "seq": 4,
                    "type": "tool_call",
                    "worker_id": "b",
                    "delegation_id": "b1",
                    "tool": "t",
                    "at": at(3),
                },
                {
                    "seq": 5,
                    "type": "returned",
                    "worker_id": "b",
                    "delegation_id": "b1",
                    "at": at(6),
                },
            ]
        )
        assert (known["parallel"], known["overlap_unknown"]) == (2, False)
        assert handoffs(known["steps"])[1]["started_at"] == "2026-11-01T10:00:03+00:00"

        unknown = worker_steps(
            [
                {
                    "seq": 1,
                    "type": "delegated",
                    "worker_id": "a",
                    "delegation_id": "a1",
                    "at": at(1),
                },
                {
                    "seq": 2,
                    "type": "returned",
                    "worker_id": "a",
                    "delegation_id": "a1",
                    "at": at(5),
                },
                {
                    "seq": 3,
                    "type": "delegated",
                    "worker_id": "b",
                    "delegation_id": "b1",
                    "at": None,
                },
                {
                    "seq": 4,
                    "type": "returned",
                    "worker_id": "b",
                    "delegation_id": "b1",
                    "at": at(6),
                },
            ]
        )
        assert (unknown["parallel"], unknown["overlap_unknown"]) == (0, True)

    def test_keeps_the_coordinators_own_call_at_the_top_after_control_passes_back(self):
        result = worker_steps(
            [
                {"seq": 1, "type": "transferred", "worker_id": None, "at": at(1)},
                {
                    "seq": 2,
                    "type": "tool_call",
                    "worker_id": None,
                    "tool": "t",
                    "at": at(2),
                },
            ]
        )
        assert [(s["kind"], s["parent"]) for s in result["steps"]] == [
            ("handoff", None),
            ("tool_call", None),
        ]

    def test_a_return_with_no_id_and_two_open_hand_offs_stays_at_the_top_as_unknown(
        self,
    ):
        result = worker_steps(
            [
                {"seq": 1, "type": "delegated", "worker_id": "a", "at": at(1)},
                {"seq": 2, "type": "delegated", "worker_id": "a", "at": at(2)},
                {"seq": 3, "type": "returned", "worker_id": "a", "at": at(3)},
            ]
        )
        assert [
            (s["key"], (s["returned"] or {}).get("seq"), s["relationship"])
            for s in handoffs(result["steps"])
        ] == [
            (1, None, "observed"),
            (2, None, "observed"),
            (3, 3, "unknown"),
        ]

    def test_reads_a_timestamp_with_a_zone_as_that_instant(self):
        result = worker_steps(
            [
                {
                    "seq": 1,
                    "type": "delegated",
                    "worker_id": "a",
                    "at": "2026-11-01T15:30:01+05:30",
                }
            ]
        )
        assert result["steps"][0]["started_at"] == "2026-11-01T10:00:01+00:00"

    def test_an_empty_record_has_no_steps(self):
        result = worker_steps([])
        assert (result["steps"], result["turns"], result["depth"]) == ([], [], 0)
        assert result["counts"]["hand_offs"] == 0 and not result["beyond_boundary"]
        assert result["asynchronous"] == 0

    def test_a_return_without_an_id_is_never_given_to_the_hand_off_still_open(
        self,
    ):
        result = worker_steps(
            [
                {
                    "seq": 1,
                    "type": "delegated",
                    "worker_id": "a",
                    "delegation_id": "x",
                    "at": at(1),
                },
                {
                    "seq": 2,
                    "type": "delegated",
                    "worker_id": "a",
                    "delegation_id": "y",
                    "at": at(2),
                },
                {
                    "seq": 3,
                    "type": "returned",
                    "worker_id": "a",
                    "delegation_id": "y",
                    "at": at(3),
                },
                {"seq": 4, "type": "returned", "worker_id": "a", "at": at(4)},
            ]
        )
        # A return with no id is not given to the hand-off still open: it stays at the top, unknown.
        assert [
            (s["key"], (s["returned"] or {}).get("seq"), s["relationship"])
            for s in handoffs(result["steps"])
        ] == [(1, None, "observed"), (2, 3, "observed"), (4, 4, "unknown")]

    def test_a_nested_hand_off_goes_under_its_recorded_parent_not_the_maker_s_latest(
        self,
    ):
        result = worker_steps(
            [
                {
                    "seq": 1,
                    "type": "delegated",
                    "worker_id": "lead",
                    "delegation_id": "p1",
                    "at": at(1),
                },
                {
                    "seq": 2,
                    "type": "delegated",
                    "worker_id": "lead",
                    "delegation_id": "p2",
                    "at": at(2),
                },
                {
                    "seq": 3,
                    "type": "delegated",
                    "worker_id": "helper",
                    "by": "lead",
                    "delegation_id": "c",
                    "parent_delegation_id": "p1",
                    "at": at(3),
                },
            ]
        )
        assert [(s["key"], s["parent"]) for s in result["steps"] if s["key"] == 3] == [
            (3, 1)
        ]

    def test_a_hand_off_with_no_start_starts_at_its_first_nested_hand_off(self):
        result = worker_steps(
            [
                {
                    "seq": 1,
                    "type": "delegated",
                    "worker_id": "b",
                    "delegation_id": "b1",
                    "at": None,
                },
                {
                    "seq": 2,
                    "type": "delegated",
                    "worker_id": "c",
                    "by": "b",
                    "delegation_id": "c1",
                    "parent_delegation_id": "b1",
                    "at": at(3),
                },
            ]
        )
        outer, inner = result["steps"]
        assert (inner["parent"], outer["started_at"]) == (
            1,
            "2026-11-01T10:00:03+00:00",
        )


def said(role, second, text):
    return {"role": role, "text": text, "at": at(second)}


class TestWorkerStepsByTurn:
    EVENTS = [
        {
            "seq": 1,
            "delegation_id": "r1",
            "type": "delegated",
            "worker_id": "refunds",
            "task": "refund 6109",
            "at": at(2),
        },
        {
            "seq": 2,
            "delegation_id": "r1",
            "type": "tool_call",
            "worker_id": "refunds",
            "tool": "issue_refund",
            "at": at(3),
        },
        {
            "seq": 3,
            "delegation_id": "r1",
            "type": "returned",
            "worker_id": "refunds",
            "result": "refunded",
            "at": at(4),
        },
        {
            "seq": 4,
            "delegation_id": "s1",
            "type": "delegated",
            "worker_id": "shipping",
            "task": "cancel 6110",
            "at": at(12),
        },
        {
            "seq": 5,
            "delegation_id": "n1",
            "type": "delegated",
            "worker_id": "notify",
            "task": "email the caller",
            "at": at(13),
        },
        {
            "seq": 6,
            "delegation_id": "n1",
            "type": "returned",
            "worker_id": "notify",
            "result": "sent",
            "at": at(14),
        },
    ]
    TURNS = [
        said("user", 1, "refund order 6109"),
        said("agent", 5, "done, $83 refunded"),
        said("user", 8, "thanks, what is the weather"),
        said("agent", 9, "sunny"),
        said("user", 11, "also cancel 6110"),
        said("agent", 16, "I could not cancel it yet"),
    ]

    def test_each_step_belongs_to_the_turn_its_hand_off_started_in(self):
        result = worker_steps(self.EVENTS, self.TURNS)
        assert [(s["key"], s["turn"]) for s in result["steps"]] == [
            (1, 1),
            (2, 1),
            (4, 3),
            (5, 3),
        ]

    def test_each_step_is_numbered_in_reading_order_within_its_turn(self):
        result = worker_steps(self.EVENTS, self.TURNS)
        assert [(s["turn"], s["order"]) for s in result["steps"]] == [
            (1, 1),
            (1, 2),
            (3, 1),
            (3, 2),
        ]

    def test_each_turn_with_steps_carries_its_request_and_the_reply_after_its_last_step(
        self,
    ):
        result = worker_steps(self.EVENTS, self.TURNS)
        assert [
            (t["turn"], t["request"]["text"], t["response"]["text"])
            for t in result["turns"]
        ] == [
            (1, "refund order 6109", "done, $83 refunded"),
            (3, "also cancel 6110", "I could not cancel it yet"),
        ]

    def test_a_worker_still_running_past_the_next_message_stays_in_the_turn_it_started(
        self,
    ):
        events = [
            {
                "seq": 1,
                "type": "delegated",
                "worker_id": "coder",
                "delegation_id": "c1",
                "at": at(2),
            },
            {
                "seq": 2,
                "type": "tool_call",
                "worker_id": "coder",
                "delegation_id": "c1",
                "tool": "run",
                "at": at(9),
            },
        ]
        result = worker_steps(
            events, [said("user", 1, "a"), said("agent", 3, "b"), said("user", 8, "c")]
        )
        assert [(s["key"], s["turn"]) for s in result["steps"]] == [(1, 1), (2, 1)]

    def test_work_before_the_caller_first_spoke_is_turn_zero_with_no_request(self):
        events = [{"seq": 1, "type": "delegated", "worker_id": "greeter", "at": at(1)}]
        result = worker_steps(
            events, [said("agent", 2, "hello"), said("user", 4, "hi")]
        )
        [only] = result["turns"]
        assert (only["turn"], only["request"]) == (0, None)
        assert only["response"]["text"] == "hello"

    def test_a_turn_whose_reply_never_came_has_no_response(self):
        events = [{"seq": 1, "type": "delegated", "worker_id": "a", "at": at(2)}]
        result = worker_steps(
            events, [said("user", 1, "a"), said("user", 5, "still there?")]
        )
        assert result["turns"][0]["response"] is None

    def test_a_step_with_no_time_has_no_turn_rather_than_the_one_before_it(self):
        events = [
            {"seq": 1, "type": "delegated", "worker_id": "a", "at": at(2)},
            {"seq": 2, "type": "delegated", "worker_id": "b", "at": None},
        ]
        result = worker_steps(events, [said("user", 1, "q"), said("agent", 3, "r")])
        assert [(s["key"], s["turn"]) for s in result["steps"]] == [(1, 1), (2, None)]
        last = result["turns"][-1]
        assert (last["turn"], last["request"], last["response"]) == (None, None, None)

    def test_a_step_after_the_last_message_a_cut_record_kept_has_no_turn(self):
        events = [
            {"seq": 1, "type": "delegated", "worker_id": "a", "at": at(2)},
            {"seq": 2, "type": "delegated", "worker_id": "b", "at": at(20)},
        ]
        turns = [said("user", 1, "q"), said("agent", 3, "r")]
        assert [s["turn"] for s in worker_steps(events, turns)["steps"]] == [1, 1]
        cut = worker_steps(events, turns, turns_cut=True)["steps"]
        assert [s["turn"] for s in cut] == [1, None]

    def test_a_message_said_while_the_steps_still_ran_is_not_the_reply(self):
        events = [
            {
                "seq": 1,
                "type": "delegated",
                "worker_id": "a",
                "delegation_id": "a1",
                "at": at(2),
            },
            {
                "seq": 2,
                "type": "returned",
                "worker_id": "a",
                "delegation_id": "a1",
                "at": at(6),
            },
        ]
        turns = [
            said("user", 1, "q"),
            said("agent", 3, "let me check"),
            said("agent", 7, "here it is"),
        ]
        assert (
            worker_steps(events, turns)["turns"][0]["response"]["text"] == "here it is"
        )

    def test_a_reply_said_at_the_next_ask_belongs_to_the_next_turn(self):
        events = [{"seq": 1, "type": "delegated", "worker_id": "a", "at": at(2)}]
        turns = [said("user", 1, "q"), said("agent", 5, "r"), said("user", 5, "next")]
        assert worker_steps(events, turns)["turns"][0]["response"] is None


def test_a_nested_hand_off_with_no_recorded_parent_stays_at_the_top_as_unknown():
    # The maker has hand-offs open, but the record does not say which one this was made in.
    events = [
        {
            "seq": 1,
            "type": "delegated",
            "worker_id": "lead",
            "delegation_id": "L",
            "at": at(1),
        },
        {
            "seq": 2,
            "type": "delegated",
            "worker_id": "lead",
            "delegation_id": "L2",
            "at": at(2),
        },
        {
            "seq": 3,
            "type": "delegated",
            "worker_id": "helper",
            "by": "lead",
            "delegation_id": "c",
            "at": at(3),
        },
    ]
    result = worker_steps(events)
    helper = next(s for s in result["steps"] if s["key"] == 3)
    assert (helper["parent"], helper["relationship"], helper["depth"]) == (
        None,
        "unknown",
        1,
    )


def test_a_step_the_harness_recorded_as_unknown_stays_unknown_with_nothing_open():
    events = [
        {
            "seq": 1,
            "type": "tool_call",
            "worker_id": "lead",
            "tool": "t",
            "relationship": "unknown",
            "at": at(1),
        },
    ]
    [step] = worker_steps(events)["steps"]
    assert (step["parent"], step["relationship"]) == (None, "unknown")


def test_a_step_recorded_at_the_top_stays_at_the_top_while_its_maker_has_a_hand_off_open():
    # ADK resumes a transferred agent as the top of the next turn: what it hands out is recorded at the top level.
    events = [
        {
            "seq": 1,
            "type": "delegated",
            "worker_id": "w/planner",
            "by": None,
            "delegation_id": "p1",
            "at": "t1",
        },
        {
            "seq": 2,
            "type": "delegated",
            "worker_id": "w/search",
            "by": "w/planner",
            "delegation_id": "s1",
            "relationship": "observed",
            "at": "t2",
        },
        {
            "seq": 3,
            "type": "tool_call",
            "worker_id": "w/planner",
            "tool": "note",
            "relationship": "observed",
            "at": "t3",
        },
    ]
    steps = worker_steps(events)["steps"]
    placed = {s["key"]: (s["parent"], s["relationship"]) for s in steps}
    assert placed[2] == (None, "observed")
    assert placed[3] == (None, "observed")


def test_a_step_sits_under_the_hand_off_its_recorded_id_names_whatever_its_time():
    hand_off = {**HOTELS, "seq": 1}
    back = {
        "seq": 3,
        "type": "returned",
        "worker_id": "searcher",
        "delegation_id": "A",
        "at": at(5),
    }
    for call_at in (None, at(6)):
        call = {
            "seq": 2,
            "type": "tool_call",
            "worker_id": "searcher",
            "delegation_id": "A",
            "tool": "search",
            "at": call_at,
        }
        events = [call, hand_off, back] if call_at is None else [hand_off, back, call]
        steps = worker_steps(events)["steps"]
        (step,) = [one for one in steps if one["kind"] == "tool_call"]
        assert (step["parent"], step["relationship"]) == (1, "observed")


def test_a_step_whose_recorded_hand_off_never_appears_is_unknown():
    call = {
        "seq": 1,
        "type": "tool_call",
        "worker_id": "searcher",
        "delegation_id": "missing",
        "tool": "search",
        "at": at(2),
    }
    (step,) = worker_steps([call])["steps"]
    assert (step["parent"], step["relationship"]) == (None, "unknown")


def test_a_hand_off_recorded_inside_one_that_opens_later_sits_under_it():
    from simulate.services.harness_worker_steps import worker_steps

    events = [
        {
            "seq": 1,
            "type": "delegated",
            "worker_id": "R/researcher",
            "by": "R/writer",
            "delegation_id": "d2",
            "parent_delegation_id": "d1",
            "at": "2026-11-09T10:00:03",
            "task": "find x",
        },
        {
            "seq": 2,
            "type": "tool_call",
            "worker_id": "R/researcher",
            "tool": "search",
            "delegation_id": "d2",
            "at": "2026-11-09T10:00:04",
        },
        {
            "seq": 3,
            "type": "returned",
            "worker_id": "R/researcher",
            "delegation_id": "d2",
            "status": "ok",
            "at": "2026-11-09T10:00:10",
        },
        {
            "seq": 4,
            "type": "delegated",
            "worker_id": "R/writer",
            "delegation_id": "d1",
            "relationship": "observed",
            "task": "draft",
        },
        {
            "seq": 5,
            "type": "returned",
            "worker_id": "R/writer",
            "delegation_id": "d1",
            "status": "ok",
            "at": "2026-11-09T10:00:20",
        },
    ]
    steps = {s["key"]: s for s in worker_steps(events)["steps"]}
    assert (steps[1]["parent"], steps[1]["relationship"]) == (4, "observed")
    assert steps[2]["parent"] == 1


def test_background_hand_offs_are_counted_as_asynchronous():
    from simulate.services.harness_worker_steps import worker_steps

    events = [
        {
            "seq": 0,
            "type": "delegated",
            "worker_id": "r/researcher",
            "delegation_id": "d1",
            "launch": "async",
            "at": "2026-11-15T10:00:01",
            "attribution": "reported",
        },
        {
            "seq": 1,
            "type": "delegated",
            "worker_id": "r/coder",
            "delegation_id": "d2",
            "at": "2026-11-15T10:00:02",
            "attribution": "reported",
        },
        {
            "seq": 2,
            "type": "returned",
            "worker_id": "r/coder",
            "delegation_id": "d2",
            "at": "2026-11-15T10:00:03",
            "attribution": "reported",
        },
    ]
    assert worker_steps(events)["asynchronous"] == 1


class TestWorkerRecordAsServed:
    def test_a_hand_off_that_never_returned_reads_no_return_unless_the_record_was_cut(
        self,
    ):
        events = [HOTELS, FLIGHTS, answer(3, "B", "two flights")]
        whole = handoffs(worker_steps(events)["steps"])
        cut = handoffs(worker_steps(events, cut_off=True)["steps"])
        assert [one["end"] for one in whole] == ["no_return", "finished"]
        # A record not kept whole can't say the missing return never came.
        assert [one["end"] for one in cut] == ["cut_off", "finished"]

    def test_a_return_with_an_error_reads_failed_and_a_control_pass_reads_passed(self):
        failed = {**answer(3, "A", "boom"), "status": "error"}
        passed = {**HOTELS, "type": "transferred"}
        assert [
            one["end"] for one in handoffs(worker_steps([HOTELS, failed])["steps"])
        ] == ["failed"]
        assert [one["end"] for one in handoffs(worker_steps([passed])["steps"])] == [
            "passed"
        ]
        broken = {**call(2, "A"), "status": "error"}
        assert [
            one["end"] for one in handoffs(worker_steps([passed, broken])["steps"])
        ] == ["failed"]

    def test_the_counts_add_up_to_the_hand_offs_shown(self):
        events = [HOTELS, FLIGHTS, answer(3, "B", "two flights")]
        result = worker_steps(events)
        counts = result["counts"]
        shown = handoffs(result["steps"])
        assert counts["hand_offs"] == len(shown)
        assert counts["finished"] + counts["did_not_finish"] == len(shown)
        assert counts["workers"] == len({one["worker_id"] for one in shown})

    def test_each_row_s_did_not_finish_agrees_with_the_headline_count(self):
        failed = {**answer(5, "A", "boom"), "status": "error"}
        passed = {**HOTELS, "seq": 3, "type": "transferred", "delegation_id": "C"}
        events = [HOTELS, FLIGHTS, passed, failed]
        for cut_off in (False, True):
            result = worker_steps(events, cut_off=cut_off)
            shown = handoffs(result["steps"])
            assert (
                sum(one["did_not_finish"] for one in shown)
                == result["counts"]["did_not_finish"]
            )
            assert {one["end"] for one in shown if one["did_not_finish"]} <= {
                "failed",
                "no_return",
            }

    def test_only_a_worker_s_first_hand_off_is_marked_first(self):
        other = {**FLIGHTS, "seq": 3, "worker_id": "booker", "delegation_id": "C"}
        shown = handoffs(worker_steps([HOTELS, FLIGHTS, other])["steps"])
        assert [(one["worker_id"], one["first_of_worker"]) for one in shown] == [
            ("searcher", True),
            ("searcher", False),
            ("booker", True),
        ]

    def test_each_turn_lists_its_hand_offs_nested_ones_included(self):
        nested = {
            **FLIGHTS,
            "seq": 2,
            "worker_id": "booker",
            "by": "searcher",
            "parent_delegation_id": "A",
        }
        result = worker_steps([HOTELS, nested], [said("user", 0, "book it")])
        [turn] = result["turns"]
        assert turn["handoffs"] == [one["key"] for one in result["steps"]]
        assert len(turn["handoffs"]) == 2

    def test_only_work_before_the_caller_spoke_or_in_no_turn_expects_no_messages(
        self,
    ):
        early = {**HOTELS, "at": at(1)}
        later = {**FLIGHTS, "at": at(5)}
        untimed = {**FLIGHTS, "seq": 3, "delegation_id": "C", "at": None}
        turns = worker_steps(
            [early, later, untimed], [said("agent", 2, "hi"), said("user", 4, "go")]
        )["turns"]
        assert [(one["turn"], one["expects_messages"]) for one in turns] == [
            (0, False),
            (1, True),
            (None, False),
        ]

    def test_each_hand_off_names_the_steps_directly_under_it(self):
        events = [HOTELS, call(2, "A"), answer(3, "A", "three hotels"), call(4)]
        result = worker_steps(events)
        [hotels] = handoffs(result["steps"])
        under = [
            one["key"] for one in result["steps"] if one["parent"] == hotels["key"]
        ]
        assert hotels["children"] == under and under
        top = [one["key"] for one in result["steps"] if one["parent"] is None]
        assert [key for turn in result["turns"] for key in turn["children"]] == top

    def test_a_turn_counts_the_tool_calls_made_outside_every_hand_off(self):
        events = [HOTELS, call(2, "A"), call(3), call(4)]
        turns = worker_steps(events, [said("user", 0, "book it")])["turns"]
        assert [one["calls_outside"] for one in turns] == [2]

    def test_work_two_levels_deep_or_in_parallel_is_beyond_the_boundary(self):
        assert not worker_steps([HOTELS])["beyond_boundary"]
        assert worker_steps([HOTELS, FLIGHTS])["beyond_boundary"]
        nested = {
            **FLIGHTS,
            "at": at(2),
            "by": "searcher",
            "parent_delegation_id": "A",
        }
        assert worker_steps([HOTELS, nested])["beyond_boundary"]

    def test_an_answer_that_is_data_is_marked_so_and_prose_is_not(self):
        events = [
            HOTELS,
            answer(3, "A", '{"hotels": 3}'),
            FLIGHTS,
            answer(4, "B", "[sorry] none left"),
        ]
        shown = [
            one["returned"]["result_shown"]
            for one in handoffs(worker_steps(events)["steps"])
        ]
        assert shown[0]["format"] == "code" and '"hotels": 3' in shown[0]["text"]
        assert shown[1] == {"format": "text", "text": "[sorry] none left"}

    def test_a_record_kept_before_times_carried_a_zone_still_serves(self):
        old = {
            "workers": [],
            "events": [HOTELS, answer(3, "A", "three hotels")],
            "events_omitted": 0,
            "coordinator": None,
            "spans_received": True,
            "started_at": at(0),
            "collected_at": "2026-11-01T10:01:00+00:00",
        }
        now = datetime(2026, 11, 1, 11, tzinfo=UTC)
        served = served_record(old, now)
        stamps = [served["started_at"], *(one["at"] for one in served["events"])]
        assert all(datetime.fromisoformat(one).tzinfo is not None for one in stamps)
        assert [one["end"] for one in handoffs(served["steps"])] == ["finished"]
        # No later read was scheduled for it, so nothing is pending.
        assert served["pending"] is False

    def test_a_record_with_a_later_read_due_is_pending_until_that_read_has_run(self):
        record = {
            "workers": [],
            "events": [HOTELS],
            "events_omitted": 0,
            "coordinator": None,
            "spans_received": True,
            "started_at": None,
            "next_read_at": "2026-11-01T10:10:00+00:00",
        }
        before = datetime(2026, 11, 1, 10, 5, tzinfo=UTC)
        long_after = datetime(2026, 11, 2, tzinfo=UTC)
        assert served_record(record, before)["pending"] is True
        assert served_record(record, long_after)["pending"] is False

    def test_only_a_hand_off_that_never_came_back_takes_the_worker_s_end(self):
        def worker(state):
            return {
                "worker_id": "searcher",
                "id": "searcher",
                "name": "S",
                "state": state,
            }

        record = {
            "workers": [worker("cancelled")],
            "events": [HOTELS, answer(3, "A", "three"), FLIGHTS],
            "events_omitted": 0,
            "coordinator": None,
            "spans_received": True,
            "started_at": None,
        }
        now = datetime(2026, 11, 2, tzinfo=UTC)
        served = served_record(record, now)
        assert [one.get("end_state") for one in handoffs(served["steps"])] == [
            None,
            "cancelled",
        ]
        assert served["workers"][0]["cut_short"] is True
        assert served["workers"][0]["past_deadline"] is False
        # Past its deadline the world refused its later calls; it was not stopped, so it is not cut short.
        late = served_record({**record, "workers": [worker("deadline_passed")]}, now)
        assert late["workers"][0]["cut_short"] is False
        assert late["workers"][0]["past_deadline"] is True
        assert handoffs(late["steps"])[1]["end_state"] == "deadline_passed"

    def test_a_pending_record_says_when_to_read_again_and_never_a_time_already_past(
        self,
    ):
        record = {
            "workers": [],
            "events": [HOTELS],
            "events_omitted": 0,
            "coordinator": None,
            "spans_received": True,
            "started_at": None,
            "next_read_at": "2026-11-01T10:10:00+00:00",
        }
        before = datetime(2026, 11, 1, 10, 5, tzinfo=UTC)
        # Due, but the read may still be queued: the client is told to look again soon, once.
        queued = datetime(2026, 11, 1, 10, 11, tzinfo=UTC)
        assert served_record(record, before)["next_read_at"] == record["next_read_at"]
        again = datetime.fromisoformat(served_record(record, queued)["next_read_at"])
        assert again > queued


class TestFailurePlacements:
    SEARCHER = {"worker_id": "r/searcher", "id": "searcher", "name": "S"}

    def steps(self, *events):
        return worker_steps([{**one, "worker_id": "r/searcher"} for one in events])[
            "steps"
        ]

    def own_check(self):
        return {
            "name": "read_only",
            "passed": False,
            "subject_kind": "worker",
            "worker_id": "searcher",
        }

    def test_a_task_check_is_never_tied_to_a_step_by_its_name(self):
        # Named after a tool that failed in the call: still not tied, since nothing recorded says it is about it.
        named, judged = failure_placements(
            [
                {"name": "call_book", "passed": False, "subject_kind": "task"},
                {
                    "name": "explained",
                    "passed": None,
                    "result": "inconclusive",
                    "subject_kind": "task",
                },
            ],
            self.steps(HOTELS),
            [self.SEARCHER],
        )
        assert (named["step"], named["untied"], named["worker_id"]) == (
            None,
            "no_moment",
            None,
        )
        assert (judged["step"], judged["untied"], judged["status"]) == (
            None,
            "no_moment",
            "inconclusive",
        )

    def test_a_passed_or_not_applicable_check_is_not_placed(self):
        assert not failure_placements(
            [
                {"name": "call_book", "passed": True, "subject_kind": "task"},
                {
                    "name": "x",
                    "passed": None,
                    "result": "not_applicable",
                    "subject_kind": "task",
                },
            ],
            self.steps(HOTELS),
            [self.SEARCHER],
        )

    def test_a_worker_s_failed_check_sits_on_its_one_hand_off(self):
        steps = self.steps(HOTELS, answer(2, "A", "two"))
        [placed] = failure_placements([self.own_check()], steps, [self.SEARCHER])
        [hand_off] = handoffs(steps)
        assert (placed["worker_id"], placed["step"], placed["untied"]) == (
            "r/searcher",
            hand_off["key"],
            None,
        )

    def test_a_worker_s_check_with_several_hand_offs_or_none_is_on_no_step(self):
        several = self.steps(HOTELS, FLIGHTS)
        [placed] = failure_placements([self.own_check()], several, [self.SEARCHER])
        assert (placed["step"], placed["untied"]) == (None, "several_hand_offs")
        [unseen] = failure_placements([self.own_check()], [], [self.SEARCHER])
        assert (unseen["step"], unseen["untied"]) == (None, "no_hand_off")


def test_a_failed_worker_check_is_served_failed_on_its_worker_beside_a_passing_task_check():
    record = {
        "workers": [
            {"worker_id": "r/order_lookup", "id": "order_lookup", "name": "Lookup"},
            {"worker_id": "r/refund", "id": "refund", "name": "Refund"},
        ],
        "events": [],
        "events_omitted": 0,
        "coordinator": None,
        "spans_received": True,
        "started_at": None,
    }
    served = served_record(
        record,
        datetime(2026, 11, 2, tzinfo=UTC),
        [
            {"name": "refund_issued", "passed": True, "subject_kind": "task"},
            {
                "name": "read_only",
                "passed": False,
                "subject_kind": "worker",
                "worker_id": "order_lookup",
            },
        ],
    )
    lookup, refund = served["workers"]
    # The call's own check passed; the worker's failure is still served, failed and named by its worker.
    assert lookup["checks"] == [{"name": "read_only", "passed": False}]
    assert refund["checks"] == []
    [placed] = served["placements"]
    # Named by the record's worker id, the one its steps and workers carry.
    assert (placed["name"], placed["status"], placed["worker_id"]) == (
        "read_only",
        "failed",
        "r/order_lookup",
    )
