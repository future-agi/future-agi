package clickhouse25exporter

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// openRouterFixture is an OTLP/HTTP JSON body shaped like OpenRouter
// Broadcast's OpenTelemetry Collector destination: one trace per request with
// a root span, a generation span and two provider attempt spans (a failed
// one, then the one that served). Attribute keys come from
// https://openrouter.ai/docs/guides/features/broadcast/otel-collector and
// https://openrouter.ai/docs/guides/features/broadcast; span names and values
// are illustrative.
const openRouterFixture = "testdata/openrouter_broadcast_otlp.json"

const (
	orProjectID    = "7a6b0000-0000-4000-8000-0000000000a1"
	orOrgID        = "7a6b0000-0000-4000-8000-0000000000b2"
	orRootID       = "00f067aa0ba902b7"
	orGenerationID = "53995c3f42cd8ad8"
	orAttemptFail  = "1a2b3c4d5e6f7081"
	orAttemptOK    = "2b3c4d5e6f708192"
	orPrompt       = `[{"role":"user","content":"Hello!"}]`
	orCompletion   = `{"role":"assistant","content":"Hi! How can I help you today?"}`
)

// loadOpenRouterFixture decodes the fixture the way the OTLP/HTTP JSON
// receiver does, then stamps the project and org onto each resource the way
// auth.StampResourceAttrs does for an authenticated request.
func loadOpenRouterFixture(t *testing.T) ptrace.Traces {
	t.Helper()
	body, err := os.ReadFile(openRouterFixture)
	if err != nil {
		t.Fatal(err)
	}
	var u ptrace.JSONUnmarshaler
	traces, err := u.UnmarshalTraces(body)
	if err != nil {
		t.Fatalf("decode fixture: %v", err)
	}
	for i := 0; i < traces.ResourceSpans().Len(); i++ {
		ra := traces.ResourceSpans().At(i).Resource().Attributes()
		ra.PutStr("fi.project_id", orProjectID)
		ra.PutStr("fi.org_id", orOrgID)
	}
	return traces
}

func rowsByID(t *testing.T, rows []map[string]any) map[string]map[string]any {
	t.Helper()
	out := make(map[string]map[string]any, len(rows))
	for _, r := range rows {
		out[r["id"].(string)] = r
	}
	return out
}

