package cost

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	feedata "github.com/futureagi/agentcc-gateway/catalog/servertools"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/servertools"
)

// shippedFees is the fee table the binary ships (catalog/servertools/fees.json).
func shippedFees(t *testing.T) servertools.FeeTable {
	t.Helper()
	fees, err := feedata.Fees()
	if err != nil {
		t.Fatal(err)
	}
	return fees
}

// toolRC is a served chat request on provider/model whose usage reports
// prompt and completion tokens and the server tools in stu, under
// billingMode (managed or byok), with one recorded attempt.
func toolRC(provider, model, billingMode string, prompt, completion int, stu *models.ServerToolUsage) *models.RequestContext {
	rc := &models.RequestContext{
		RequestID: "req-v2",
		Model:     model,
		Provider:  provider,
		Request:   &models.ChatCompletionRequest{Model: model},
		Response: &models.ChatCompletionResponse{Usage: &models.Usage{
			PromptTokens: prompt, CompletionTokens: completion, TotalTokens: prompt + completion, ServerToolUse: stu,
		}},
		Metadata: map[string]string{"billing_mode": billingMode},
		Timings:  map[string]time.Duration{},
	}
	cred := models.CredentialBYOK
	if billingMode == models.BillingModeManaged {
		cred = models.CredentialManaged
	}
	rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess,
		Provider: provider, Model: model, CredentialType: cred})
	return rc
}

// cardPlugin prices every model on card (as the injected catalog lookup
// would), with a 5% managed markup.
func cardPlugin(card models.PriceCard) *Plugin {
	db := managedDB()
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.SetManagedMarkup(5)
	p.SetPricer(NewPricer(func() *modeldb.ModelDB { return db }, fixedLookup(card)))
	return p
}

func servedAttempt(t *testing.T, rc *models.RequestContext) models.Attempt {
	t.Helper()
	got := rc.AttemptsSnapshot()
	if len(got) != 1 {
		t.Fatalf("attempts = %d, want 1", len(got))
	}
	return got[0]
}

// An Anthropic response reporting web_search_requests=3 bills $0.03 on top
// of its tokens: 1,000 input at $3/M and 100 output at $15/M are 4,500
// micros, so the attempt's upstream cost is 34,500.
func TestAnthropicWebSearchBilled(t *testing.T) {
	rc := toolRC("anthropic", "claude-sonnet-5.5", "byok", 1000, 100, &models.ServerToolUsage{WebSearchRequests: 3})
	managedPlugin(nil).ProcessResponse(context.Background(), rc)

	a := servedAttempt(t, rc)
	if a.UpstreamCostMicros != 34_500 || rc.Billing.UpstreamMicros != 34_500 {
		t.Fatalf("upstream = %d (request %d), want 34,500: 4,500 of tokens plus $0.03 of searches", a.UpstreamCostMicros, rc.Billing.UpstreamMicros)
	}
	if rc.Metadata["cost"] != "0.034500" {
		t.Errorf("cost = %q, want 0.034500", rc.Metadata["cost"])
	}

	// The same response without searches is the tokens alone.
	plain := toolRC("anthropic", "claude-sonnet-5.5", "byok", 1000, 100, nil)
	managedPlugin(nil).ProcessResponse(context.Background(), plain)
	if got := servedAttempt(t, plain).UpstreamCostMicros; got != 4_500 {
		t.Fatalf("tokens alone = %d, want 4,500", got)
	}
}

// openAIResponsesBody is a recorded-shape Responses API body: two
// web_search_call items made with the web_search tool, and the message.
const openAIResponsesBody = `{
  "id": "resp_v2", "object": "response", "model": "%s",
  "tools": [{"type": "web_search", "search_context_size": "medium"}],
  "output": [
    {"type": "web_search_call", "id": "ws_1", "status": "completed", "action": {"type": "search", "query": "a"}},
    {"type": "web_search_call", "id": "ws_2", "status": "completed", "action": {"type": "search", "query": "b"}},
    {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": "ok", "annotations": []}]}
  ],
  "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
}`

func responsesUsage(t *testing.T, model string) *models.ServerToolUsage {
	t.Helper()
	body := []byte(fmt.Sprintf(openAIResponsesBody, model))
	u := openai.ResponsesServerToolUsage(body)
	if u == nil {
		t.Fatal("no server-tool usage read from the Responses body")
	}
	return u
}

