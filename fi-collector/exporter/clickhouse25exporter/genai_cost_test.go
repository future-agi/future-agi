package clickhouse25exporter

import (
	"context"
	"math"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// orSenderTotalCost is the generation span's gen_ai.usage.total_cost in the
// OpenRouter fixture. Its input_cost + output_cost (0.00012) is lower, since
// OpenRouter's total includes its fee.
const orSenderTotalCost = 0.0001275

// recordingPricer is the production pricer over the embedded litellm table,
// without per-org custom pricing, and records every model it is asked about.
type recordingPricer struct {
	table  *pricing.Table
	inner  *pricing.Pricer
	models []string
}

func newRecordingPricer(t *testing.T) *recordingPricer {
	t.Helper()
	tbl, err := pricing.LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	return &recordingPricer{table: tbl, inner: pricing.New(tbl, nil)}
}

func (r *recordingPricer) TokenCost(ctx context.Context, orgID, model string, p, c int32) (float64, bool) {
	r.models = append(r.models, model)
	return r.inner.TokenCost(ctx, orgID, model, p, c)
}

func (r *recordingPricer) asked(model string) bool {
	for _, m := range r.models {
		if m == model {
			return true
		}
	}
	return false
}

func sumCost(rows []map[string]any) float64 {
	var s float64
	for _, r := range rows {
		s += r["cost"].(float64)
	}
	return s
}

// near compares computed prices; arm64 may fuse the price multiply-add
// differently in the converter and in the test.
func near(a, b float64) bool { return math.Abs(a-b) < 1e-12 }

// spanAttrs returns the attributes of the span with the given hex id.
func spanAttrs(t *testing.T, td ptrace.Traces, spanID string) pcommon.Map {
	t.Helper()
	rss := td.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		sss := rss.At(i).ScopeSpans()
		for j := 0; j < sss.Len(); j++ {
			ss := sss.At(j).Spans()
			for k := 0; k < ss.Len(); k++ {
				if ss.At(k).SpanID().String() == spanID {
					return ss.At(k).Attributes()
				}
			}
		}
	}
	t.Fatalf("span %s not in fixture", spanID)
	return pcommon.Map{}
}

// OpenRouter's billed cost is stored as the generation span's cost, even
// though "openai/gpt-4o" has no exact price-table key, and the pricer is
// not asked to price that span.
func TestOpenRouterBroadcastSenderCost(t *testing.T) {
	pr := newRecordingPricer(t)
	rows, _, err := ConvertWithIdentities(context.Background(), loadOpenRouterFixture(t), pr)
	if err != nil {
		t.Fatal(err)
	}
	byID := rowsByID(t, rows)

	gen := byID[orGenerationID]
	if got := gen["cost"].(float64); got != orSenderTotalCost {
		t.Errorf("generation cost = %v, want the sender's total %v", got, orSenderTotalCost)
	}
	if pr.asked("openai/gpt-4o") {
		t.Errorf("pricer was asked to price the generation span (asked %q); the sender cost must win", pr.models)
	}
}

// One OpenRouter request is one charge. The root repeats it under
// trace.metadata.openrouter_generation.usage and the cost-allocation keys,
// and the attempt spans carry none; only the generation span's cost counts,
// so a trace or session sum(cost) equals the charge once.
func TestOpenRouterBroadcastCostNotDoubleCounted(t *testing.T) {
	for _, tc := range []struct {
		name   string
		pricer func(*testing.T) Pricer
	}{
		{"production pricer", func(t *testing.T) Pricer { return newRecordingPricer(t) }},
		{"no pricer", func(*testing.T) Pricer { return nil }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rows, _, err := ConvertWithIdentities(context.Background(), loadOpenRouterFixture(t), tc.pricer(t))
			if err != nil {
				t.Fatal(err)
			}
			byID := rowsByID(t, rows)

			// The root does carry the charge, so this test would see a
			// double count if any of those keys were read as cost.
			root := byID[orRootID]
			if v := root["attrs_number"].(map[string]float64)["trace.metadata.openrouter_generation.usage"]; v != orSenderTotalCost {
				t.Fatalf("fixture root trace.metadata.openrouter_generation.usage = %v, want %v", v, orSenderTotalCost)
			}
			for _, id := range []string{orRootID, orAttemptFail, orAttemptOK} {
				if c := byID[id]["cost"].(float64); c != 0 {
					t.Errorf("span %s cost = %v, want 0", id, c)
				}
				for _, k := range []string{"prompt_tokens", "completion_tokens", "total_tokens"} {
					if v := byID[id][k].(int32); v != 0 {
						t.Errorf("span %s %s = %d, want 0", id, k, v)
					}
				}
			}
			if s := sumCost(rows); !near(s, orSenderTotalCost) {
				t.Errorf("sum(cost) over the trace = %v, want %v once", s, orSenderTotalCost)
			}
		})
	}
}

