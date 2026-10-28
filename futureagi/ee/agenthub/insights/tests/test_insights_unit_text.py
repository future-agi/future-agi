"""What the built-in engine reads and how what it finds is checked, on
sessions shaped like the real-user seed (``seeded_support``).

The unit text shows the agent's tool calls with their results and failures in
turn order, marks media it can't see and keeps a failure late in a long
session when clipping. A quote is kept only when it occurs in that text, a
failure stands only on a quote that shows it, and a claim that the agent made
something up stands only on the tool result that contradicts it."""

import json
import re

import pytest

from ee.agenthub.insights import billing, facets, llm, summary, units
from ee.agenthub.insights.facets import backed, included, normalise
from ee.agenthub.insights.tests import seeded_support as seed


@pytest.fixture
def load(monkeypatch):
    """Load units the engine's way from the seeded sessions."""

    def load(sessions, unit="sessions", ids=None, read_steps=True, **reader_options):
        reader = seed.FakeReader(sessions, **reader_options)
        monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
        monkeypatch.setattr(units, "_user_labels", lambda ids: {})
        loaded = units.load_units(
            unit, "project", ids or list(sessions), read_steps=read_steps
        )
        return loaded, reader

    return load


def seeded(*names):
    return {name: seed.SESSIONS[name][1] for name in names}


def reading(content, raw, key="7"):
    return backed(normalise(raw, []), raw, content, key)