// An OpenAI Responses body with two web_search_call items bills $0.02.
func TestOpenAIWebSearchCallBilled(t *testing.T) {
	u := responsesUsage(t, "gpt-4.1")
	if u.WebSearchRequests != 2 {
		t.Fatalf("web_search_call items = %d, want 2", u.WebSearchRequests)
	}
	rc := toolRC("openai", "gpt-4.1", "byok", 0, 0, u)
	rc.EndpointType = "responses"
	cardPlugin(models.PriceCard{Prompt: 2e-6, Completion: 8e-6, Source: models.PriceSourceCatalog}).ProcessResponse(context.Background(), rc)
	if got := servedAttempt(t, rc).UpstreamCostMicros; got != 20_000 {
		t.Fatalf("upstream = %d, want 20,000 ($0.02 for two calls)", got)
	}
}

// On gpt-4o-mini and gpt-4.1-mini each search call adds a fixed block of
// 8,000 input tokens at the model's input price: one call at $0.15/M input
// is $0.01 + 8,000 × $0.15/M = 11,200 micros.
func TestOpenAIMiniFixedBlock(t *testing.T) {
	card := models.PriceCard{Prompt: 0.15e-6, Completion: 0.6e-6, Source: models.PriceSourceCatalog}
	u := &models.ServerToolUsage{WebSearchRequests: 1}
	rc := toolRC("openai", "gpt-4o-mini", "byok", 0, 0, u)
	cardPlugin(card).ProcessResponse(context.Background(), rc)
	if got := servedAttempt(t, rc).UpstreamCostMicros; got != 11_200 {
		t.Fatalf("mini upstream = %d, want 11,200: $0.01 plus 8,000 input tokens", got)
	}
	// gpt-4.1 has no block: the call alone.
	full := toolRC("openai", "gpt-4.1", "byok", 0, 0, &models.ServerToolUsage{WebSearchRequests: 1})
	cardPlugin(card).ProcessResponse(context.Background(), full)
	if got := servedAttempt(t, full).UpstreamCostMicros; got != 10_000 {
		t.Fatalf("gpt-4.1 upstream = %d, want 10,000", got)
	}
	// The Responses parser routes a mini model's calls to the same row.
	if got := responsesUsage(t, "gpt-4o-mini").WebSearchRequests; got != 2 {
		t.Fatalf("mini web_search_call items = %d, want 2", got)
	}
}

// Gemini 3 and later bill each search query: four queries are $0.056,
// whatever the number of grounded prompts.
func TestGemini3QueriesBilled(t *testing.T) {
	card := models.PriceCard{Prompt: 2e-6, Completion: 12e-6, Source: models.PriceSourceCatalog}
	rc := toolRC("gemini", "gemini-3-pro", "byok", 0, 0, &models.ServerToolUsage{WebSearchQueries: 4, GroundedPrompts: 1})
	cardPlugin(card).ProcessResponse(context.Background(), rc)
	if got := servedAttempt(t, rc).UpstreamCostMicros; got != 56_000 {
		t.Fatalf("upstream = %d, want 56,000 ($0.014 × 4 queries)", got)
	}
	// A vendor-prefixed id of a later generation takes the same rate.
	later := toolRC("openrouter", "google/gemini-3.1-flash", "byok", 0, 0, &models.ServerToolUsage{WebSearchQueries: 1, GroundedPrompts: 1})
	cardPlugin(card).ProcessResponse(context.Background(), later)
	if got := servedAttempt(t, later).UpstreamCostMicros; got != 14_000 {
		t.Fatalf("gemini-3.1 upstream = %d, want 14,000", got)
	}
}

// Gemini 2.5 bills the grounded prompt, $0.035, however many queries it ran.
func TestGemini25GroundedPrompts(t *testing.T) {
	card := models.PriceCard{Prompt: 0.3e-6, Completion: 2.5e-6, Source: models.PriceSourceCatalog}
	rc := toolRC("gemini", "gemini-2.5-flash", "byok", 0, 0, &models.ServerToolUsage{WebSearchQueries: 3, GroundedPrompts: 1})
	cardPlugin(card).ProcessResponse(context.Background(), rc)
	if got := servedAttempt(t, rc).UpstreamCostMicros; got != 35_000 {
		t.Fatalf("upstream = %d, want 35,000 (one grounded prompt)", got)
	}
	// Maps grounding on 2.5 is $0.025 a grounded prompt.
	maps := toolRC("gemini", "gemini-2.5-pro", "byok", 0, 0, &models.ServerToolUsage{MapsGroundedPrompts: 1})
	cardPlugin(card).ProcessResponse(context.Background(), maps)
	if got := servedAttempt(t, maps).UpstreamCostMicros; got != 25_000 {
		t.Fatalf("maps upstream = %d, want 25,000", got)
	}
}

