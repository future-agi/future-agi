package server

// CN22c (R-CN22c-1 and R-CN22c-3): a budgeted key's spend
// moves on every model call its scope admits, streamed or not. A streamed
// POST /v1/responses is priced from its response.completed event, and a
// stream that ends before the upstream reports usage (the client left, the
// upstream failed, a BYOK upstream that sends none) is priced on an estimate,
// so a client cannot spend past the key's budget by streaming or by dropping
// each stream before its usage arrives.

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	ks "github.com/futureagi/agentcc-gateway/internal/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	keyscopeplugin "github.com/futureagi/agentcc-gateway/internal/plugins/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

const (
	budgetStreamOrg  = "org-budget-stream"
	budgetStreamKey  = "sk-agentcc-budget-stream-key-0000000000001"
	budgetStreamKey2 = "sk-agentcc-budget-stream-key-0000000000002"
)

// budgetedResponsesUpstream is an OpenAI Responses upstream (named apart from
// responses_stream_pricing_test.go's responsesUpstream type). "completed"
// streams a response whose usage costs USD 1.25 at gpt-4o prices (the
// completed event written in two pieces, so the gateway reads it across
// reads); "cut" streams text deltas and closes before response.completed, as
// an upstream that failed or a call the client left does; "huge" streams the
// deltas, then a response.completed whose line is over the line limit the
// gateway reads usage within. A non-streamed call answers the same usage as
// "completed".
func budgetedResponsesUpstream(t *testing.T, mode string, calls *atomic.Int32) *httptest.Server {
	t.Helper()
	const usage = `"usage":{"input_tokens":100000,"output_tokens":100000,"total_tokens":200000}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.URL.Path != "/v1/responses" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		calls.Add(1)
		if !strings.Contains(string(body), `"stream":true`) {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":"resp_n","object":"response","model":"gpt-4o","output":[],%s}`, usage)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flush := w.(http.Flusher).Flush
		fmt.Fprint(w, "event: response.created\r\ndata: {\"type\":\"response.created\",\"response\":{\"id\":\"resp_s\",\"model\":\"gpt-4o\"}}\r\n\r\n")
		flush()
		for i := 0; i < 4; i++ {
			fmt.Fprintf(w, "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":%q}\n\n",
				strings.Repeat("word ", 100))
			flush()
		}
		if mode == "cut" {
			return
		}
		output := "[]"
		if mode == "huge" {
			// A last event over the line limit the gateway reads usage
			// within (release's maxResponsesUsageLineBytes), as a
			// response.completed carrying base64 images can be.
			output = `[{"type":"message","id":"msg_1","status":"completed","role":"assistant","content":[{"type":"output_text","text":"` +
				strings.Repeat("A", maxResponsesUsageLineBytes+1024) + `","annotations":[]}]}]`
		}
		completed := fmt.Sprintf("event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"id\":\"resp_s\",\"model\":\"gpt-4o\",\"output\":%s,%s}}\n\n", output, usage)
		half := len(completed) / 2
		fmt.Fprint(w, completed[:half])
		flush()
		time.Sleep(5 * time.Millisecond)
		fmt.Fprint(w, completed[half:])
		flush()
	}))
	t.Cleanup(srv.Close)
	return srv
}

// newBudgetedKeyServer is a gateway with the auth, cost and keyscope plugins
// and two budgeted, scoped keys of one org, calling upstreamURL as openai.
func newBudgetedKeyServer(t *testing.T, upstreamURL, budgetUSD string) (*Server, *ks.Spend) {
	t.Helper()
	srv, spend, _ := newBudgetedKeyServerProbed(t, upstreamURL, budgetUSD)
	return srv, spend
}

// unbudgetedStreamKey is a third key of the same org and scope with no
// budget (key_3 in newBudgetedKeyServerProbed): a control that keeps
// release's pricing.
const unbudgetedStreamKey = "sk-agentcc-budget-stream-key-0000000000003"

