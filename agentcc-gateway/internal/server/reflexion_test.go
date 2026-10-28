package server

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	cachepkg "github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	rateplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Deterministic upstream responses and check fixtures exercise the real HTTP
// handler, OpenAI adapter, policy engine, pipeline, accounting and cache. Native
// leakage/PII checks are covered separately in reflexion_compat_test.go.
type reflectionUpstream struct {
	mu                    sync.Mutex
	requests              []models.ChatCompletionRequest
	blocked               int
	blockedText           string
	status                int
	tool                  bool
	multi                 bool
	responseModel         string
	usage                 *models.Usage
	repairError           bool
	repairStreamError     string
	responseFailure       string
	repairResponseFailure string
}

func (u *reflectionUpstream) serve(w http.ResponseWriter, r *http.Request) {
	var req models.ChatCompletionRequest
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		http.Error(w, "invalid request", 400)
		return
	}
	u.mu.Lock()
	u.requests = append(u.requests, req)
	n := len(u.requests)
	u.mu.Unlock()
	if u.status != 0 {
		http.Error(w, "upstream failure", u.status)
		return
	}
	if u.repairError && n > 1 {
		messages, _ := json.Marshal(req.Messages)
		models.WriteError(w, &models.APIError{Status: 400, Type: "private-error-type: " + string(messages), Code: "private-error-code: " + string(messages), Message: string(messages)})
		return
	}
	model := req.Model
	if u.responseModel != "" {
		model = u.responseModel
	}
	text := "accepted answer"
	if n <= u.blocked {
		text = "rejected secret answer"
		if u.blockedText != "" {
			text = u.blockedText
		}
	}
	content, _ := json.Marshal(text)
	usage := &models.Usage{PromptTokens: 10, CompletionTokens: 20, TotalTokens: 30}
	if u.usage != nil {
		usage = u.usage
	}
	if req.Stream {
		w.Header().Set("Content-Type", "text/event-stream")
		finish := "stop"
		chunks := []models.StreamChunk{
			{ID: "candidate", Object: "chat.completion.chunk", Model: model, Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Role: "assistant", Content: &text}}}},
			{ID: "candidate", Object: "chat.completion.chunk", Model: model, Choices: []models.StreamChoice{{Index: 0, FinishReason: &finish}}},
			{ID: "candidate", Object: "chat.completion.chunk", Model: model, Choices: []models.StreamChoice{}, Usage: usage},
		}
		if u.repairStreamError != "" && n > 1 {
			chunks = chunks[:2]
			messages, _ := json.Marshal(req.Messages)
			private, _ := json.Marshal("STREAM_PRIVATE_SENTINEL: " + string(messages))
			chunks[1].Extra = map[string]json.RawMessage{"detail": private}
			if strings.Contains(u.repairStreamError, "envelope") {
				chunks[1].Extra["error"] = json.RawMessage(`{"message":` + string(private) + `}`)
			}
			if strings.Contains(u.repairStreamError, "finish") {
				finish = "error"
			}
		}
		for _, chunk := range chunks {
			b, _ := json.Marshal(chunk)
			fmt.Fprintf(w, "data: %s\n\n", b)
			w.(http.Flusher).Flush()
		}
		fmt.Fprint(w, "data: [DONE]\n\n")
		return
	}
	m := models.Message{Role: "assistant", Content: content}
	if u.tool {
		m.ToolCalls = []models.ToolCall{{ID: "call-1", Type: "function", Function: models.FunctionCall{Name: "lookup", Arguments: `{}`}}}
	}
	choices := []models.Choice{{Index: 0, Message: m, FinishReason: "stop"}}
	if u.multi {
		choices = append(choices, models.Choice{Index: 1, Message: m, FinishReason: "stop"})
	}
	response := models.ChatCompletionResponse{ID: "candidate", Object: "chat.completion", Model: model, Choices: choices, Usage: usage}
	failure := u.responseFailure
	if n > 1 && u.repairResponseFailure != "" {
		failure = u.repairResponseFailure
	}
	if failure != "" {
		messages, _ := json.Marshal(req.Messages)
		private, _ := json.Marshal("NONSTREAM_PRIVATE_SENTINEL: " + string(messages))
		response.Extra = map[string]json.RawMessage{"detail": private}
		switch failure {
		case "envelope":
			response.Extra["error"] = json.RawMessage(`{"message":` + string(private) + `}`)
		case "empty":
			response.Choices = nil
		case "finish":
			response.Choices[0].FinishReason = "error"
		}
	}
	json.NewEncoder(w).Encode(response)
}

