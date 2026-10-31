package clickhouse25exporter

import (
	"context"
	"encoding/json"
	"os"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

type normalizationPricer struct{ calls int }

func (p *normalizationPricer) TokenCost(context.Context, string, string, int32, int32) (float64, bool) {
	p.calls++
	return 0.5, true
}
func (p *normalizationPricer) OpenRouterTokenCost(ctx context.Context, org, model string, in, out int32) (float64, bool) {
	return p.TokenCost(ctx, org, model, in, out)
}

func copilotFixture(t *testing.T) ptrace.Traces {
	t.Helper()
	raw, err := os.ReadFile("testdata/copilot_1_0_98_otlp.json")
	if err != nil {
		t.Fatal(err)
	}
	req := ptraceotlp.NewExportRequest()
	if err = req.UnmarshalJSON(raw); err != nil {
		t.Fatal(err)
	}
	return req.Traces()
}

func TestCopilotAccountingAndSessions(t *testing.T) {
	td := copilotFixture(t)
	price := &normalizationPricer{}
	rows, ids, err := ConvertWithIdentities(context.Background(), td, price)
	if err != nil {
		t.Fatal(err)
	}
	var input, output int32
	var cost float64
	calls := 0
	session := rows[0]["trace_session_id"]
	if session == nil {
		t.Fatal("conversation session not mapped")
	}
	for _, row := range rows {
		input += row["prompt_tokens"].(int32)
		output += row["completion_tokens"].(int32)
		cost += row["cost"].(float64)
		if row["trace_session_id"] != session {
			t.Fatal("split session")
		}
		if row["observation_type"] == "llm" {
			calls++
		}
	}
	if input != 42925 || output != 146 || calls != 3 {
		t.Fatalf("totals=%d/%d calls=%d", input, output, calls)
	}
	if rows[0]["observation_type"] != "agent" || rows[4]["observation_type"] != "chain" {
		t.Fatal("operations not normalized")
	}
	if rows[0]["cost"] != float64(0) {
		t.Fatal("summary repriced")
	}
	raw := rows[0]["attrs_number"].(map[string]float64)
	if raw["fi.source.summary.gen_ai.usage.input_tokens"] != 42925 || raw["github.copilot.cost"] != 3 {
		t.Fatal("raw counters/multiplier lost")
	}
	// A hook has no tokens but the deliberately aggressive test pricer returns a
	// price; only the summary must categorically skip it (no fake cost attr).
	if price.calls != 4 || cost != 2 {
		t.Fatalf("pricing calls=%d cost=%v", price.calls, cost)
	}
	if len(ids.Sessions()) == 0 {
		t.Fatal("missing curated session")
	}
}

func TestCopilotSplitBatchAndEqualCalls(t *testing.T) {
	td := copilotFixture(t)
	ss := td.ResourceSpans().At(0).ScopeSpans().At(0)
	// The summary arrives alone; this must NOT depend on same-batch children.
	ss.Spans().RemoveIf(func(sp ptrace.Span) bool { return sp.ParentSpanID() != pcommon.SpanID{} })
	rows, _, err := ConvertWithIdentities(context.Background(), td, &normalizationPricer{})
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0]["prompt_tokens"] != int32(0) || rows[0]["cost"] != float64(0) {
		t.Fatal(rows)
	}
	td = copilotFixture(t)
	ss = td.ResourceSpans().At(0).ScopeSpans().At(0)
	ss.Spans().At(1).CopyTo(ss.Spans().AppendEmpty())
	ss.Spans().At(5).SetSpanID(pcommon.SpanID{9})
	rows, err = Convert(td)
	if err != nil {
		t.Fatal(err)
	}
	total := int32(0)
	for _, r := range rows {
		total += r["prompt_tokens"].(int32)
	}
	if total != 42925+14095 {
		t.Fatalf("equal-value independent call lost: %d", total)
	}
}

func TestNativeCachePricingIsUnknownUnlessReported(t *testing.T) {
	td := copilotFixture(t)
	sp := td.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(1)
	sp.Attributes().PutInt("gen_ai.usage.cache_read.input_tokens", 40)
	p := &normalizationPricer{}
	rows, _, err := ConvertWithIdentities(context.Background(), td, p)
	if err != nil {
		t.Fatal(err)
	}
	if rows[1]["cost"] != float64(0) || rows[1]["attrs_string"].(map[string]string)["fi.cost.kind"] != "unknown" {
		t.Fatal(rows[1])
	}
	sp.Attributes().PutDouble("gen_ai.cost.total", 0.123)
	rows, _, err = ConvertWithIdentities(context.Background(), td, p)
	if err != nil {
		t.Fatal(err)
	}
	if rows[1]["cost"] != 0.123 {
		t.Fatal("provider USD lost")
	}
}

func TestEventsAndLinksReachStoredRow(t *testing.T) {
	td := copilotFixture(t)
	sp := td.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0)
	ev := sp.Events().AppendEmpty()
	ev.SetName("error\x01")
	ev.Attributes().PutStr("exception.type", "Timeout")
	ev.Attributes().PutStr("exception.message", "secret-canary")
	link := sp.Links().AppendEmpty()
	link.SetTraceID(pcommon.TraceID{1})
	link.SetSpanID(pcommon.SpanID{2})
	link.Attributes().PutStr("fi.agent.caused_by.work_id", "opaque")
	rows, err := Convert(td)
	if err != nil {
		t.Fatal(err)
	}
	var events []map[string]any
	if err = json.Unmarshal([]byte(rows[0]["span_events"].(string)), &events); err != nil {
		t.Fatal(err)
	}
	if events[0]["attributes"].(map[string]any)["exception.type"] != "Timeout" {
		t.Fatal(events)
	}
	if _, ok := events[0]["attributes"].(map[string]any)["exception.message"]; ok {
		t.Fatal("unapproved text")
	}
	if _, ok := rows[0]["attributes_extra"].(map[string]any)["fi.otel.links"]; !ok {
		t.Fatal("links not stored")
	}
}

// DASH-E2E-010/011/012 pin exact legacy attribute maps. Provenance is a
// qualified-profile feature, not a new implicit field on every customer's span.
func TestLegacyCostDoesNotInjectProvenance(t *testing.T) {
	for _, kind := range []string{"chain", "llm"} {
		t.Run(kind, func(t *testing.T) {
			td := ptrace.NewTraces()
			sp := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty()
			sp.Attributes().PutStr("fi.span.kind", kind)
			sp.Attributes().PutDouble("gen_ai.cost.total", 0)
			rows, err := Convert(td)
			if err != nil {
				t.Fatal(err)
			}
			attrs := rows[0]["attrs_string"].(map[string]string)
			if len(attrs) != 1 || attrs["fi.span.kind"] != kind {
				t.Fatalf("legacy attributes changed: %#v", attrs)
			}
		})
	}
}