// newBudgetedKeyServerProbed is newBudgetedKeyServer with unbudgetedStreamKey
// added, and a probe (responses_stream_pricing_test.go's costProbe) of each
// call's cost, usage and usage_estimated after the cost plugin priced it.
func newBudgetedKeyServerProbed(t *testing.T, upstreamURL, budgetUSD string) (*Server, *ks.Spend, *costProbe) {
	t.Helper()
	meta := func() map[string]string {
		return map[string]string{"org_id": budgetStreamOrg, "scopes": "mcp,models",
			"budget_limit_usd": budgetUSD, "budget_period": "monthly"}
	}
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "budget-stream-admin-token-0123456789abcdef"
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "one", Key: budgetStreamKey, KeyType: "internal", Metadata: meta()},
		{Name: "two", Key: budgetStreamKey2, KeyType: "internal", Metadata: meta()},
		{Name: "three", Key: unbudgetedStreamKey, KeyType: "internal",
			Metadata: map[string]string{"org_id": budgetStreamOrg, "scopes": "mcp,models"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	mdb := testModelDBPtr()
	spend := ks.NewSpend(nil)
	probe := &costProbe{}
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), costplugin.New(true, mdb.Load, nil, nil),
		keyscopeplugin.New(keyStore, spend), probe)
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, metrics.NewRegistry(), mdb, nil, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv, spend, probe
}

func postAs(srv *Server, key, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func keySpent(spend *ks.Spend, keyID, budgetUSD string) int64 {
	b, _ := ks.ParseBudget(map[string]string{"budget_limit_usd": budgetUSD, "budget_period": "monthly"})
	return spend.Spent(budgetStreamOrg, keyID, b, time.Now())
}

// R-CN22c-1: a streamed /v1/responses call spends what a non-streamed one of
// the same usage spends, and a key past its budget is refused 402 before the
// upstream is called again.
func TestScopedKeyBudgetCountsStreamedResponses(t *testing.T) {
	var calls atomic.Int32
	up := budgetedResponsesUpstream(t, "completed", &calls)
	srv, spend := newBudgetedKeyServer(t, up.URL, "0.01")

	// Control: non-streamed.
	if w := postAs(srv, budgetStreamKey, "/v1/responses", `{"model":"gpt-4o","input":"hi"}`); w.Code != http.StatusOK {
		t.Fatalf("first non-streamed call: %d %s", w.Code, w.Body.String())
	}
	nonStreamed := keySpent(spend, "key_1", "0.01")
	if nonStreamed <= 0 {
		t.Fatalf("a non-streamed call spent %d micros", nonStreamed)
	}

	w := postAs(srv, budgetStreamKey2, "/v1/responses", `{"model":"gpt-4o","input":"hi","stream":true}`)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "response.completed") {
		t.Fatalf("first streamed call: %d %.200s", w.Code, w.Body.String())
	}
	streamed := keySpent(spend, "key_2", "0.01")
	if streamed != nonStreamed {
		t.Fatalf("a streamed call spent %d micros, the same non-streamed call %d", streamed, nonStreamed)
	}

	before := calls.Load()
	for i := 0; i < 3; i++ {
		w := postAs(srv, budgetStreamKey2, "/v1/responses", `{"model":"gpt-4o","input":"hi","stream":true}`)
		if w.Code != http.StatusPaymentRequired || !strings.Contains(w.Body.String(), ks.CodeBudgetExhausted) {
			t.Fatalf("streamed call %d past the budget: %d %.200s", i+2, w.Code, w.Body.String())
		}
	}
	if n := calls.Load() - before; n != 0 {
		t.Fatalf("calls refused for the budget reached the upstream %d times", n)
	}
}