func (u *reflectionUpstream) calls() []models.ChatCompletionRequest {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]models.ChatCompletionRequest(nil), u.requests...)
}

type reflectionCheck struct {
	name       string
	stage      guardrails.Stage
	always     bool
	sawMutated bool
	mu         sync.Mutex
}

func (g *reflectionCheck) Name() string            { return g.name }
func (g *reflectionCheck) Stage() guardrails.Stage { return g.stage }
func (g *reflectionCheck) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	if in.Request != nil && len(in.Request.Messages) != 1 {
		g.mu.Lock()
		g.sawMutated = true
		g.mu.Unlock()
	}
	blocked := g.always
	if in.Response != nil {
		for _, c := range in.Response.Choices {
			blocked = blocked || strings.Contains(string(c.Message.Content), "rejected")
		}
	}
	if blocked {
		return &guardrails.CheckResult{Pass: false, Score: 1, Message: "Remove the rejected secret answer"}
	}
	return &guardrails.CheckResult{Pass: true}
}

type reflectionObserver struct {
	count    int
	snapshot *models.TraceSnapshot
	usage    *models.Usage
	metadata map[string]string
	initial  map[string]string
}

func (p *reflectionObserver) Name() string  { return "reflection-test-observer" }
func (p *reflectionObserver) Priority() int { return 1 }
func (p *reflectionObserver) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	for k, v := range p.initial {
		rc.Metadata[k] = v
	}
	return pipeline.ResultContinue()
}
func (p *reflectionObserver) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.count++
	p.snapshot = rc.Snapshot()
	p.usage = rc.ResponseUsage()
	p.metadata = p.snapshot.Metadata
	return pipeline.ResultContinue()
}

func reflectionPlugin(g *reflectionCheck, cfg map[string]interface{}, action, stage string) *guardrails.GuardrailPlugin {
	r := map[string]guardrails.Guardrail{g.name: g}
	e := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: g.name, Stage: stage, Mode: "sync", Action: action, Config: cfg}}}, r)
	return guardrails.NewPlugin(e, r, nil, nil, nil)
}
func reflectionPolicy() map[string]interface{} {
	return map[string]interface{}{"on_block": "reflect", "reflect_debug": true}
}
func reflectionCost() pipeline.Plugin {
	db := modeldb.New(map[string]*modeldb.ModelInfo{"gpt-4o": {ID: "gpt-4o", Pricing: modeldb.PricingInfo{InputPerToken: .01, OutputPerToken: .01}}}, nil)
	return costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil)
}
func callReflectionServer(s *Server, stream bool) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(fmt.Sprintf(`{"model":"gpt-4o","messages":[{"role":"user","content":"original prompt"}],"stream":%t}`, stream)))
	w := httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, r)
	return w
}

func TestReflexionHTTPPassingCandidateOnly(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			u := &reflectionUpstream{blocked: 1}
			upstream := httptest.NewServer(http.HandlerFunc(u.serve))
			defer upstream.Close()
			g := &reflectionCheck{name: "output", stage: guardrails.StagePost}
			observer := &reflectionObserver{}
			s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(g, reflectionPolicy(), "block", "post"), reflectionCost(), observer)
			w := callReflectionServer(s, stream)
			if w.Code != 200 || !strings.Contains(w.Body.String(), "accepted answer") || strings.Contains(w.Body.String(), "rejected") {
				t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
			}
			calls := u.calls()
			if len(calls) != 2 || len(calls[0].Messages) != 1 || len(calls[1].Messages) != 3 {
				t.Fatalf("provider requests=%+v", calls)
			}
			if calls[1].Messages[1].Role != "assistant" || !strings.Contains(string(calls[1].Messages[1].Content), "rejected secret answer") || !strings.Contains(string(calls[1].Messages[2].Content), "Remove") {
				t.Fatal("provider did not receive original rejected answer and reason")
			}
			if observer.count != 1 || len(observer.snapshot.Request.Messages) != 1 || g.sawMutated {
				t.Fatal("candidate mutation escaped to checks or final observers")
			}
			if observer.usage == nil || observer.usage.TotalTokens != 60 || observer.metadata["cost"] != "0.600000" {
				t.Fatalf("usage=%+v metadata=%v", observer.usage, observer.metadata)
			}
			if strings.Contains(fmt.Sprint(observer.metadata), "rejected") {
				t.Fatal("blocked content leaked to metadata")
			}
			if w.Header().Get("x-agentcc-reflexion-attempts") != "1" {
				t.Fatalf("headers=%v", w.Header())
			}
			if stream {
				if strings.Count(w.Body.String(), "data: [DONE]") != 1 || strings.Count(w.Body.String(), `"usage"`) != 1 || !strings.Contains(w.Body.String(), `"total_tokens":60`) {
					t.Fatalf("invalid SSE termination/usage: %s", w.Body.String())
				}
			} else {
				var resp models.ChatCompletionResponse
				json.Unmarshal(w.Body.Bytes(), &resp)
				if resp.Usage.TotalTokens != 60 {
					t.Fatalf("usage=%+v", resp.Usage)
				}
			}
		})
	}
}