class TestUnitText:
    def test_a_turn_shows_the_agents_tool_calls_and_their_results_in_order(self, load):
        out, _ = load(seeded("sess_refund_failed"))
        text = out["sess_refund_failed"].content
        order = [
            "[Turn 1] User: Order HB-48213 shows two charges",
            'Tool lookup_order {"order_id": "HB-48213"} -> {"order_id"',
            'Tool issue_refund {"order_id": "HB-48213", "amount": 61.2} -> ERROR '
            'refund_declined: {"error": "refund_declined", "message": "Charge is '
            'still pending settlement; refunds open after settlement."}',
            "Assistant: Done. I've refunded $61.20",
            "[Turn 2] User: Great, so I don't need to do anything else?",
        ]
        at = [text.index(line) for line in order]
        assert at == sorted(at)

    def test_an_image_it_cant_see_is_marked_once_and_never_inlined(self, load):
        out, _ = load(seeded("sess_screenshot_read", "sess_screenshot_ignored"))
        read = out["sess_screenshot_read"].content
        assert read.startswith(
            "[Turn 1] User: My payment keeps failing at checkout, here's a "
            f"screenshot. {units.IMAGE_NOTE}\nAssistant: The screenshot shows"
        )
        # The chat input of turn 2 repeats no image: the note isn't repeated.
        assert out["sess_screenshot_ignored"].content.count(units.IMAGE_NOTE) == 1
        for unit in out.values():
            assert "base64" not in unit.content and "iVBOR" not in unit.content
            assert units.IMAGE_NOTE not in unit.excerpt + unit.label

    def test_content_parts_read_as_text_with_media_as_notes(self):
        chat = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Here is the error"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AAAA"},
                            },
                            {"type": "input_audio", "input_audio": {"data": "UklG"}},
                        ],
                    }
                ]
            }
        )
        assert units._readable(chat) == (
            f"Here is the error {units.IMAGE_NOTE} {units.AUDIO_NOTE}"
        )
        assert units._readable("see data:image/jpeg;base64,/9j/4AAQSk=") == (
            f"see {units.IMAGE_NOTE}"
        )

    def test_a_long_session_keeps_the_failed_turn_with_its_tool_result(self, load):
        # Wordier turns than the seed's: 30 turns run far over budget.
        out, _ = load({"wordy": seed.long_session(padding_sentences=30)})
        text = out["wordy"].content
        assert len(text) <= units.SESSION_CHARS
        assert (
            "[Turn 23] User: So will the export finish by tonight?\n"
            'Tool check_export_job {"job": "exp_77"} -> ERROR quota_exceeded: '
            '{"status": "failed", "error": "quota_exceeded", "retry_after": "24h"}\n'
            "Assistant: Yes, the export is running and will finish by tonight."
        ) in text
        turns = [int(n) for n in re.findall(r"\[Turn (\d+)\]", text)]
        # The first, the failed one, the second and the latest that fit.
        assert turns[:3] == [1, 2, 23] and turns[-1] == 30
        assert 22 not in turns and 24 not in turns
        assert text.count("turns omitted …]") == 2

    def test_the_seeds_long_session_is_read_whole_with_its_big_result_cut(self, load):
        out, _ = load(seeded("sess_long"))
        text = out["sess_long"].content
        assert re.findall(r"\[Turn (\d+)\]", text) == [str(n) for n in range(1, 31)]
        logs = next(line for line in text.splitlines() if "fetch_logs" in line)
        assert len(logs) < 2 * units.STEP_CHARS and logs.endswith("…")
        assert '"error": "quota_exceeded"' in text

    def test_steps_that_could_not_be_read_are_said_not_hidden(self, load):
        out, _ = load(seeded("sess_refund_failed"), steps_unreadable=True)
        text = out["sess_refund_failed"].content
        assert text.startswith(units.UNREAD_NOTE + "\n\n[Turn 1] User: Order")
        assert "Tool " not in text

    def test_a_v2_run_does_not_read_the_steps_it_reads_itself(self, load):
        out, reader = load(seeded("sess_refund_failed"), read_steps=False)
        assert reader.asked == []
        assert out["sess_refund_failed"].content.startswith("[Turn 1] User: Order")
        assert "Tool " not in out["sess_refund_failed"].content

    def test_a_trace_and_a_user_show_their_tool_calls_too(self, load):
        out, _ = load(
            seeded("sess_refund_failed"), "traces", ids=["sess_refund_failed-t1"]
        )
        assert "-> ERROR refund_declined" in out["sess_refund_failed-t1"].content
        out, reader = load(
            seeded("sess_refund_failed", "sess_long"), "users", ["cust_001"]
        )
        text = out["cust_001"].content
        assert (
            '  Tool issue_refund {"order_id": "HB-48213", "amount": 61.2} -> ERROR'
            in text
        )
        # Only the turns a user's text shows are read for their steps.
        assert len(reader.asked[0]) == 2 + units.USER_TURNS_PER_SESSION

    def test_a_turn_with_many_calls_shows_the_failed_ones_and_the_ends(self):
        steps = [
            {
                "observation_type": "tool",
                "name": f"call_{i}",
                "status": "ERROR" if i == 6 else "OK",
                "status_message": "boom" if i == 6 else "",
                "input": "{}",
                "output": "{}",
            }
            for i in range(12)
        ]
        # Room for four lines: the failed call, then the ends.
        room = sum(len(units._step_line(steps[i], 50)) for i in (0, 1, 6, 11))
        lines = units._step_lines(steps, chars=50, budget=room, total=14)
        names = [re.search(r"call_\d+", x).group(0) for x in lines if "call_" in x]
        assert names == ["call_0", "call_1", "call_6", "call_11"]
        # Two calls past the twelve read are counted as omitted too.
        assert lines[-1] == "[… 2 steps omitted …]"
        assert "[… 4 steps omitted …]" in lines