// Anthropic web fetch costs nothing beyond tokens: two fetches leave the
// attempt at its token price, and no estimate is claimed.
func TestWebFetchFree(t *testing.T) {
	up, lines := PriceServerTools(&models.ServerToolUsage{WebFetchRequests: 2}, "anthropic", "claude-sonnet-5.5", shippedFees(t))
	if up != 0 || len(lines) != 1 || lines[0].Tool != servertools.WebFetch || lines[0].Count != 2 || lines[0].Unpriced {
		t.Fatalf("PriceServerTools = %d, %+v; want 0 with one priced web_fetch line of 2", up, lines)
	}
	rc := toolRC("anthropic", "claude-sonnet-5.5", "byok", 1000, 100, &models.ServerToolUsage{WebFetchRequests: 2})
	managedPlugin(nil).ProcessResponse(context.Background(), rc)
	a := servedAttempt(t, rc)
	if a.UpstreamCostMicros != 4_500 || a.UsageEstimated {
		t.Fatalf("attempt = %d (estimated %v), want 4,500 and not estimated", a.UpstreamCostMicros, a.UsageEstimated)
	}
}

// OpenAI containers cost their size's session price per 20 minutes, billed
// by the minute: a 16 GB container for 30 minutes is $0.48 / 20 × 30 =
// $0.72, a 1 GB one for 20 minutes $0.03.
func TestOpenAIContainerSessionBilled(t *testing.T) {
	fees := shippedFees(t)
	up, lines := PriceServerTools(&models.ServerToolUsage{Containers: []models.ServerToolContainer{
		{ID: "cntr_a", MemoryGB: 16, Minutes: 30},
		{ID: "cntr_b", MemoryGB: 1, Minutes: 20},
	}}, "openai", "gpt-5", fees)
	if up != 750_000 {
		t.Fatalf("upstream = %d, want 750,000 ($0.72 + $0.03)", up)
	}
	if len(lines) != 2 || lines[0].Micros != 720_000 || lines[1].Micros != 30_000 || lines[0].Estimated || lines[1].Estimated {
		t.Fatalf("lines = %+v", lines)
	}
	// The Responses parser reads the container a code interpreter call ran
	// in, and its size from the tool's memory_limit.
	u := openai.ResponsesServerToolUsage([]byte(`{"model":"gpt-5","tools":[{"type":"code_interpreter","container":{"type":"auto","memory_limit":"4g"}}],
		"output":[{"type":"code_interpreter_call","id":"ci_1","container_id":"cntr_9","code":"1+1"},
		          {"type":"code_interpreter_call","id":"ci_2","container_id":"cntr_9","code":"2+2"}]}`))
	if u == nil || len(u.Containers) != 1 || u.Containers[0].ID != "cntr_9" || u.Containers[0].MemoryGB != 4 {
		t.Fatalf("containers = %+v, want the one 4 GB container", u)
	}
}

// A 4 GB container used for 3 minutes bills the 5-minute minimum: $0.12 /
// 20 × 5 = $0.03. An OpenAI response reports no duration, so a container
// it names is billed that minimum as an estimate.
func TestOpenAIContainerFiveMinuteMinimum(t *testing.T) {
	fees := shippedFees(t)
	up, lines := PriceServerTools(&models.ServerToolUsage{Containers: []models.ServerToolContainer{{ID: "c", MemoryGB: 4, Minutes: 3}}}, "openai", "gpt-5", fees)
	if up != 30_000 || len(lines) != 1 || lines[0].Minutes != 5 || lines[0].Estimated {
		t.Fatalf("3 minutes = %d, %+v; want 30,000 for the 5-minute minimum", up, lines)
	}
	up, lines = PriceServerTools(&models.ServerToolUsage{Containers: []models.ServerToolContainer{{ID: "c", MemoryGB: 4}}}, "openai", "gpt-5", fees)
	if up != 30_000 || !lines[0].Estimated {
		t.Fatalf("unknown duration = %d, %+v; want the 30,000 minimum, estimated", up, lines)
	}
	// An unknown size is estimated at the smallest container.
	up, lines = PriceServerTools(&models.ServerToolUsage{Containers: []models.ServerToolContainer{{ID: "c", Minutes: 20}}}, "openai", "gpt-5", fees)
	if up != 30_000 || !lines[0].Estimated {
		t.Fatalf("unknown size = %d, %+v; want the 1 GB price, estimated", up, lines)
	}
}