func TestReflexionHTTPRejectExhaustionAndNonRepairable(t *testing.T) {
	for _, tc := range []struct {
		name                  string
		cfg                   map[string]interface{}
		blocks                int
		action, stage         string
		tool, multi           bool
		wantCalls, wantStatus int
	}{
		{"default reject", nil, 9, "block", "post", false, false, 1, 403},
		{"exhaustion", reflectionPolicy(), 9, "block", "post", false, false, 2, 403},
		{"hard cap", map[string]interface{}{"on_block": "reflect", "reflect_max_attempts": 5}, 9, "block", "post", false, false, 6, 403},
		{"pre block", nil, 9, "block", "pre", false, false, 0, 403},
		{"warn", nil, 9, "warn", "post", false, false, 1, 200},
		{"log", nil, 9, "log", "post", false, false, 1, 200},
		{"pre reflect invalid", reflectionPolicy(), 9, "block", "pre", false, false, 0, 400},
		{"warn reflect invalid", reflectionPolicy(), 9, "warn", "post", false, false, 0, 400},
		{"log reflect invalid", reflectionPolicy(), 9, "log", "post", false, false, 0, 400},
		{"tool calls", reflectionPolicy(), 9, "block", "post", true, false, 1, 403},
		{"multiple choices", reflectionPolicy(), 9, "block", "post", false, true, 1, 403},
		{"invalid limit", map[string]interface{}{"on_block": "reflect", "reflect_max_attempts": 6}, 9, "block", "post", false, false, 0, 400},
	} {
		t.Run(tc.name, func(t *testing.T) {
			u := &reflectionUpstream{blocked: tc.blocks, tool: tc.tool, multi: tc.multi}
			upstream := httptest.NewServer(http.HandlerFunc(u.serve))
			defer upstream.Close()
			g := &reflectionCheck{name: "output", stage: guardrails.StagePost, always: tc.stage == "pre"}
			observer := &reflectionObserver{}
			s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(g, tc.cfg, tc.action, tc.stage), reflectionCost(), observer)
			w := callReflectionServer(s, false)
			if w.Code != tc.wantStatus || len(u.calls()) != tc.wantCalls {
				t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
			}
			if tc.wantStatus != 200 && ((tc.wantCalls > 0 && strings.Contains(w.Body.String(), "rejected")) || observer.snapshot.Response != nil) {
				t.Fatal("rejected response leaked")
			}
			if tc.wantCalls > 0 && observer.usage.TotalTokens != 30*tc.wantCalls {
				t.Fatalf("unmetered blocked attempts: %+v", observer.usage)
			}
		})
	}
}

func TestReflexionHTTPMixedRulesRejectWins(t *testing.T) {
	u := &reflectionUpstream{blocked: 9}
	upstream := httptest.NewServer(http.HandlerFunc(u.serve))
	defer upstream.Close()
	registry := map[string]guardrails.Guardrail{"repair": &reflectionCheck{name: "repair"}, "reject": &reflectionCheck{name: "reject"}}
	engine := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{
		{Name: "repair", Stage: "post", Action: "block", Config: reflectionPolicy()}, {Name: "reject", Stage: "post", Action: "block"},
	}}, registry)
	s := createTestServerWithPlugins(t, upstream.URL, guardrails.NewPlugin(engine, registry, nil, nil, nil))
	w := callReflectionServer(s, true)
	if w.Code != 403 || len(u.calls()) != 1 || strings.Contains(w.Body.String(), "data:") {
		t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
	}
}