func TestOpenRouterBroadcastFillsGenerationInputOutput(t *testing.T) {
	rows, ids, err := ConvertWithIdentities(context.Background(), loadOpenRouterFixture(t), nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 4 {
		t.Fatalf("rows=%d want 4 (root, generation, two attempts)", len(rows))
	}
	byID := rowsByID(t, rows)
	for _, r := range rows {
		t.Logf("span %-16s %-17q parent=%-16s type=%-7s input=%dB output=%dB model=%q tokens=%v/%v/%v cost=%v session=%v end_user=%v",
			r["id"], r["name"], r["parent_span_id"], r["observation_type"],
			len(r["input"].(string)), len(r["output"].(string)), r["model"],
			r["prompt_tokens"], r["completion_tokens"], r["total_tokens"], r["cost"],
			r["trace_session_id"], r["end_user_id"])
	}

	wantSession := detid.TraceSessionID(orProjectID, "session_abc").String()

	gen := byID[orGenerationID]
	if gen == nil {
		t.Fatal("generation span missing")
	}
	expect := map[string]any{
		"input":             orPrompt,
		"output":            orCompletion,
		"observation_type":  "llm",
		"name":              "Generate Response",
		"parent_span_id":    orRootID,
		"trace_id":          "4bf92f35-77b3-4da6-a3ce-929d0e0e4736",
		"service_name":      "openrouter",
		"status":            "OK",
		"model":             "openai/gpt-4o",
		"provider":          "OpenAI",
		"gen_ai_system":     "openai",
		"gen_ai_operation":  "chat",
		"prompt_tokens":     int32(12),
		"completion_tokens": int32(9),
		"total_tokens":      int32(21),
		"trace_session_id":  wantSession,
	}
	for k, want := range expect {
		if got := gen[k]; got != want {
			t.Errorf("generation %s = %#v, want %#v", k, got, want)
		}
	}
	// OpenRouter sends no `project_type` resource attribute, so the
	// observe-only end-user gate leaves end_user_id NULL here. On a real
	// request auth.StampResourceAttrs adds project_type "observe" when the
	// stamped project is an Observe one (TestOpenRouterR2UserAndSession).
	if gen["end_user_id"] != nil {
		t.Errorf("generation end_user_id = %v, want nil without project_type", gen["end_user_id"])
	}

	// The source attributes stay where adapter.Split put them; only the hot
	// columns are filled from them.
	as := gen["attrs_string"].(map[string]string)
	for k, want := range map[string]string{
		"span.input":                 orPrompt,
		"span.output":                orCompletion,
		"gen_ai.prompt":              orPrompt,
		"gen_ai.completion":          orCompletion,
		"user.id":                    "user_12345",
		"session.id":                 "session_abc",
		"trace.metadata.environment": "staging",
		"span.metadata.openrouter_cost_allocation.output_cost": "0.00009",
	} {
		if as[k] != want {
			t.Errorf("generation attrs_string[%q] = %q, want %q", k, as[k], want)
		}
	}
	an := gen["attrs_number"].(map[string]float64)
	if v, ok := an["span.metadata.openrouter_generation.cache_write_tokens"]; !ok || v != 0 {
		t.Errorf("cache_write_tokens = %v (present %v), want an explicit 0", v, ok)
	}
	if v, ok := an["gen_ai.usage.total_cost"]; !ok || v != 0.0001275 {
		t.Errorf("gen_ai.usage.total_cost = %v (present %v), want 0.0001275 in attrs_number", v, ok)
	}
	if v, ok := gen["attrs_bool"].(map[string]uint8)["span.metadata.openrouter_generation.usage_is_estimated"]; !ok || v != 0 {
		t.Errorf("usage_is_estimated = %v (present %v), want 0", v, ok)
	}

	// OpenRouter puts content on the generation span only, so the root and
	// the attempt spans have none to show.
	root := byID[orRootID]
	if root["input"] != "" || root["output"] != "" {
		t.Errorf("root input/output = %q/%q, want empty", root["input"], root["output"])
	}
	if root["parent_span_id"] != "" || root["name"] != "Chat Handler" {
		t.Errorf("root parent/name = %q/%q", root["parent_span_id"], root["name"])
	}
	// OpenRouter names no kind on the root or the attempts: they are chains.
	for _, id := range []string{orRootID, orAttemptFail, orAttemptOK} {
		if got := byID[id]["observation_type"]; got != "chain" {
			t.Errorf("span %s observation_type = %v, want chain", id, got)
		}
	}
	if root["trace_session_id"] != wantSession {
		t.Errorf("root trace_session_id = %v, want %s", root["trace_session_id"], wantSession)
	}
	if got := root["attrs_string"].(map[string]string)["trace.metadata.environment"]; got != "staging" {
		t.Errorf("root trace.metadata.environment = %q", got)
	}
	for _, id := range []string{orAttemptFail, orAttemptOK} {
		a := byID[id]
		if a["input"] != "" || a["output"] != "" {
			t.Errorf("attempt %s input/output = %q/%q, want empty", id, a["input"], a["output"])
		}
		if a["parent_span_id"] != orGenerationID {
			t.Errorf("attempt %s parent = %q, want the generation span", id, a["parent_span_id"])
		}
	}
	if fail := byID[orAttemptFail]; fail["status"] != "ERROR" || fail["status_message"] != "429 Too Many Requests" {
		t.Errorf("failed attempt status = %v %q", fail["status"], fail["status_message"])
	}

	// The traces row comes from the root span, which carries no content.
	ts := ids.Traces()
	if len(ts) != 1 {
		t.Fatalf("traces rows = %d, want 1", len(ts))
	}
	if ts[0].Name != "Chat Handler" || ts[0].SessionID != wantSession {
		t.Errorf("trace row name/session = %q/%q", ts[0].Name, ts[0].SessionID)
	}
	if ts[0].Input != "" || ts[0].Output != "" {
		t.Errorf("trace row input/output = %q/%q, want empty (root has no content)", ts[0].Input, ts[0].Output)
	}
	if s := ids.Sessions(); len(s) != 1 || s[0].ExternalSessionID != "session_abc" {
		t.Errorf("sessions = %+v, want one session_abc", s)
	}
}

// contentSpan builds a single span with fill applied to its attributes.
func contentSpan(fill func(pcommon.Map)) ptrace.Traces {
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", "3f2e0000-0000-4000-8000-000000000001")
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetName("content")
	sp.SetTraceID([16]byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16})
	sp.SetSpanID([8]byte{1, 2, 3, 4, 5, 6, 7, 8})
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Unix(1700000000, 0)))
	fill(sp.Attributes())
	return td
}