// Anthropic code execution is $0.05 a container-hour with a 5-minute
// minimum. The response names its container but no duration, so the
// minimum is billed as an upper bound and the attempt is an estimate:
// $0.05 / 60 × 5 = 4,167 micros, managed or not.
func TestAnthropicCodeExecutionBilled(t *testing.T) {
	stu := &models.ServerToolUsage{CodeExecutionRequests: 2, Containers: []models.ServerToolContainer{{ID: "container_011"}}}
	up, lines := PriceServerTools(stu, "anthropic", "claude-sonnet-5.5", shippedFees(t))
	if up != 4_167 || len(lines) != 1 || lines[0].Tool != servertools.CodeExecution || lines[0].Minutes != 5 || !lines[0].Estimated {
		t.Fatalf("PriceServerTools = %d, %+v; want 4,167 for one container's 5-minute minimum, estimated", up, lines)
	}
	// A code-execution count with no container object is one container.
	if up, _ := PriceServerTools(&models.ServerToolUsage{CodeExecutionRequests: 1}, "anthropic", "claude-sonnet-5.5", shippedFees(t)); up != 4_167 {
		t.Fatalf("count without a container = %d, want 4,167", up)
	}

	p := billingPlugin(fixedLookup(models.PriceCard{Prompt: 3e-6, Completion: 15e-6, Source: models.PriceSourcePriceCard}))
	rc := toolRC("direct", "claude-sonnet-5.5", "managed", 0, 0, stu)
	p.ProcessResponse(context.Background(), rc)
	a := servedAttempt(t, rc)
	if a.UpstreamCostMicros != 4_167 || a.BilledCostMicros != 4_375 || !a.UsageEstimated || !rc.Billing.UsageEstimated {
		t.Fatalf("managed attempt = upstream %d billed %d estimated %v; want 4,167, 4,375 (5%%), estimated",
			a.UpstreamCostMicros, a.BilledCostMicros, a.UsageEstimated)
	}
}

// Code execution is free when the request also carries web_search_20260209
// or web_fetch_20260209 or later (it runs their dynamic filtering): the
// searches are billed, the execution is not.
func TestAnthropicCodeExecutionFreeWithNewSearch(t *testing.T) {
	stu := &models.ServerToolUsage{WebSearchRequests: 2, CodeExecutionRequests: 1, Containers: []models.ServerToolContainer{{ID: "container_1"}}}
	tools := func(types ...string) *models.ChatCompletionRequest {
		req := &models.ChatCompletionRequest{}
		for _, typ := range types {
			var tool models.Tool
			if err := json.Unmarshal([]byte(`{"type":"`+typ+`","name":"x"}`), &tool); err != nil {
				t.Fatal(err)
			}
			req.Tools = append(req.Tools, tool)
		}
		return req
	}
	fees := shippedFees(t)
	for _, tc := range []struct {
		name  string
		req   *models.ChatCompletionRequest
		total int64
	}{
		{"web_search_20260209", tools("web_search_20260209", "code_execution_20260521"), 20_000},
		{"web_fetch_20260318", tools("web_fetch_20260318"), 20_000},
		{"web_search_20250305 only", tools("web_search_20250305", "code_execution_20250825"), 24_167},
		{"no tools", nil, 24_167},
	} {
		up, lines := PriceServerTools(stu, "anthropic", "claude-sonnet-5.5", fees.ForRequest(tc.req))
		if up != tc.total {
			t.Errorf("%s: upstream = %d, want %d (%+v)", tc.name, up, tc.total, lines)
		}
	}

	rc := toolRC("anthropic", "claude-sonnet-5.5", "byok", 0, 0, stu)
	rc.Request = tools("web_search_20260209", "code_execution_20260521")
	managedPlugin(nil).ProcessResponse(context.Background(), rc)
	if got := servedAttempt(t, rc).UpstreamCostMicros; got != 20_000 {
		t.Fatalf("request with the new search = %d, want 20,000 (searches only)", got)
	}
}