func TestReflexionHTTPAdmissionStopsWithoutDoubleCharge(t *testing.T) {
	for _, gate := range []string{"credits", "budget", "rpm", "tpm"} {
		t.Run(gate, func(t *testing.T) {
			u := &reflectionUpstream{blocked: 9}
			upstream := httptest.NewServer(http.HandlerFunc(u.serve))
			defer upstream.Close()
			store := tenant.NewStore()
			store.Set("org-a", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: true, OrgLimit: .1, HardLimit: true}})
			ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: "managed", Key: "test-managed-credit-key", KeyType: "managed", CreditBalance: .1}}})
			key := ks.List()[0]
			observer := &reflectionObserver{}
			plugins := []pipeline.Plugin{reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), reflectionCost(), observer}
			switch gate {
			case "credits":
				observer.initial = map[string]string{"auth_key_id": key.ID, "key_type": "managed"}
				plugins = append(plugins, creditplugin.New(true, ks))
			case "budget":
				observer.initial = map[string]string{"org_id": "org-a", tenant.MetadataKeyOrgID: "org-a"}
				plugins = append(plugins, budgetplugin.New(nil, budgetplugin.NewPricingLookup(map[string]config.CustomPricing{"gpt-4o": {InputPerMTok: 10000, OutputPerMTok: 10000}}), false, store))
			case "rpm":
				plugins = append(plugins, rateplugin.New(config.RateLimitConfig{Enabled: true, GlobalRPM: 1}, nil, nil))
			case "tpm":
				observer.initial = map[string]string{"org_rate_limit_tpm": "20"}
				plugins = append(plugins, rateplugin.New(config.RateLimitConfig{Enabled: true}, nil, nil))
			}
			s := createTestServerWithPlugins(t, upstream.URL, plugins...)
			w := callReflectionServer(s, false)
			want := 429
			if gate == "credits" {
				want = 402
			}
			if w.Code != want || len(u.calls()) != 1 {
				t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
			}
			if observer.count != 1 || observer.usage.TotalTokens != 30 || observer.metadata["cost"] != "0.300000" || observer.metadata["reflexion_attempts"] != "0" {
				t.Fatalf("double/stale accounting: usage=%+v metadata=%v observers=%d", observer.usage, observer.metadata, observer.count)
			}
			if gate == "credits" && math.Abs(key.BalanceUSD()-(-.2)) > 1e-6 {
				t.Fatalf("balance=%f; want -.2 after exactly one charge", key.BalanceUSD())
			}
		})
	}
}

func TestReflexionHTTPRejectedCandidatesNeverCached(t *testing.T) {
	u := &reflectionUpstream{blocked: 99}
	upstream := httptest.NewServer(http.HandlerFunc(u.serve))
	defer upstream.Close()
	store := cachepkg.NewStore(100)
	s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), cacheplugin.NewFromStore(store, time.Minute))
	for i := 0; i < 2; i++ {
		if w := callReflectionServer(s, false); w.Code != 403 {
			t.Fatalf("status=%d", w.Code)
		}
	}
	if len(u.calls()) != 4 {
		t.Fatalf("blocked output was cached: calls=%d", len(u.calls()))
	}
}

func TestReflexionHTTPUpstreamErrorsDoNotReflect(t *testing.T) {
	u := &reflectionUpstream{status: 503}
	upstream := httptest.NewServer(http.HandlerFunc(u.serve))
	defer upstream.Close()
	s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"))
	w := callReflectionServer(s, false)
	if w.Code < 400 || len(u.calls()) != 1 {
		t.Fatalf("status=%d calls=%d", w.Code, len(u.calls()))
	}
}

func TestReflexionBufferOverflowAndCancellation(t *testing.T) {
	for _, tc := range []string{"bytes", "cancel", "incomplete"} {
		t.Run(tc, func(t *testing.T) {
			chunks := make(chan models.StreamChunk, 1)
			errs := make(chan error)
			close(errs)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			var first *models.StreamChunk
			switch tc {
			case "bytes":
				text := strings.Repeat("x", maxReflexionStreamBytes)
				first = &models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
				close(chunks)
			case "cancel":
				cancel()
			case "incomplete":
				text := "partial"
				chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
				close(chunks)
			}
			_, err := collectCandidate(ctx, chunks, errs, first)
			if err == nil {
				t.Fatal("expected fail-closed buffer error")
			}
		})
	}
}
