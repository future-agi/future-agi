package clickhouse25exporter

import (
	"context"
	"os"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// langfuseFixture is the OTLP/HTTP JSON body the Langfuse Python SDK 5.2.0
// exports to /api/public/otel/v1/traces for a small traced script: one
// root span observation around one generation, here also inside
// propagate_attributes(user_id, session_id, tags). Attribute names and value
// shapes follow
// https://github.com/langfuse/langfuse-python/blob/v5.2.0/langfuse/_client/attributes.py
// and the scope is the SDK's tracer (langfuse-sdk, the SDK version).
const langfuseFixture = "testdata/langfuse_5_2_0_otlp.json"

const (
	lfProjectID    = "7a6b0000-0000-4000-8000-0000000000c3"
	lfOrgID        = "7a6b0000-0000-4000-8000-0000000000d4"
	lfRootID       = "4a5b6c7d8e9f0a1b"
	lfGenerationID = "5b6c7d8e9f0a1b2c"
	lfQuestion     = `[{"role": "user", "content": "PARITY-LF What is the capital of France?"}]`
	lfAnswer       = `{"role": "assistant", "content": "PARITY-LF The capital of France is Paris."}`
	lfRootInput    = `{"question": "PARITY-LF What is the capital of France?"}`
	lfRootOutput   = `{"answer": "PARITY-LF The capital of France is Paris."}`
)

// wantColumn reports a row column that differs from want.
func wantColumn(t *testing.T, row map[string]any, column string, want any) {
	t.Helper()
	if got := row[column]; got != want {
		t.Errorf("span %v: %s = %#v, want %#v", row["name"], column, got, want)
	}
}

// TestLangfuseSDKTraceMapsLikePlainOTLP converts the Langfuse SDK export with
// the production pricer and checks what the product shows for it: the
// generation is an llm span with its model, provider, tokens, a cost priced
// from them and its input/output; the root is a chain whose input/output and
// name become the trace's; both carry the session.
func TestLangfuseSDKTraceMapsLikePlainOTLP(t *testing.T) {
	body, err := os.ReadFile(langfuseFixture)
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
		ra.PutStr("fi.project_id", lfProjectID)
		ra.PutStr("fi.org_id", lfOrgID)
	}
	pricer := newRecordingPricer(t)
	wantCost, ok := pricer.table.Cost("gpt-4o-mini", 12, 9)
	if !ok || wantCost <= 0 {
		t.Fatalf("gpt-4o-mini table price = %v, %v; want a positive price", wantCost, ok)
	}

	rows, ids, err := ConvertWithIdentities(context.Background(), traces, pricer)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("rows = %d, want 2 (root, generation)", len(rows))
	}
	byID := rowsByID(t, rows)
	wantSession := detid.TraceSessionID(lfProjectID, "parity-session").String()

	gen := byID[lfGenerationID]
	if gen == nil {
		t.Fatal("generation span missing")
	}
	wantColumn(t, gen, "observation_type", "llm")
	wantColumn(t, gen, "model", "gpt-4o-mini")
	wantColumn(t, gen, "provider", "openai")
	wantColumn(t, gen, "prompt_tokens", int32(12))
	wantColumn(t, gen, "completion_tokens", int32(9))
	wantColumn(t, gen, "total_tokens", int32(21))
	wantColumn(t, gen, "input", lfQuestion)
	wantColumn(t, gen, "output", lfAnswer)
	wantColumn(t, gen, "parent_span_id", lfRootID)
	wantColumn(t, gen, "trace_session_id", wantSession)
	if cost, _ := gen["cost"].(float64); !near(cost, wantCost) {
		t.Errorf("generation cost = %v, want %v (gpt-4o-mini, 12 in, 9 out)", gen["cost"], wantCost)
	}
	if !pricer.asked("gpt-4o-mini") {
		t.Error("the generation was not priced by its model")
	}
	// The SDK's own attributes stay next to the mapped ones.
	if got := gen["attrs_string"].(map[string]string)["langfuse.observation.model.name"]; got != "gpt-4o-mini" {
		t.Errorf("raw langfuse.observation.model.name = %q, want it kept", got)
	}

	root := byID[lfRootID]
	if root == nil {
		t.Fatal("root span missing")
	}
	wantColumn(t, root, "observation_type", "chain")
	wantColumn(t, root, "input", lfRootInput)
	wantColumn(t, root, "output", lfRootOutput)
	wantColumn(t, root, "parent_span_id", "")
	wantColumn(t, root, "model", "")
	wantColumn(t, root, "cost", float64(0))
	wantColumn(t, root, "trace_session_id", wantSession)

	// The traces row comes from the root span, as for a plain OTLP trace.
	ts := ids.Traces()
	if len(ts) != 1 {
		t.Fatalf("traces rows = %d, want 1", len(ts))
	}
	if ts[0].Name != "parity-langfuse-chat" || ts[0].Input != lfRootInput || ts[0].Output != lfRootOutput || ts[0].SessionID != wantSession {
		t.Errorf("trace row = %+v, want the root's name, input, output and session", ts[0])
	}
}