// A charge the upstream reports wins, as for tokens: the fee is already in it,
// so nothing is added.
func TestUpstreamReportedWins(t *testing.T) {
	p := billingPlugin(fixedLookup(models.PriceCard{Prompt: 1e-6, Source: models.PriceSourceCatalog}))
	rc := toolRC("openrouter", "anthropic/claude-sonnet-5.5", "managed", 1000, 100, &models.ServerToolUsage{WebSearchRequests: 3})
	rc.Response.Usage.Cost = usd(0.05)
	p.ProcessResponse(context.Background(), rc)
	a := servedAttempt(t, rc)
	if a.UpstreamCostMicros != 50_000 || a.CostSource != models.CostSourceProviderReported || a.UsageEstimated {
		t.Fatalf("attempt = upstream %d source %s estimated %v; want the reported 50,000", a.UpstreamCostMicros, a.CostSource, a.UsageEstimated)
	}
}

// A BYOK attempt's server-tool fee is the gateway's estimate of what the
// customer's own upstream account is charged: it is in the attempt's
// upstream cost, marked usage_estimated, and billed nothing.
func TestByokFeeIsEstimate(t *testing.T) {
	rc := toolRC("anthropic", "claude-sonnet-5.5", "byok", 1000, 100, &models.ServerToolUsage{WebSearchRequests: 3})
	managedPlugin(nil).ProcessResponse(context.Background(), rc)
	a := servedAttempt(t, rc)
	if a.CredentialType != models.CredentialBYOK || a.UpstreamCostMicros != 34_500 || !a.UsageEstimated {
		t.Fatalf("attempt = %+v; want a BYOK upstream estimate of 34,500, usage_estimated", a)
	}
	if b := rc.Billing; b.BilledMicros != 0 || b.UpstreamMicros != 34_500 || !b.UsageEstimated {
		t.Fatalf("billing = %+v; want nothing billed, the estimate upstream", b)
	}
	// Without a fee, a BYOK attempt is not an estimate.
	plain := toolRC("anthropic", "claude-sonnet-5.5", "byok", 1000, 100, nil)
	managedPlugin(nil).ProcessResponse(context.Background(), plain)
	if servedAttempt(t, plain).UsageEstimated {
		t.Fatal("a BYOK attempt with no server-tool fee was marked estimated")
	}
}

// A managed attempt bills its server-tool fee with the markup:
// PriceServerTools prices three Anthropic searches at 30,000 upstream
// micros, and at 500 bps they bill 30,000 × 10,500 / 10,000 = 31,500.
func TestManagedServerToolFeeWithMarkup(t *testing.T) {
	stu := &models.ServerToolUsage{WebSearchRequests: 3}
	up, lines := PriceServerTools(stu, "anthropic", "claude-sonnet-5.5", shippedFees(t))
	if up != 30_000 || len(lines) != 1 || lines[0].Micros != 30_000 || lines[0].Count != 3 {
		t.Fatalf("PriceServerTools = %d, %+v; want 30,000 for three searches", up, lines)
	}
	p := billingPlugin(fixedLookup(models.PriceCard{Prompt: 3e-6, Source: models.PriceSourcePriceCard}))
	rc := toolRC("direct", "claude-sonnet-5.5", "managed", 0, 0, stu)
	p.ProcessResponse(context.Background(), rc)
	a := servedAttempt(t, rc)
	if a.UpstreamCostMicros != 30_000 || a.BilledCostMicros != 31_500 || a.UsageEstimated {
		t.Fatalf("attempt = upstream %d billed %d estimated %v; want 30,000 billed 31,500", a.UpstreamCostMicros, a.BilledCostMicros, a.UsageEstimated)
	}
	if b := rc.Billing; b.BilledMicros != 31_500 || b.MarkupMicros != 1_500 || rc.Metadata["cost"] != "0.031500" {
		t.Fatalf("billing = %+v, cost %q; want 31,500 billed with 1,500 markup", b, rc.Metadata["cost"])
	}
}

