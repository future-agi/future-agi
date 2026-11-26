package clickhouse25exporter

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// R2's OpenRouter Broadcast bodies (TH-8787), as posted to a self-hosted
// install. OR5-real-free mirrors a real OpenRouter :free call byte for byte;
// OR3b is a claude-haiku-4.5 call exported without cost metadata; OR1 has two
// provider attempts, the first failed (429), and OpenRouter's billed cost.
const (
	r2FixtureDir = "testdata/openrouter"

	r2OR5         = "OR5-real-free.json"
	r2OR5Root     = "74597871150ac906"
	r2OR5Gen      = "13d37b41acfb34dd"
	r2OR5Attempt  = "b199aaae6147d494"
	r2OR3b        = "OR3b-claude-nocost.json"
	r2OR3bRoot    = "3eab198783aea476"
	r2OR3bGen     = "160f44851586895c"
	r2OR3bAttempt = "f0a0cd3189a9ba83"
	r2OR1         = "OR1-gpt4omini-cost.json"
	r2OR1Root     = "033d0e2c924ff322"
	r2OR1Gen      = "483fde9fd9a40d5d"
	r2OR1Fail     = "e55d2a8eec90f454"
	r2OR1OK       = "dc8d1d9ac2feb004"
)

// loadR2Fixture decodes an R2 body like the OTLP/HTTP JSON receiver and
// stamps project and org the way auth.StampResourceAttrs does. edit, when
// non-nil, changes each resource's attributes afterwards.
func loadR2Fixture(t *testing.T, name string, edit func(pcommon.Map)) ptrace.Traces {
	t.Helper()
	body, err := os.ReadFile(filepath.Join(r2FixtureDir, name))
	if err != nil {
		t.Fatal(err)
	}
	var u ptrace.JSONUnmarshaler
	td, err := u.UnmarshalTraces(body)
	if err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	for i := 0; i < td.ResourceSpans().Len(); i++ {
		ra := td.ResourceSpans().At(i).Resource().Attributes()
		ra.PutStr("fi.project_id", orProjectID)
		ra.PutStr("fi.org_id", orOrgID)
		if edit != nil {
			edit(ra)
		}
	}
	return td
}

func convertR2(t *testing.T, td ptrace.Traces, pr Pricer) map[string]map[string]any {
	t.Helper()
	rows, _, err := ConvertWithIdentities(context.Background(), td, pr)
	if err != nil {
		t.Fatal(err)
	}
	return rowsByID(t, rows)
}

// G7: OpenRouter's root and Provider Attempt spans name no kind. They are
// stored as "chain"; the generation span stays "llm".
func TestOpenRouterR2SpanTypes(t *testing.T) {
	for _, tc := range []struct {
		file    string
		gen     string
		untyped []string
	}{
		{r2OR5, r2OR5Gen, []string{r2OR5Root, r2OR5Attempt}},
		{r2OR3b, r2OR3bGen, []string{r2OR3bRoot, r2OR3bAttempt}},
		{r2OR1, r2OR1Gen, []string{r2OR1Root, r2OR1Fail, r2OR1OK}},
	} {
		t.Run(tc.file, func(t *testing.T) {
			byID := convertR2(t, loadR2Fixture(t, tc.file, nil), nil)
			if got := byID[tc.gen]["observation_type"]; got != "llm" {
				t.Errorf("generation observation_type = %v, want llm", got)
			}
			for _, id := range tc.untyped {
				if got := byID[id]["observation_type"]; got != "chain" {
					t.Errorf("span %s (%v) observation_type = %v, want chain", id, byID[id]["name"], got)
				}
			}
		})
	}
}

// The attempt spans carry no OpenRouter-namespaced key, so with another
// service.name they are still recognised through the root's
// trace.metadata.openrouter.* keys in the same resource.
func TestOpenRouterR2SpanTypesWithoutServiceName(t *testing.T) {
	td := loadR2Fixture(t, r2OR3b, func(ra pcommon.Map) { ra.PutStr("service.name", "edge-drain") })
	byID := convertR2(t, td, nil)
	for _, id := range []string{r2OR3bRoot, r2OR3bAttempt} {
		if got := byID[id]["observation_type"]; got != "chain" {
			t.Errorf("span %s observation_type = %v, want chain", id, got)
		}
	}
}