class TestQuotes:
    @pytest.fixture
    def text(self, load):
        out, _ = load(
            seeded(
                "sess_refund_failed",
                "sess_parcel_found",
                "sess_screenshot_read",
                "sess_cancel_ignored",
            )
        )
        return {k: u.content for k, u in out.items()}

    def test_a_quote_is_kept_only_when_it_is_in_the_text(self, text):
        content = text["sess_refund_failed"]
        facets_ = reading(
            content,
            {
                "lens_answer": "Agent said a refund went through after the refund "
                "tool declined it",
                "failure_present": True,
                "failure_basis": "contradicted",
                "evidence": [
                    {"quote": "Done. I've refunded $61.20", "supports": "failure"},
                    {"quote": '"error": "refund_declined"', "supports": "failure"},
                    {"quote": "The refund was processed successfully"},
                    {"quote": units.IMAGE_NOTE},
                ],
            },
        )
        quotes = [e["quote"] for e in facets_["evidence"]]
        assert quotes == ["Done. I've refunded $61.20", '"error": "refund_declined"']
        for e in facets_["evidence"]:
            assert content[e["start"] : e["end"]] == e["quote"]
            assert e["evidence_id"] == f"7:{e['start']}:{len(e['quote'])}"
        assert facets_["dropped_quotes"] == 2
        assert facets_["failure_present"] and facets_["failure_basis"] == "contradicted"

    def test_a_failure_without_a_quote_that_shows_it_is_not_counted(self, text):
        for evidence in (
            [],
            [
                {
                    "quote": "I've refunded $61.20 into your account",
                    "supports": "failure",
                }
            ],
            [{"quote": "Done. I've refunded $61.20", "supports": "summary"}],
        ):
            facets_ = reading(
                text["sess_refund_failed"],
                {
                    "lens_answer": "Agent said the refund was done",
                    "failure_present": True,
                    "failure_mode": "false refund claim",
                    "broke_at_turn": 1,
                    "evidence": evidence,
                },
            )
            assert not facets_["failure_present"]
            assert facets_["unbacked_failure"] == "no_quote"
            assert facets_["failure_mode"] == "none"
            assert facets_["broke_at_turn"] is None
            assert not included(facets_, "failures", [])

    def test_no_accusation_of_making_something_up_without_a_contradiction(self, text):
        # What the built-in engine said of two correct agents on the seed.
        parcel = reading(
            text["sess_parcel_found"],
            {
                "lens_answer": "Agent states the parcel is out for delivery by 6 pm "
                "with no lookup or tracking evidence shown",
                "failure_present": True,
                "failure_basis": "shown",
                "evidence": [
                    {"quote": "should arrive by 6 pm today", "supports": "failure"}
                ],
            },
        )
        assert not parcel["failure_present"]
        assert parcel["unbacked_failure"] == "no_contradiction"
        screenshot = {
            "lens_answer": "Agent stated a decline code as if it had read the "
            "screenshot",
            "failure_present": True,
            "evidence": [
                {"quote": "declined with code 51", "supports": "failure"},
                {"quote": "here's a screenshot.", "supports": "failure"},
            ],
        }
        for basis, reason in (
            ("shown", "no_contradiction"),
            ("unsupported", "unsupported"),
        ):
            out = reading(
                text["sess_screenshot_read"], {**screenshot, "failure_basis": basis}
            )
            assert not out["failure_present"] and out["unbacked_failure"] == reason
        # The same kind of claim stands on the tool result that contradicts it.
        refund = reading(
            text["sess_refund_failed"],
            {
                "lens_answer": "Agent claimed the refund was complete without "
                "checking it went through",
                "failure_present": True,
                "failure_basis": "shown",
                "evidence": [
                    {"quote": "the refund is complete", "supports": "failure"},
                    {
                        "quote": "-> ERROR refund_declined",
                        "supports": "failure",
                    },
                ],
            },
        )
        assert refund["failure_present"] and "unbacked_failure" not in refund

    @pytest.mark.parametrize(
        "claim, made_up",
        [
            ("Agent made up a delivery time", True),
            ("Agent invents a refund policy", True),
            ("Decline code stated as if a screenshot had been read", True),
            ("ETA given with no lookup or tracking evidence shown", True),
            ("Promised the export would finish without supporting evidence", True),
            ("Unverified factual claims stated as certain", True),
            ("Agent misreports inventory levels when asked about sizes", False),
            ("Agent treats a returning customer as if new and re-asks details", False),
            ("Agent ignores a request to cancel", False),
        ],
    )
    def test_what_reads_as_a_claim_that_the_agent_made_something_up(
        self, claim, made_up
    ):
        assert bool(facets._MADE_UP.search(claim)) is made_up

    def test_a_mistake_shown_in_the_conversation_stands_on_its_quotes(self, text):
        out = reading(
            text["sess_cancel_ignored"],
            {
                "lens_answer": "Agent pushes a retention offer when the user asks "
                "to cancel",
                "failure_present": True,
                "failure_basis": "shown",
                "evidence": [
                    {
                        "quote": "No thanks, please just cancel it.",
                        "supports": "failure",
                    },
                    {"quote": "Shall I apply it?", "supports": "failure"},
                ],
            },
        )
        assert out["failure_present"] and out["failure_basis"] == "shown"
        assert included(out, "failures", [])

    def test_a_quote_is_published_as_the_sessions_words_never_the_engines(self, text):
        """On the real run two tool quotes read 'ERROR quota_exceeded: {…}':
        the JSON was the tool's, the "ERROR <message>: " the engine's. Each
        published quote is now text of one of the session's spans (an input,
        an output or a status message), as the tester checked them."""
        from ee.agenthub.insights.agent import verified_evidence

        content = text["sess_refund_failed"]
        roots, steps = seed.spans(
            "sess_refund_failed", seed.SESSIONS["sess_refund_failed"][1]
        )
        fields = [r.input for r in roots] + [r.output for r in roots]
        fields += [s[k] for s in steps for k in ("input", "output", "status_message")]
        declined = (
            '{"error": "refund_declined", "message": "Charge is still pending '
            'settlement; refunds open after settlement."}'
        )
        for quote, published in (
            (f"ERROR refund_declined: {declined}", declined),
            (f"-> ERROR refund_declined: {declined[:27]}", declined[:27]),
            (
                'Tool issue_refund {"order_id": "HB-48213", "amount": 61.2} -> '
                f"ERROR refund_declined: {declined}",
                declined,
            ),
            ("-> ERROR refund_declined", "refund_declined"),
            ('Tool lookup_order {"order_id": "HB-48213"}', '{"order_id": "HB-48213"}'),
            ("Assistant: Done. I've refunded $61.20", "Done. I've refunded $61.20"),
            (
                "[Turn 1] User: Order HB-48213 shows two charges",
                "Order HB-48213 shows two charges",
            ),
            # Joins two turns, holds none of the session's words, or isn't there.
            ("within a week. [Turn 2] User: Great, so I don't", None),
            ("Tool issue_refund", None),
            ("-> ERROR", None),
            ("ERROR refund_declined: the refund was declined", None),
        ):
            kept, dropped = verified_evidence([{"quote": quote}], content, "7")
            if published is None:
                assert (kept, dropped) == ([], 1), quote
                continue
            assert [e["quote"] for e in kept] == [published], quote
            (e,) = kept
            assert content[e["start"] : e["end"]] == published
            assert e["evidence_id"] == f"7:{e['start']}:{len(published)}"
            assert any(published in field for field in fields), quote
        # The tool's own result still backs the failure it contradicts.
        out = reading(
            content,
            {
                "lens_answer": "Agent said a refund went through after the refund "
                "tool declined it",
                "failure_present": True,
                "failure_basis": "contradicted",
                "evidence": [
                    {"quote": "Done. I've refunded $61.20", "supports": "failure"},
                    {
                        "quote": f"ERROR refund_declined: {declined}",
                        "supports": "failure",
                    },
                ],
            },
        )
        assert out["failure_present"] and "unbacked_failure" not in out
        assert [e["quote"] for e in out["evidence"]] == [
            "Done. I've refunded $61.20",
            declined,
        ]

    def test_a_name_or_message_that_could_run_on_reads_as_a_string(self):
        """A name with a space, or a message with ": ", would leave a line
        unclear about where the engine's words start again: it is shown as a
        JSON string, and a quote through it keeps only the session's words."""
        from ee.agenthub.insights.agent import verified_evidence

        step = {
            "observation_type": "tool",
            "name": "execute_tool issue_refund",
            "status": "ERROR",
            "status_message": "ValueError: refund declined",
            "input": '{"amount": 61.2}',
            "output": '{"status": "declined"}',
        }
        line = units._step_line(step, units.STEP_CHARS)
        assert line == (
            'Tool "execute_tool issue_refund" {"amount": 61.2} -> '
            'ERROR "ValueError: refund declined": {"status": "declined"}'
        )
        plain = units._step_line({**step, "name": "issue_refund"}, units.STEP_CHARS)
        assert plain.startswith('Tool issue_refund {"amount"')
        content = f"[Turn 1] User: Refund me\n{line}\nAssistant: Done."
        for quote, published in (
            (
                'ERROR "ValueError: refund declined": {"status": "declined"}',
                '{"status": "declined"}',
            ),
            ('"ValueError: refund declined"', "ValueError: refund declined"),
            ('refund declined": {"status"', '{"status"'),
            ('issue_refund" {"amount": 61.2}', '{"amount": 61.2}'),
        ):
            kept, _ = verified_evidence([{"quote": quote}], content, "7")
            assert [e["quote"] for e in kept] == [published], quote
        # An escaped message isn't the span's text as written.
        escaped = units._step_line(
            {**step, "status_message": '"x" declined', "output": ""}, units.STEP_CHARS
        )
        assert escaped.endswith('-> ERROR "\\"x\\" declined"')
        kept, dropped = verified_evidence(
            [{"quote": '\\"x\\" declined'}], f"Assistant: ok\n{escaped}", "7"
        )
        assert (kept, dropped) == ([], 1)