// Without a sender cost the pricer still prices the span, and the
// provider-prefixed fallback finds "openai/gpt-4o" as gpt-4o.
func TestOpenRouterBroadcastNoSenderCostUsesPricer(t *testing.T) {
	td := loadOpenRouterFixture(t)
	attrs := spanAttrs(t, td, orGenerationID)
	for _, k := range []string{"gen_ai.usage.total_cost", "gen_ai.usage.input_cost", "gen_ai.usage.output_cost"} {
		if !attrs.Remove(k) {
			t.Fatalf("fixture generation span has no %s", k)
		}
	}

	pr := newRecordingPricer(t)
	want, ok := pr.table.Cost("gpt-4o", 12, 9)
	if !ok || want == 0 {
		t.Fatal("embedded price table must price gpt-4o")
	}
	rows, _, err := ConvertWithIdentities(context.Background(), td, pr)
	if err != nil {
		t.Fatal(err)
	}
	gen := rowsByID(t, rows)[orGenerationID]
	if got := gen["cost"].(float64); !near(got, want) {
		t.Errorf("generation cost = %v, want the token price %v", got, want)
	}
	if !pr.asked("openai/gpt-4o") {
		t.Errorf("pricer was not asked about openai/gpt-4o (asked %q)", pr.models)
	}
	if s := sumCost(rows); !near(s, want) {
		t.Errorf("sum(cost) = %v, want %v", s, want)
	}

	// No pricer: cost stays 0, as before.
	rows, _, err = ConvertWithIdentities(context.Background(), td, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := rowsByID(t, rows)[orGenerationID]["cost"].(float64); got != 0 {
		t.Errorf("generation cost without a pricer = %v, want 0", got)
	}
}

// OpenRouter's webhook destination example, unchanged
// (https://openrouter.ai/docs/guides/features/broadcast/webhook) apart from
// real ids: it uses the old gen_ai.usage.prompt_tokens / completion_tokens
// names and carries no cost.
const openRouterWebhookExample = `{
  "resourceSpans": [{
    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "openrouter"}}]},
    "scopeSpans": [{
      "spans": [{
        "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
        "spanId": "00f067aa0ba902b7",
        "name": "chat",
        "startTimeUnixNano": "1705312800000000000",
        "endTimeUnixNano": "1705312801000000000",
        "attributes": [
          {"key": "gen_ai.request.model", "value": {"stringValue": "openai/gpt-4"}},
          {"key": "gen_ai.usage.prompt_tokens", "value": {"intValue": "100"}},
          {"key": "gen_ai.usage.completion_tokens", "value": {"intValue": "50"}}
        ]
      }]
    }]
  }]
}`

func TestOpenRouterLegacyTokenNames(t *testing.T) {
	var u ptrace.JSONUnmarshaler
	td, err := u.UnmarshalTraces([]byte(openRouterWebhookExample))
	if err != nil {
		t.Fatal(err)
	}
	pr := newRecordingPricer(t)
	rows, _, err := ConvertWithIdentities(context.Background(), td, pr)
	if err != nil {
		t.Fatal(err)
	}
	r := rows[0]
	for k, want := range map[string]int32{"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150} {
		if got := r[k].(int32); got != want {
			t.Errorf("%s = %d, want %d", k, got, want)
		}
	}
	// The legacy counts feed token pricing too.
	want, ok := pr.table.Cost("gpt-4", 100, 50)
	if !ok || want == 0 {
		t.Fatal("embedded price table must price gpt-4")
	}
	if got := r["cost"].(float64); !near(got, want) {
		t.Errorf("cost = %v, want %v", got, want)
	}
}

// traceAI spans (current gen_ai.* names, and the older llm.token_count.* /
// OpenInference ones) keep the tokens and cost they had, whatever OpenRouter
// keys sit beside them.
func TestTraceAISpanCostAndTokensUnchanged(t *testing.T) {
	tbl, err := pricing.LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	gpt4o, _ := tbl.Cost("gpt-4o", 120, 38)
	gpt4oMini, _ := tbl.Cost("gpt-4o-mini", 120, 38)

	traceAILLM := func(m pcommon.Map) {
		m.PutStr("gen_ai.span.kind", "LLM")
		m.PutStr("gen_ai.provider.name", "openai")
		m.PutStr("gen_ai.request.model", "gpt-4o")
		m.PutInt("gen_ai.usage.input_tokens", 120)
		m.PutInt("gen_ai.usage.output_tokens", 38)
		m.PutInt("gen_ai.usage.total_tokens", 158)
	}
	cases := []struct {
		name                   string
		fill                   func(pcommon.Map)
		wantIn, wantOut, wantT int32
		wantCost               float64
	}{
		{
			name:   "traceAI LLM span, priced from tokens",
			fill:   traceAILLM,
			wantIn: 120, wantOut: 38, wantT: 158, wantCost: gpt4o,
		},
		{
			name: "traceAI gen_ai.cost.total wins over a sender total",
			fill: func(m pcommon.Map) {
				traceAILLM(m)
				m.PutDouble("gen_ai.cost.total", 0.5)
				m.PutDouble("gen_ai.usage.total_cost", 0.7)
			},
			wantIn: 120, wantOut: 38, wantT: 158, wantCost: 0.5,
		},
		{
			name: "traceAI gen_ai.cost.input+output wins over a sender total",
			fill: func(m pcommon.Map) {
				traceAILLM(m)
				m.PutDouble("gen_ai.cost.input", 0.1)
				m.PutDouble("gen_ai.cost.output", 0.2)
				m.PutDouble("gen_ai.usage.total_cost", 0.7)
			},
			wantIn: 120, wantOut: 38, wantT: 158, wantCost: 0.1 + 0.2,
		},
		{
			name: "traceAI explicit gen_ai.cost.total 0 stays 0",
			fill: func(m pcommon.Map) {
				traceAILLM(m)
				m.PutDouble("gen_ai.cost.total", 0)
				m.PutDouble("gen_ai.usage.total_cost", 0.7)
			},
			wantIn: 120, wantOut: 38, wantT: 158, wantCost: 0,
		},
		{
			name: "current token names win over the legacy ones",
			fill: func(m pcommon.Map) {
				traceAILLM(m)
				m.PutInt("gen_ai.usage.prompt_tokens", 1000)
				m.PutInt("gen_ai.usage.completion_tokens", 1000)
			},
			wantIn: 120, wantOut: 38, wantT: 158, wantCost: gpt4o,
		},
		{
			name: "older traceAI / OpenInference llm.* keys",
			fill: func(m pcommon.Map) {
				m.PutStr("fi.span.kind", "LLM")
				m.PutStr("llm.model_name", "gpt-4o-mini")
				m.PutInt("llm.token_count.prompt", 120)
				m.PutInt("llm.token_count.completion", 38)
				m.PutInt("llm.token_count.total", 158)
				m.PutInt("gen_ai.usage.prompt_tokens", 1000)
				m.PutInt("gen_ai.usage.completion_tokens", 1000)
			},
			wantIn: 120, wantOut: 38, wantT: 158, wantCost: gpt4oMini,
		},
	}
	pr := pricing.New(tbl, nil)
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rows, _, err := ConvertWithIdentities(context.Background(), contentSpan(c.fill), pr)
			if err != nil {
				t.Fatal(err)
			}
			r := rows[0]
			if r["prompt_tokens"] != c.wantIn || r["completion_tokens"] != c.wantOut || r["total_tokens"] != c.wantT {
				t.Errorf("tokens = %v/%v/%v, want %d/%d/%d",
					r["prompt_tokens"], r["completion_tokens"], r["total_tokens"], c.wantIn, c.wantOut, c.wantT)
			}
			if got := r["cost"].(float64); !near(got, c.wantCost) {
				t.Errorf("cost = %v, want %v", got, c.wantCost)
			}
			if r["observation_type"] != "llm" {
				t.Errorf("observation_type = %v, want llm", r["observation_type"])
			}
		})
	}

	// The canonical OpenInference fixture keeps its tokens and price.
	rows, _, err := ConvertWithIdentities(context.Background(), buildOTLPSpan(), pr)
	if err != nil {
		t.Fatal(err)
	}
	r := rows[0]
	if r["prompt_tokens"] != int32(120) || r["completion_tokens"] != int32(38) || r["total_tokens"] != int32(158) {
		t.Errorf("buildOTLPSpan tokens = %v/%v/%v", r["prompt_tokens"], r["completion_tokens"], r["total_tokens"])
	}
	if got := r["cost"].(float64); !near(got, gpt4oMini) || got == 0 {
		t.Errorf("buildOTLPSpan cost = %v, want %v", got, gpt4oMini)
	}
}