// G6: without a sender cost, anthropic/claude-haiku-4.5 has no exact price
// key and no same-provider bare key; OpenRouter's rate prices it
// (openrouter/anthropic/claude-haiku-4.5: 14*1e-6 + 9*5e-6).
func TestOpenRouterR2NoSenderCostPricedAtOpenRouterRate(t *testing.T) {
	pr := newRecordingPricer(t)
	byID := convertR2(t, loadR2Fixture(t, r2OR3b, nil), pr)
	gen := byID[r2OR3bGen]
	if got := gen["cost"].(float64); !near(got, 14*1e-6+9*5e-6) {
		t.Errorf("generation cost = %v, want 5.9e-05", got)
	}
	if !pr.askedAsOpenRouter("anthropic/claude-haiku-4.5") {
		t.Errorf("generation was not priced as OpenRouter traffic (asked %q)", pr.models)
	}
	for _, id := range []string{r2OR3bRoot, r2OR3bAttempt} {
		if c := byID[id]["cost"].(float64); c != 0 {
			t.Errorf("span %s cost = %v, want 0", id, c)
		}
	}
	for k, want := range map[string]int32{"prompt_tokens": 14, "completion_tokens": 9, "total_tokens": 23} {
		if got := gen[k].(int32); got != want {
			t.Errorf("generation %s = %d, want %d", k, got, want)
		}
	}
}

// OpenRouter's billed cost still wins over token pricing, the root and the
// attempts cost nothing, and the free call stays free.
func TestOpenRouterR2SenderCostStillWins(t *testing.T) {
	pr := newRecordingPricer(t)
	byID := convertR2(t, loadR2Fixture(t, r2OR1, nil), pr)
	if got := byID[r2OR1Gen]["cost"].(float64); got != 3.054e-05 {
		t.Errorf("generation cost = %v, want the sender's 3.054e-05", got)
	}
	for _, id := range []string{r2OR1Root, r2OR1Fail, r2OR1OK} {
		if c := byID[id]["cost"].(float64); c != 0 {
			t.Errorf("span %s cost = %v, want 0", id, c)
		}
	}
	if pr.asked("openai/gpt-4o-mini") {
		t.Errorf("pricer asked to price the generation (asked %q); the sender cost must win", pr.models)
	}
	if fail := byID[r2OR1Fail]; fail["status"] != "ERROR" {
		t.Errorf("failed attempt status = %v, want ERROR", fail["status"])
	}

	byID = convertR2(t, loadR2Fixture(t, r2OR5, nil), newRecordingPricer(t))
	if got := byID[r2OR5Gen]["cost"].(float64); got != 0 {
		t.Errorf("free generation cost = %v, want 0", got)
	}
}

// user.id links only for an Observe project: without project_type the
// converter leaves end_user_id NULL; with project_type "observe" (which
// auth.StampResourceAttrs now adds for an Observe project) the root and the
// generation, which both carry user.id, get the same end user. Sessions link
// either way.
func TestOpenRouterR2UserAndSession(t *testing.T) {
	wantSession := detid.TraceSessionID(orProjectID, "sess_r2_single_1").String()
	wantUser := detid.EndUserID(orProjectID, orOrgID, "user_r2_alice", "").String()

	byID := convertR2(t, loadR2Fixture(t, r2OR5, nil), nil)
	for _, id := range []string{r2OR5Root, r2OR5Gen} {
		if got := byID[id]["trace_session_id"]; got != wantSession {
			t.Errorf("span %s trace_session_id = %v, want %s", id, got, wantSession)
		}
		if got := byID[id]["end_user_id"]; got != nil {
			t.Errorf("span %s end_user_id = %v, want nil without project_type", id, got)
		}
	}

	td := loadR2Fixture(t, r2OR5, func(ra pcommon.Map) { ra.PutStr("project_type", "observe") })
	rows, ids, err := ConvertWithIdentities(context.Background(), td, nil)
	if err != nil {
		t.Fatal(err)
	}
	byID = rowsByID(t, rows)
	for _, id := range []string{r2OR5Root, r2OR5Gen} {
		if got := byID[id]["end_user_id"]; got != wantUser {
			t.Errorf("span %s end_user_id = %v, want %s", id, got, wantUser)
		}
	}
	if got := byID[r2OR5Attempt]["end_user_id"]; got != nil {
		t.Errorf("attempt end_user_id = %v, want nil (no user.id on the attempt)", got)
	}
	if eus := ids.EndUsers(); len(eus) != 1 || eus[0].UserID != "user_r2_alice" || eus[0].EndUserID != wantUser {
		t.Errorf("end users = %+v, want one user_r2_alice", eus)
	}

	td = loadR2Fixture(t, r2OR5, func(ra pcommon.Map) { ra.PutStr("project_type", "experiment") })
	byID = convertR2(t, td, nil)
	if got := byID[r2OR5Gen]["end_user_id"]; got != nil {
		t.Errorf("experiment project end_user_id = %v, want nil", got)
	}
}

