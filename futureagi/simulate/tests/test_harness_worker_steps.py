from simulate.services.harness_worker_steps import worker_steps


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
                {"seq": 1, "type": "delegated", "worker_id": "lead", "at": at(1)},
                {
                    "seq": 2,
                    "type": "delegated",
                    "worker_id": "helper",
                    "by": "lead",
                    "at": at(2),
                },
                {
                    "seq": 3,
                    "type": "tool_call",
                    "worker_id": "helper",
                    "tool": "t",
                    "at": at(3),
                },
                {"seq": 4, "type": "returned", "worker_id": "helper", "at": at(4)},
                {"seq": 5, "type": "returned", "worker_id": "lead", "at": at(5)},
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
                {"seq": 1, "type": "delegated", "worker_id": "a", "at": at(1)},
                {"seq": 2, "type": "transferred", "worker_id": "b", "at": at(2)},
                {"seq": 3, "type": "returned", "worker_id": "a", "at": at(4)},
                {"seq": 4, "type": "returned", "worker_id": "b", "at": at(5)},
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

    def test_files_a_tool_call_by_its_delegation_id_and_by_the_latest_open_without_one(
        self,
    ):
        result = worker_steps([HOTELS, FLIGHTS, call(3, "A"), call(4)])
        assert [
            (s["key"], s["parent"]) for s in result["steps"] if s["kind"] == "tool_call"
        ] == [(3, 1), (4, 2)]

    def test_takes_a_missing_start_from_the_first_step_and_says_when_overlap_cannot_be_known(
        self,
    ):
        known = worker_steps(
            [
                {"seq": 1, "type": "delegated", "worker_id": "a", "at": at(1)},
                {"seq": 2, "type": "returned", "worker_id": "a", "at": at(5)},
                {"seq": 3, "type": "delegated", "worker_id": "b", "at": None},
                {
                    "seq": 4,
                    "type": "tool_call",
                    "worker_id": "b",
                    "tool": "t",
                    "at": at(3),
                },
                {"seq": 5, "type": "returned", "worker_id": "b", "at": at(6)},
            ]
        )
        assert (known["parallel"], known["overlap_unknown"]) == (2, False)
        assert handoffs(known["steps"])[1]["started_at"] == "2026-11-01T10:00:03+00:00"

        unknown = worker_steps(
            [
                {"seq": 1, "type": "delegated", "worker_id": "a", "at": at(1)},
                {"seq": 2, "type": "returned", "worker_id": "a", "at": at(5)},
                {"seq": 3, "type": "delegated", "worker_id": "b", "at": None},
                {"seq": 4, "type": "returned", "worker_id": "b", "at": at(6)},
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

    def test_falls_back_to_the_latest_open_hand_off_for_a_return_with_no_delegation_id(
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
            (s["returned"] or {}).get("seq") for s in handoffs(result["steps"])
        ] == [None, 3]

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
        assert worker_steps([]) == {
            "steps": [],
            "depth": 0,
            "parallel": 0,
            "overlap_unknown": False,
            "worker_depths": {},
        }

    def test_a_return_closes_its_hand_off_so_the_next_one_goes_to_the_one_still_open(
        self,
    ):
        result = worker_steps(
            [
                {"seq": 1, "type": "delegated", "worker_id": "a", "at": at(1)},
                {"seq": 2, "type": "delegated", "worker_id": "a", "at": at(2)},
                {"seq": 3, "type": "returned", "worker_id": "a", "at": at(3)},
                {"seq": 4, "type": "returned", "worker_id": "a", "at": at(4)},
            ]
        )
        assert [
            (s["key"], s["returned"]["seq"]) for s in handoffs(result["steps"])
        ] == [(1, 4), (2, 3)]

    def test_a_hand_off_with_no_start_starts_at_its_first_nested_hand_off(self):
        result = worker_steps(
            [
                {"seq": 1, "type": "delegated", "worker_id": "b", "at": None},
                {
                    "seq": 2,
                    "type": "delegated",
                    "worker_id": "c",
                    "by": "b",
                    "at": at(3),
                },
            ]
        )
        outer, inner = result["steps"]
        assert (inner["parent"], outer["started_at"]) == (
            1,
            "2026-11-01T10:00:03+00:00",
        )