# How a fast model read each seeded session: right on the failures, and the
# two accusations the built-in engine made of correct agents on the real run.
REPLIES = {
    "sess_refund_ok": {"lens_answer": "none", "failure_present": False},
    "sess_refund_failed": {
        "lens_answer": "Agent told the user a refund was done when the refund "
        "tool had declined it",
        "failure_present": True,
        "failure_basis": "contradicted",
        "evidence": [
            {"quote": "Done. I've refunded $61.20", "supports": "failure"},
            {"quote": '"error": "refund_declined"', "supports": "failure"},
        ],
    },
    "sess_parcel_found": {
        "lens_answer": "Agent gave a delivery time with no lookup shown",
        "failure_present": True,
        "failure_basis": "shown",
        "evidence": [{"quote": "should arrive by 6 pm today", "supports": "failure"}],
    },
    "sess_screenshot_read": {
        "lens_answer": "Agent stated a decline code as if a screenshot had been read",
        "failure_present": True,
        "failure_basis": "unsupported",
        "evidence": [{"quote": "declined with code 51", "supports": "failure"}],
    },
    "sess_screenshot_ignored": {
        "lens_answer": "Agent repeats cache advice when the user points at the "
        "screenshot's error",
        "failure_present": True,
        "failure_basis": "shown",
        "evidence": [
            {
                "quote": "Did you look at the error in the screenshot?",
                "supports": "failure",
            },
            {
                "quote": "Please try clearing your cache once more",
                "supports": "failure",
            },
        ],
    },
    "sess_cancel_ignored": {
        "lens_answer": "Agent pushes a discount when the user asks to cancel",
        "failure_present": True,
        "failure_basis": "shown",
        "evidence": [
            {"quote": "No thanks, please just cancel it.", "supports": "failure"},
            {"quote": "Would you like a month at half price instead?"},
        ],
    },
    "sess_long": {
        "lens_answer": "Agent promised the export would finish tonight after the "
        "export job tool reported it failed",
        "failure_present": True,
        "failure_basis": "contradicted",
        "broke_at_turn": 23,
        "evidence": [
            {
                "quote": "Yes, the export is running and will finish by tonight.",
                "supports": "failure",
            },
            {
                "quote": '"status": "failed", "error": "quota_exceeded"',
                "supports": "failure",
            },
            {"quote": "The export will be done tonight", "supports": "failure"},
        ],
    },
}