// traceAI-shaped spans keep today's type and price: OpenRouter handling turns
// on only for an OpenRouter export or an "openrouter" provider.
func TestNonOpenRouterSpansUnchanged(t *testing.T) {
	tbl, err := pricing.LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	pr := pricing.New(tbl, nil)
	mini, _ := tbl.Cost("gpt-4o-mini", 120, 38)

	app := func(fill func(pcommon.Map)) ptrace.Traces {
		td := contentSpan(fill)
		td.ResourceSpans().At(0).Resource().Attributes().PutStr("service.name", "my-app")
		return td
	}
	for _, tc := range []struct {
		name     string
		td       ptrace.Traces
		wantType string
		wantCost float64
	}{
		{
			name:     "no kind, another sender: unknown",
			td:       app(func(m pcommon.Map) { m.PutStr("user.id", "u1") }),
			wantType: "unknown",
		},
		{
			name: "OpenRouter-style slug without an OpenRouter signal: unpriced as before",
			td: app(func(m pcommon.Map) {
				m.PutStr("fi.span.kind", "LLM")
				m.PutStr("gen_ai.provider.name", "anthropic")
				m.PutStr("gen_ai.request.model", "anthropic/claude-haiku-4.5")
				m.PutInt("gen_ai.usage.input_tokens", 14)
				m.PutInt("gen_ai.usage.output_tokens", 9)
			}),
			wantType: "llm",
		},
		{
			name: "plain gpt-4o-mini: priced as before",
			td: app(func(m pcommon.Map) {
				m.PutStr("fi.span.kind", "LLM")
				m.PutStr("llm.provider", "openai")
				m.PutStr("llm.model_name", "gpt-4o-mini")
				m.PutInt("llm.token_count.prompt", 120)
				m.PutInt("llm.token_count.completion", 38)
			}),
			wantType: "llm", wantCost: mini,
		},
		{
			name: "traceAI calling OpenRouter (llm.provider=openrouter): OpenRouter rate",
			td: app(func(m pcommon.Map) {
				m.PutStr("fi.span.kind", "LLM")
				m.PutStr("llm.provider", "openrouter")
				m.PutStr("llm.model_name", "anthropic/claude-haiku-4.5")
				m.PutInt("llm.token_count.prompt", 14)
				m.PutInt("llm.token_count.completion", 9)
			}),
			wantType: "llm", wantCost: 14*1e-6 + 9*5e-6,
		},
		{
			name: "openrouter provider does not retype a kindless traceAI span",
			td: app(func(m pcommon.Map) {
				m.PutStr("gen_ai.system", "OpenRouter")
			}),
			wantType: "unknown",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rows, _, err := ConvertWithIdentities(context.Background(), tc.td, pr)
			if err != nil {
				t.Fatal(err)
			}
			r := rows[0]
			if r["observation_type"] != tc.wantType {
				t.Errorf("observation_type = %v, want %s", r["observation_type"], tc.wantType)
			}
			if got := r["cost"].(float64); !near(got, tc.wantCost) {
				t.Errorf("cost = %v, want %v", got, tc.wantCost)
			}
		})
	}
}
