"""The model receives the whole span tree; nothing pre-selects content for it.

The payload used to pick one span's output as "AGENT RESPONSE", pick a task,
and cap the prompt at 40 spans. Audited against raw spans, 8 of 18 confirmed
false positives were the model correctly judging text that selection had
mislabelled as the agent's answer — session-wrapper roots whose output is the
call transcript while the real work (finalization audits) sat in child spans
past the cap. On the audited trace the audit verdicts sat at child index ~57
of 90: the cap guaranteed the model could never see the evidence that would
have exonerated the agent.

Fixtures reproduce that real shape: a call-platform root carrying the
transcript log, a finalization pipeline of Summary/Adherence audit children,
and enough padding spans that the old 40-span cap would have hidden the tail.
"""

from ee.agenthub.trace_scanner.compress import (
    build_trace_payload,
    structural_prefilter_with_ids,
)
from ee.agenthub.trace_scanner.prompt import (
    PROMPT_RUNAWAY_BUDGET,
    build_prompt_v8,
)

TRANSCRIPT = "Agent: Hola\nAgent: Buenas tardes, ¿hablo con el titular de la cuenta?\nAgent: [Fin de llamada]"
AUDIT_JSON = (
    '{"violations": [], "notes": "No verbalización de herramientas detectada", '
    '"violation_exist": false}'
)
SUMMARY_RULE = "Responde SOLO con el enunciado. Sin JSON, sin markdown, sin comillas."
SUMMARY_OUT = "No se alcanzó compromiso ni transferencia, la llamada finalizó por corte."


def _span(sid, name, inp="", out="", kind_key="span.kind", kind=""):
    attrs = {}
    if inp:
        attrs["input.value"] = inp
    if out:
        attrs["output.value"] = out
    if kind:
        attrs[kind_key] = kind
    return {
        "span_id": sid,
        "span_name": name,
        "status_code": "Unset",
        "span_attributes": attrs,
        "child_spans": [],
    }


def _call_platform_trace(n_padding=55):
    """Root = session wrapper with the transcript as output; finalization
    audits AFTER the padding, exactly where the old cap cut."""
    children = [
        _span(f"msg-{i}", "conversation.item", inp=f"turno {i}", out=f"respuesta {i}")
        for i in range(n_padding)
    ]
    children.append(
        _span("summary-1", "Summary", inp=SUMMARY_RULE, out=SUMMARY_OUT, kind="LLM")
    )
    children.append(
        _span("audit-57", "Adherence", inp="audita la verbalización", out=AUDIT_JSON, kind="LLM")
    )
    root = _span("root-1", "call.session", inp="llamada saliente", out=TRANSCRIPT, kind="CHAIN")
    root["child_spans"] = children
    return {"trace_id": "trace-call", "spans": [root]}


def _payload(trace):
    return build_trace_payload(trace, structural_prefilter_with_ids(trace))


class TestEverySpanReachesThePrompt:
    def test_spans_past_the_old_cap_are_visible(self):
        """The regression itself: the audit verdicts at index ~57 must reach
        the model — before, everything past span 40 silently vanished."""
        prompt = build_prompt_v8([_payload(_call_platform_trace())])
        assert AUDIT_JSON in prompt, "the exonerating audit output was hidden from the model"
        assert SUMMARY_RULE in prompt
        assert SUMMARY_OUT in prompt

    def test_the_root_transcript_is_also_visible(self):
        """No demotion either: the model sees the transcript AND the audits and
        works out for itself which is the final response."""
        prompt = build_prompt_v8([_payload(_call_platform_trace())])
        assert "¿hablo con el titular de la cuenta?" in prompt

    def test_all_sixty_spans_render(self):
        payload = _payload(_call_platform_trace())
        prompt = build_prompt_v8([payload])
        assert len(payload["spans"]) == 58
        for i in range(55):
            assert f"respuesta {i}" in prompt


class TestNothingIsPreSelected:
    def test_payload_carries_no_task_or_result(self):
        payload = _payload(_call_platform_trace())
        assert "task" not in payload
        assert "result" not in payload
        assert "prior_turns" not in payload

    def test_prompt_labels_no_span_as_the_answer(self):
        prompt = build_prompt_v8([_payload(_call_platform_trace())])
        assert "AGENT RESPONSE" not in prompt
        assert "USER REQUEST" not in prompt


class TestSpansArriveRawAndAddressable:
    def test_span_ids_are_rendered_for_the_completion_gate(self):
        prompt = build_prompt_v8([_payload(_call_platform_trace())])
        assert "[audit-57]" in prompt
        assert "[root-1]" in prompt

    def test_negations_survive_verbatim(self):
        """Raw means raw: no stopword stripping of the sentence on trial."""
        trace = {"trace_id": "t", "spans": [
            _span("s1", "tool", inp="fetch it", out="the tool did not return any data")
        ]}
        assert "the tool did not return any data" in build_prompt_v8([_payload(trace)])

    def test_kind_is_read_through_vendor_prefixes(self):
        """Kind lives under vendor prefixes (openinference.span.kind); the old
        payload read the bare key and labelled every such span CHAIN."""
        trace = {"trace_id": "t", "spans": [
            _span("s1", "generate", inp="q", out="a",
                  kind_key="openinference.span.kind", kind="LLM")
        ]}
        payload = _payload(trace)
        assert payload["spans"][0]["kind"] == "LLM"

    def test_provider_envelopes_are_not_unwrapped_in_the_payload(self):
        """The model sees the envelope as recorded and judges it itself."""
        envelope = '{"choices": [{"message": {"content": "hola"}}], "usage": {"total_tokens": 9}}'
        trace = {"trace_id": "t", "spans": [_span("s1", "llm", out=envelope)]}
        assert '"total_tokens"' in build_prompt_v8([_payload(trace)])


class TestPromptRunawayGuard:
    def test_a_pathological_trace_is_cut_visibly_not_silently(self):
        trace = {"trace_id": "t", "spans": [
            _span("s1", "blob", out="x" * (PROMPT_RUNAWAY_BUDGET + 100_000))
        ]}
        prompt = build_prompt_v8([_payload(trace)])
        assert len(prompt) <= PROMPT_RUNAWAY_BUDGET + 200
        assert "truncated by the scanner" in prompt

    def test_a_real_sized_trace_is_never_cut(self):
        prompt = build_prompt_v8([_payload(_call_platform_trace())])
        assert "truncated by the scanner" not in prompt