class TestExtract:
    def test_the_seeded_sessions_read_end_to_end_with_a_fake_model(
        self, load, monkeypatch
    ):
        out, _ = load({k: v[1] for k, v in seed.SESSIONS.items()})
        asked = []

        def fake(system, prompt, *, tier, max_tokens, **_):
            asked.append((system, prompt))
            assert tier == llm.FAST
            content = prompt.split("<content>\n", 1)[1].split("\n</content>", 1)[0]
            session = next(k for k, u in out.items() if u.content == content)
            return {"summary": "s", "outcome": "unclear", **REPLIES[session]}

        monkeypatch.setattr(facets, "chat_json", fake)
        found = {}
        for session, unit in out.items():
            found[session] = facets.extract(
                "sessions",
                unit.content,
                lens_key="failures",
                question="",
                agent_description="",
                instructions="",
                attributes=[],
                key=session,
            )
        system, prompt = asked[0]
        assert "A 'Tool' line is a call the agent made" in system
        assert "Never say the agent made something up" in system
        assert '"evidence": up to 3' in prompt and '"failure_basis"' in prompt
        failures = {k for k, f in found.items() if included(f, "failures", [])}
        truth = {k for k, (_, _, failed) in seed.SESSIONS.items() if failed}
        assert failures == truth
        for session in failures:
            evidence = found[session]["evidence"]
            assert evidence, session
            for e in evidence:
                assert e["quote"] in out[session].content
        assert found["sess_long"]["dropped_quotes"] == 1
        assert found["sess_long"]["broke_at_turn"] == 23

    def test_a_units_prompt_stays_within_the_read_estimate(self, load):
        """``billing.READ_TOKENS`` is the upper end of what reading one unit
        sends: a session clipped to its budget, with the prompt around it."""
        out, _ = load({k: v[1] for k, v in seed.SESSIONS.items()})
        attributes = [
            {"key": f"attribute_{i}", "type": "boolean", "description": "d" * 60}
            for i in range(4)
        ]

        def prompt_tokens(content):
            text = facets._system("sessions", "a" * 400) + facets.build_prompt(
                "sessions",
                content,
                lens_key="custom",
                question="q" * 300,
                instructions="i" * 600,
                attributes=attributes,
            )
            return len(text) / billing.CHARS_PER_TOKEN

        largest = max(prompt_tokens(u.content) for u in out.values())
        clipped = prompt_tokens("x" * (units.SESSION_CHARS + 120))
        assert largest < clipped <= billing.READ_TOKENS[0]