func convertOne(t *testing.T, td ptrace.Traces) map[string]any {
	t.Helper()
	rows, err := Convert(td)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows=%d want 1", len(rows))
	}
	return rows[0]
}

func TestSpanContentPrecedence(t *testing.T) {
	cases := []struct {
		name       string
		fill       func(pcommon.Map)
		wantInput  string
		wantOutput string
	}{
		{
			name: "input.value wins over every GenAI key",
			fill: func(m pcommon.Map) {
				m.PutStr("input.value", "iv")
				m.PutStr("span.input", "si")
				m.PutStr("gen_ai.prompt", "gp")
				m.PutStr("gen_ai.input.messages", "gim")
				m.PutStr("output.value", "ov")
				m.PutStr("span.output", "so")
				m.PutStr("gen_ai.completion", "gc")
				m.PutStr("gen_ai.output.messages", "gom")
			},
			wantInput: "iv", wantOutput: "ov",
		},
		{
			name: "span.input next",
			fill: func(m pcommon.Map) {
				m.PutStr("span.input", "si")
				m.PutStr("gen_ai.prompt", "gp")
				m.PutStr("gen_ai.input.messages", "gim")
				m.PutStr("span.output", "so")
				m.PutStr("gen_ai.completion", "gc")
				m.PutStr("gen_ai.output.messages", "gom")
			},
			wantInput: "si", wantOutput: "so",
		},
		{
			name: "gen_ai.prompt before gen_ai.input.messages",
			fill: func(m pcommon.Map) {
				m.PutStr("gen_ai.prompt", "gp")
				m.PutStr("gen_ai.input.messages", "gim")
				m.PutStr("gen_ai.completion", "gc")
				m.PutStr("gen_ai.output.messages", "gom")
			},
			wantInput: "gp", wantOutput: "gc",
		},
		{
			name: "gen_ai.input.messages last",
			fill: func(m pcommon.Map) {
				m.PutStr("gen_ai.input.messages", `[{"role":"user","parts":[{"type":"text","content":"hi"}]}]`)
				m.PutStr("gen_ai.output.messages", `[{"role":"assistant","parts":[{"type":"text","content":"hello"}]}]`)
			},
			wantInput:  `[{"role":"user","parts":[{"type":"text","content":"hi"}]}]`,
			wantOutput: `[{"role":"assistant","parts":[{"type":"text","content":"hello"}]}]`,
		},
		{
			name: "empty values don't block a later key",
			fill: func(m pcommon.Map) {
				m.PutStr("input.value", "")
				m.PutStr("span.input", "")
				m.PutStr("gen_ai.prompt", "gp")
				m.PutEmpty("output.value")
				m.PutStr("span.output", "so")
			},
			wantInput: "gp", wantOutput: "so",
		},
		{
			name: "map and array values are JSON-encoded",
			fill: func(m pcommon.Map) {
				in := m.PutEmptyMap("span.input")
				in.PutStr("role", "user")
				in.PutStr("content", "hi")
				out := m.PutEmptySlice("gen_ai.output.messages")
				msg := out.AppendEmpty().SetEmptyMap()
				msg.PutStr("role", "assistant")
				msg.PutInt("n", 1)
			},
			wantInput:  `{"content":"hi","role":"user"}`,
			wantOutput: `[{"n":1,"role":"assistant"}]`,
		},
		{
			name: "indexed message keys are not read",
			fill: func(m pcommon.Map) {
				m.PutStr("gen_ai.prompt.0.role", "user")
				m.PutStr("gen_ai.prompt.0.content", "hi")
				m.PutStr("gen_ai.input.messages.0.message.content", "hi")
				m.PutStr("gen_ai.completion.0.content", "hello")
				m.PutStr("gen_ai.output.messages.0.message.content", "hello")
			},
			wantInput: "", wantOutput: "",
		},
		{
			name:      "no content keys",
			fill:      func(m pcommon.Map) { m.PutStr("gen_ai.operation.name", "chat") },
			wantInput: "", wantOutput: "",
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := convertOne(t, contentSpan(c.fill))
			if r["input"] != c.wantInput {
				t.Errorf("input = %q, want %q", r["input"], c.wantInput)
			}
			if r["output"] != c.wantOutput {
				t.Errorf("output = %q, want %q", r["output"], c.wantOutput)
			}
		})
	}
}