// A response with Gemini Grounded Results is kept out of evals: the
// request's log evidence carries eval_export=forbidden, which the request
// log writes as its top-level eval_export. Ungrounded responses set nothing.
func TestGeminiGroundingSetsEvalExportForbidden(t *testing.T) {
	card := models.PriceCard{Prompt: 2e-6, Completion: 12e-6, Source: models.PriceSourceCatalog}
	rc := toolRC("gemini", "gemini-3-pro", "byok", 10, 10, &models.ServerToolUsage{WebSearchQueries: 2, GroundedPrompts: 1})
	cardPlugin(card).ProcessResponse(context.Background(), rc)
	if got := string(rc.LogEvidence["eval_export"]); got != `"forbidden"` {
		t.Fatalf("eval_export evidence = %q, want \"forbidden\"", got)
	}
	if got := rc.SnapshotV2().LogEvidence["eval_export"]; string(got) != `"forbidden"` {
		t.Fatalf("the logging plugin's snapshot carries %q", got)
	}
	// Maps grounding is a Grounded Result too.
	maps := toolRC("gemini", "gemini-2.5-pro", "byok", 10, 10, &models.ServerToolUsage{MapsGroundedPrompts: 1})
	cardPlugin(card).ProcessResponse(context.Background(), maps)
	if string(maps.LogEvidence["eval_export"]) != `"forbidden"` {
		t.Fatal("maps grounding left eval_export unset")
	}
	for _, stu := range []*models.ServerToolUsage{nil, {WebSearchRequests: 1}} {
		plain := toolRC("anthropic", "claude-sonnet-5.5", "byok", 10, 10, stu)
		managedPlugin(nil).ProcessResponse(context.Background(), plain)
		if _, set := plain.LogEvidence["eval_export"]; set {
			t.Fatalf("server tools %+v set eval_export", stu)
		}
	}
}

// A card that already prices web searches (the catalog's per-search price)
// bills them once: through the card, not again from the fee table.
func TestCardWebSearchPriceNotBilledTwice(t *testing.T) {
	card := models.PriceCard{Prompt: 1e-6, WebSearch: 0.004, Source: models.PriceSourceCatalog}
	rc := toolRC("anthropic", "claude-sonnet-5.5", "byok", 0, 0, &models.ServerToolUsage{WebSearchRequests: 3, WebFetchRequests: 1})
	cardPlugin(card).ProcessResponse(context.Background(), rc)
	if got := servedAttempt(t, rc).UpstreamCostMicros; got != 12_000 {
		t.Fatalf("upstream = %d, want 12,000 (3 × the card's $0.004, once)", got)
	}
}

// A card that prices the searches of a mini model still leaves the fixed
// input block to the fee table: two calls on gpt-4o-mini are the card's
// 2 × $0.004 (8,000 micros) plus 16,000 input tokens at $0.15/M (2,400),
// never the fee row's $0.01 a call on top.
func TestCardWebSearchPriceKeepsMiniFixedBlock(t *testing.T) {
	card := models.PriceCard{Prompt: 0.15e-6, Completion: 0.6e-6, WebSearch: 0.004, Source: models.PriceSourceCatalog}
	rc := toolRC("openai", "gpt-4o-mini", "byok", 0, 0, responsesUsage(t, "gpt-4o-mini"))
	rc.EndpointType = "responses"
	cardPlugin(card).ProcessResponse(context.Background(), rc)
	if got := servedAttempt(t, rc).UpstreamCostMicros; got != 10_400 {
		t.Fatalf("upstream = %d, want 10,400: the card's two searches plus the 16,000-token block", got)
	}
}

// An attempt that cannot be priced stays unpriced: a fee alone is not a
// price for the call. A count no fee row prices bills nothing and is
// reported unpriced.
func TestServerToolFeesNeverPriceAnUnpricedAttempt(t *testing.T) {
	rc := toolRC("anthropic", "no-such-model", "byok", 10, 10, &models.ServerToolUsage{WebSearchRequests: 3})
	managedPlugin(nil).ProcessResponse(context.Background(), rc)
	a := servedAttempt(t, rc)
	if a.UpstreamCostMicros != 0 || a.CostSource != models.CostSourceUnpriced {
		t.Fatalf("attempt = upstream %d source %s; want unpriced", a.UpstreamCostMicros, a.CostSource)
	}
	up, lines := PriceServerTools(&models.ServerToolUsage{WebSearchRequests: 3}, "", "mystery-1", shippedFees(t))
	if up != 0 || len(lines) != 1 || !lines[0].Unpriced {
		t.Fatalf("unknown vendor = %d, %+v; want one unpriced line", up, lines)
	}
}