// R-CN22c-1, the second half: a streamed /v1/responses call that ends before
// response.completed (the client left, or the upstream failed) still spends,
// on an estimate of what was sent and generated.
func TestScopedKeyBudgetCountsAStreamedResponseCutBeforeItsUsage(t *testing.T) {
	var calls atomic.Int32
	up := budgetedResponsesUpstream(t, "cut", &calls)
	srv, spend := newBudgetedKeyServer(t, up.URL, "0.000001")

	if w := postAs(srv, budgetStreamKey, "/v1/responses", `{"model":"gpt-4o","input":"hi","stream":true}`); w.Code != http.StatusOK {
		t.Fatalf("first streamed call: %d %.200s", w.Code, w.Body.String())
	}
	// 2000 characters of output at about 4 a token: 500 tokens, USD 0.005 at
	// gpt-4o's output price.
	if spent := keySpent(spend, "key_1", "0.000001"); spent < 4000 {
		t.Fatalf("a stream cut before its usage spent %d micros, want the estimate (about 5000)", spent)
	}
	w := postAs(srv, budgetStreamKey, "/v1/responses", `{"model":"gpt-4o","input":"hi","stream":true}`)
	if w.Code != http.StatusPaymentRequired {
		t.Fatalf("the next call: %d %.200s, want 402", w.Code, w.Body.String())
	}
}

// R-CN22c-3: a budgeted key's chat stream on a BYOK upstream that ends with no
// usage chunk is priced on an estimate, as a managed stream is, so the key's
// spend moves and the next call past the budget is refused.
func TestScopedKeyBudgetCountsABYOKChatStreamWithNoUsage(t *testing.T) {
	mock := startMockOpenAI(t) // streams content, then [DONE], with no usage chunk
	t.Cleanup(mock.Close)
	srv, spend := newBudgetedKeyServer(t, mock.URL, "0.000001")
	body := `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hello there"}]}`

	if w := postAs(srv, budgetStreamKey, "/v1/chat/completions", body); w.Code != http.StatusOK {
		t.Fatalf("first streamed chat: %d %.200s", w.Code, w.Body.String())
	}
	if spent := keySpent(spend, "key_1", "0.000001"); spent <= 0 {
		t.Fatalf("a BYOK stream with no usage chunk spent %d micros", spent)
	}
	if w := postAs(srv, budgetStreamKey, "/v1/chat/completions", body); w.Code != http.StatusPaymentRequired {
		t.Fatalf("the next streamed chat: %d %.200s, want 402", w.Code, w.Body.String())
	}
}

// MCP-APPS-PLAN adaptation (a), the CN22c estimate on release's
// /v1/responses settle path. A budgeted key's stream that ends with no usage
// the gateway can read is priced on an estimate: the prompt as release's hold
// estimates it (billing.EstimateInputTokens over the body read in the chat
// shape) and the deltas the stream sent at about 4 characters a token,
// marked usage_estimated. That covers a stream cut before its last event and
// one whose last event is over the line limit release reads usage within: on
// the org's own provider neither is prepaid credit (no hold), so release
// leaves both unpriced, and a key could spend past its budget either way.
func TestScopedKeyBudgetEstimatesAStreamedResponseWithNoReadableUsage(t *testing.T) {
	const body = `{"model":"gpt-4o","instructions":"Answer in one short paragraph.","input":"Describe a cat.","stream":true}`
	req, _ := requestview.Responses([]byte(body)).ChatRequest(context.Background())
	wantPrompt := billing.EstimateInputTokens(req)
	if wantPrompt == 0 {
		t.Fatal("set-up: the hold's prompt estimate is 0")
	}
	for _, mode := range []string{"cut", "huge"} {
		t.Run(mode, func(t *testing.T) {
			var calls atomic.Int32
			up := budgetedResponsesUpstream(t, mode, &calls)
			srv, spend, probe := newBudgetedKeyServerProbed(t, up.URL, "0.000001")

			if w := postAs(srv, budgetStreamKey, "/v1/responses", body); w.Code != http.StatusOK {
				t.Fatalf("first streamed call: %d %.200s", w.Code, w.Body.String())
			}
			run := probe.last(t, 1)
			// 4 deltas of 500 characters: 500 output tokens.
			if run.usage == nil || run.usage.PromptTokens != wantPrompt || run.usage.CompletionTokens != 500 ||
				run.usage.TotalTokens != wantPrompt+500 {
				t.Fatalf("usage %+v, want %d prompt and 500 completion tokens", run.usage, wantPrompt)
			}
			if run.usageEstimated != "true" || run.usageUnreadable != "" || run.cost == "" {
				t.Fatalf("usage_estimated %q, usage_unreadable %q, cost %q; want an estimate, priced",
					run.usageEstimated, run.usageUnreadable, run.cost)
			}
			if spent := keySpent(spend, "key_1", "0.000001"); spent < 5000 {
				t.Fatalf("the key spent %d micros, want the estimate's cost (500 tokens at gpt-4o's output price, 5000)", spent)
			}
			before := calls.Load()
			if w := postAs(srv, budgetStreamKey, "/v1/responses", body); w.Code != http.StatusPaymentRequired {
				t.Fatalf("the next call: %d %.200s, want 402", w.Code, w.Body.String())
			}
			if n := calls.Load() - before; n != 0 {
				t.Fatalf("a call refused for the budget reached the upstream %d times", n)
			}
		})
	}
}