// A traceAI / OpenInference span stores input.value / output.value exactly
// as before, whatever GenAI content keys sit next to it.
func TestTraceAISpanContentUnchanged(t *testing.T) {
	const (
		inputJSON  = `{"messages":[{"role":"user","content":"What is 2+2?"}]}`
		outputJSON = `{"choices":[{"message":{"role":"assistant","content":"4"}}]}`
	)
	cases := []struct {
		name       string
		fill       func(pcommon.Map)
		wantInput  string
		wantOutput string
	}{
		{
			name: "traceAI LLM span with flattened messages",
			fill: func(m pcommon.Map) {
				m.PutStr("fi.span.kind", "LLM")
				m.PutStr("input.value", inputJSON)
				m.PutStr("input.mime_type", "application/json")
				m.PutStr("output.value", outputJSON)
				m.PutStr("output.mime_type", "application/json")
				m.PutStr("gen_ai.input.messages.0.message.role", "user")
				m.PutStr("gen_ai.input.messages.0.message.content", "What is 2+2?")
				m.PutStr("gen_ai.output.messages.0.message.role", "assistant")
				m.PutStr("gen_ai.output.messages.0.message.content", "4")
			},
			wantInput: inputJSON, wantOutput: outputJSON,
		},
		{
			name: "traceAI span with GenAI content keys beside input.value",
			fill: func(m pcommon.Map) {
				m.PutStr("input.value", "from input.value")
				m.PutStr("output.value", "from output.value")
				m.PutStr("gen_ai.prompt", "from gen_ai.prompt")
				m.PutStr("gen_ai.completion", "from gen_ai.completion")
				m.PutStr("gen_ai.input.messages", "[]")
				m.PutStr("span.output", "from span.output")
			},
			wantInput: "from input.value", wantOutput: "from output.value",
		},
		{
			name: "FI_HIDE_INPUTS redaction still wins",
			fill: func(m pcommon.Map) {
				m.PutStr("input.value", "__REDACTED__")
				m.PutStr("output.value", "__REDACTED__")
				m.PutStr("gen_ai.prompt", "raw prompt")
				m.PutStr("gen_ai.completion", "raw completion")
			},
			wantInput: "__REDACTED__", wantOutput: "__REDACTED__",
		},
		{
			name: "OpenInference map-valued input.value",
			fill: func(m pcommon.Map) {
				in := m.PutEmptyMap("input.value")
				in.PutStr("query", "q")
				m.PutStr("span.input", "ignored")
				out := m.PutEmptySlice("output.value")
				out.AppendEmpty().SetStr("a")
				m.PutStr("gen_ai.completion", "ignored")
			},
			wantInput: `{"query":"q"}`, wantOutput: `["a"]`,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := convertOne(t, contentSpan(c.fill))
			if r["input"] != c.wantInput {
				t.Errorf("input = %q, want %q", r["input"], c.wantInput)
			}
			if r["output"] != c.wantOutput {
				t.Errorf("output = %q, want %q", r["output"], c.wantOutput)
			}
			// input.value / output.value stay in the overflow tier.
			of := r["attributes_extra"].(map[string]any)
			if _, ok := of["input.value"]; !ok {
				t.Errorf("input.value missing from attributes_extra: %#v", of)
			}
		})
	}

	// The canonical OpenInference fixture keeps every column it had.
	r := convertOne(t, buildOTLPSpan())
	if r["input"] != "Hello, world!" || r["output"] != "Hi there." {
		t.Errorf("buildOTLPSpan input/output = %q/%q", r["input"], r["output"])
	}
}