class TestReport:
    def test_built_in_findings_and_highlights_carry_their_examples_quotes(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        from ee.agenthub.insights import agent, report

        def unit(key, evidence):
            return {
                "key": key,
                "unit_id": f"sess-{key}",
                "summary": f"summary {key}",
                "facets": {"sentiment": 2, "evidence": evidence},
                "category_id": "top",
                "sub_category_id": "sub",
            }

        refund = unit(
            "1",
            [
                {
                    "quote": "Order HB-1 shows",
                    "supports": "summary",
                    "evidence_id": "1:0:16",
                },
                {
                    "quote": "Done. I've refunded",
                    "supports": "failure",
                    "evidence_id": "1:40:19",
                },
            ],
        )
        quiet = unit("2", [])
        prompts = []

        def fake(system, prompt, **_):
            prompts.append(prompt)
            return {
                "title": "T",
                "findings": [
                    {
                        "headline": "Refunds claimed after a decline",
                        "detail": "See #1 and #2.",
                    }
                ],
                "highlights": [{"ref": 1, "title": "Claimed refund"}],
            }

        monkeypatch.setattr(summary, "chat_json", fake)
        monkeypatch.setattr(agent, "enabled", lambda: False)
        category = {
            "id": "sub",
            "level": 2,
            "name": "Refund claims",
            "description": "",
            "parent_id": "top",
            "stats": {"count": 2, "share": 1.0, "users": 1},
            "notable": [],
        }
        result = {
            "totals": {"count": 2, "users": 1},
            "categories": [
                {**category, "id": "top", "level": 1, "parent_id": None},
                category,
            ],
        }
        run = SimpleNamespace(settings={"question": "q"}, unit="sessions")
        out = report.write(run, result, [refund, quiet], {}, llm.Spend())
        assert 'Quote: "Done. I\'ve refunded"' in prompts[0]
        assert out["findings"][0]["evidence"] == [
            {
                "unit_id": "sess-1",
                "quote": "Done. I've refunded",
                "evidence_id": "1:40:19",
            }
        ]
        assert out["highlights"][0]["quote"] == "Done. I've refunded"
        assert out["verification"] == {"numbers": True, "quotes": True, "audit": False}