// Controls: a key with no budget keeps release's pricing. Its /v1/responses
// stream cut before its last event, or whose last event is over the line
// limit, stays unpriced on the org's own provider (release F3's Known
// limits), and its chat stream with no usage chunk is not estimated (chat
// estimates only a managed stream), so nothing changes for a key without a
// budget.
func TestUnbudgetedKeyStreamsWithNoUsageKeepReleasePricing(t *testing.T) {
	const body = `{"model":"gpt-4o","input":"hi","stream":true}`
	for _, mode := range []string{"cut", "huge"} {
		t.Run("responses "+mode, func(t *testing.T) {
			var calls atomic.Int32
			up := budgetedResponsesUpstream(t, mode, &calls)
			srv, _, probe := newBudgetedKeyServerProbed(t, up.URL, "0.000001")
			if w := postAs(srv, unbudgetedStreamKey, "/v1/responses", body); w.Code != http.StatusOK {
				t.Fatalf("streamed call: %d %.200s", w.Code, w.Body.String())
			}
			if run := probe.last(t, 1); run.usage != nil || run.usageEstimated != "" || run.cost != "" {
				t.Fatalf("usage %+v, usage_estimated %q, cost %q; want none", run.usage, run.usageEstimated, run.cost)
			}
		})
	}
	t.Run("chat", func(t *testing.T) {
		mock := startMockOpenAI(t) // no usage chunk
		t.Cleanup(mock.Close)
		srv, _, probe := newBudgetedKeyServerProbed(t, mock.URL, "0.000001")
		chat := `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hello there"}]}`
		if w := postAs(srv, unbudgetedStreamKey, "/v1/chat/completions", chat); w.Code != http.StatusOK {
			t.Fatalf("streamed chat: %d %.200s", w.Code, w.Body.String())
		}
		if run := probe.last(t, 1); run.usage != nil || run.usageEstimated != "" || run.cost != "" {
			t.Fatalf("usage %+v, usage_estimated %q, cost %q; want none", run.usage, run.usageEstimated, run.cost)
		}
	})
}

// R-CN22c-3 on release's chat settle path (finalizeStream): the budgeted
// key's BYOK chat stream with no usage chunk is priced on the estimate a
// managed stream gets (the messages, and the output at about 4 characters a
// token), marked usage_estimated.
func TestScopedKeyBudgetChatStreamEstimateIsMarked(t *testing.T) {
	mock := startMockOpenAI(t) // "Hello! How can I help you?", no usage chunk
	t.Cleanup(mock.Close)
	srv, _, probe := newBudgetedKeyServerProbed(t, mock.URL, "1")
	chat := `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hello there"}]}`
	if w := postAs(srv, budgetStreamKey, "/v1/chat/completions", chat); w.Code != http.StatusOK {
		t.Fatalf("streamed chat: %d %.200s", w.Code, w.Body.String())
	}
	run := probe.last(t, 1)
	wantOut := (len("Hello! How can I help you?") + 3) / 4
	if run.usage == nil || run.usage.CompletionTokens != wantOut || run.usage.PromptTokens == 0 ||
		run.usageEstimated != "true" || run.cost == "" {
		t.Fatalf("usage %+v, usage_estimated %q, cost %q; want the estimate (%d output tokens), priced",
			run.usage, run.usageEstimated, run.cost, wantOut)
	}
}
