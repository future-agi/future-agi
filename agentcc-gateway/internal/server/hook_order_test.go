package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// hookEvents records, in order, every hook the observer saw and the steps the
// test plugin and finalizer mark.
type hookEvents struct {
	mu   sync.Mutex
	list []string
}

func (e *hookEvents) add(s string) {
	e.mu.Lock()
	e.list = append(e.list, s)
	e.mu.Unlock()
}

func (e *hookEvents) all() []string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append([]string(nil), e.list...)
}

// hookOrderPlugin marks call-order steps 12 and 16: it runs among the
// pre-plugins, at priority 505.
type hookOrderPlugin struct {
	ev       *hookEvents
	panicPre bool
}

func (p *hookOrderPlugin) Name() string  { return "hook-order" }
func (p *hookOrderPlugin) Priority() int { return 505 }
func (p *hookOrderPlugin) ProcessRequest(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	p.ev.add("pre-plugins")
	if p.panicPre {
		panic("hook-order test panic")
	}
	return pipeline.ResultContinue()
}
func (p *hookOrderPlugin) ProcessResponse(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	p.ev.add("post-plugins")
	return pipeline.ResultContinue()
}

// hookUpstream is an OpenAI-shaped upstream that answers JSON, or SSE when
// the request streams, and fails its first failFirst chat requests with 502.
// It keeps every chat request body it received.
type hookUpstream struct {
	*httptest.Server
	failFirst int32
	calls     atomic.Int32
	mu        sync.Mutex
	bodies    []map[string]any
}

func startHookUpstream(t *testing.T, failFirst int32) *hookUpstream {
	t.Helper()
	u := &hookUpstream{failFirst: failFirst}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		u.mu.Lock()
		u.bodies = append(u.bodies, body)
		u.mu.Unlock()
		if u.calls.Add(1) <= u.failFirst {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadGateway)
			_, _ = w.Write([]byte(`{"error":{"message":"upstream down","type":"server_error"}}`))
			return
		}
		model, _ := body["model"].(string)
		if stream, _ := body["stream"].(bool); stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"chatcmpl-h\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"ok\"},\"finish_reason\":null}]}\n\n", model)
			fmt.Fprintf(w, "data: {\"id\":\"chatcmpl-h\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":1,\"total_tokens\":4}}\n\n", model)
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": "chatcmpl-h", "object": "chat.completion", "created": 1, "model": model,
			"choices": []any{map[string]any{"index": 0, "message": map[string]string{"role": "assistant", "content": "ok"}, "finish_reason": "stop"}},
			"usage":   map[string]int{"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
		})
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *hookUpstream) received() []map[string]any {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]map[string]any(nil), u.bodies...)
}

type hookServerMode int

const (
	hookPlain         hookServerMode = iota // openai serves gpt-4o
	hookFailover                            // first and second serve gpt-4o, with provider failover
	hookModelFallback                       // primary-model falls back to fallback-model
)

// hookOrderTest is a real server whose handlers report every hook to ev.
type hookOrderTest struct {
	srv  *Server
	ev   *hookEvents
	gens chan *models.TraceSnapshot
}

func newHookOrderTest(t *testing.T, upstreamURL string, mode hookServerMode, panicPre bool) *hookOrderTest {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "hook", Key: "sk-hook", Owner: "t", KeyType: "internal"}}
	provider := func(models ...string) config.ProviderConfig {
		return config.ProviderConfig{BaseURL: upstreamURL, APIKey: "dummy", APIFormat: "openai", Models: models}
	}
	switch mode {
	case hookPlain:
		cfg.Providers["openai"] = provider("gpt-4o")
		cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
	case hookFailover:
		cfg.Providers["first"] = provider("gpt-4o")
		cfg.Providers["second"] = provider("gpt-4o")
		cfg.Routing.DefaultStrategy = "round-robin"
		cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "first", Weight: 1}, {Provider: "second", Weight: 1}}}
		cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusBadGateway}}
	case hookModelFallback:
		cfg.Providers["primary"] = provider("primary-model")
		cfg.Providers["fallback"] = provider("fallback-model")
		// One target builds the router failover needs, without provider failover.
		cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"primary-model": {{Provider: "primary", Weight: 1}}}
		cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusBadGateway}}
		cfg.Routing.ModelFallbacks = map[string][]string{"primary-model": {"fallback-model"}}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	ev := &hookEvents{}
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), &hookOrderPlugin{ev: ev, panicPre: panicPre})
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	ht := &hookOrderTest{srv: srv, ev: ev, gens: make(chan *models.TraceSnapshot, 4)}
	srv.handlers.hookObserver = func(hook, detail string, rc *models.RequestContext) {
		if detail != "" {
			hook += ":" + detail
		}
		ev.add(hook)
		if hook == hookRuntimeIdentity {
			rc.AddFinalizer(func() { ev.add("finalizer") })
		}
	}
	srv.handlers.SetGenerationRecorder(func(snap *models.TraceSnapshot) { ht.gens <- snap })
	return ht
}

func (ht *hookOrderTest) chat(t *testing.T, body string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-hook")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	ht.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// waitGeneration waits for the request's generation record and returns the
// events without the asynchronous recordGeneration, after checking it ran
// exactly once, after the step that must precede it.
func (ht *hookOrderTest) waitGeneration(t *testing.T, after string) []string {
	t.Helper()
	select {
	case snap := <-ht.gens:
		if snap.RequestID == "" || snap.Response == nil {
			t.Errorf("generation snapshot has no request id or response: %+v", snap)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("recordGeneration never ran")
	}
	events := ht.ev.all()
	var rest []string
	genAt, afterAt := -1, -1
	for i, e := range events {
		switch {
		case e == hookRecordGeneration:
			if genAt >= 0 {
				t.Errorf("recordGeneration ran twice: %v", events)
			}
			genAt = i
			continue
		case e == after:
			afterAt = i
		}
		rest = append(rest, e)
	}
	if genAt < afterAt {
		t.Errorf("recordGeneration (event %d) ran before %s (event %d): %v", genAt, after, afterAt, events)
	}
	return rest
}

// preRouteSteps are call-order steps 1 to 10.
var preRouteSteps = []string{
	hookRuntimeIdentity, hookPeekKeyOrgID, hookResolveOrgConfig, hookParseAgentContext, hookEffectiveOrgConfig,
	hookResolveContentMode, hookApplyRoutingOptions, hookApplyAgentContext, hookExpandPreset,
	hookIdempotencyBegin, hookRunBudgetCap,
}

func steps(parts ...[]string) []string {
	var out []string
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

func checkOrder(t *testing.T, got, want []string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Errorf("hook order:\n got %v\nwant %v", got, want)
	}
}

const hookChatBody = `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`

func TestHookOrderNonStream(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := newHookOrderTest(t, up.URL, hookPlain, false)
	w := ht.chat(t, hookChatBody)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	got := ht.waitGeneration(t, hookSetAgentccHeaders)
	checkOrder(t, got, steps(preRouteSteps, []string{
		hookCheckEligibility + ":openai gpt-4o",
		"pre-plugins",
		hookHedgePlan,
		hookBeginAttempt + ":primary openai gpt-4o", hookRewriteUpstream, hookFinishAttempt,
		"post-plugins",
		hookCheckToolCalls, hookIdempotencyFinish, hookSetAgentccHeaders,
		"finalizer",
	}))
}

func TestHookOrderStream(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := newHookOrderTest(t, up.URL, hookPlain, false)
	w := ht.chat(t, `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "[DONE]") {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	got := ht.waitGeneration(t, "post-plugins")
	checkOrder(t, got, steps(preRouteSteps, []string{
		hookCheckEligibility + ":openai gpt-4o",
		"pre-plugins",
		hookHedgePlan,
		hookBeginAttempt + ":primary openai gpt-4o", hookRewriteUpstream, hookFinishAttempt,
		hookIdempotencyFinish, hookSetAgentccHeaders,
		"post-plugins",
		"finalizer",
	}))
}

func TestHookOrderFailover(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("provider failover stream=%v", stream), func(t *testing.T) {
			up := startHookUpstream(t, 1)
			ht := newHookOrderTest(t, up.URL, hookFailover, false)
			body := hookChatBody
			if stream {
				body = `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`
			}
			w := ht.chat(t, body)
			if w.Code != http.StatusOK || up.calls.Load() != 2 {
				t.Fatalf("status = %d after %d upstream calls, body %s", w.Code, up.calls.Load(), w.Body.String())
			}
			after := hookSetAgentccHeaders
			if stream {
				after = "post-plugins"
			}
			got := ht.waitGeneration(t, after)
			// Round-robin picks which provider goes first; the hop is to the other.
			var tried []string
			for _, e := range got {
				if rest, ok := strings.CutPrefix(e, hookBeginAttempt+":"); ok {
					tried = append(tried, strings.Fields(rest)[1])
				}
			}
			if len(tried) != 2 || tried[0] == tried[1] {
				t.Fatalf("attempts went to %v, want both providers once: %v", tried, got)
			}
			first, second := tried[0], tried[1]
			routed := got[len(preRouteSteps)]
			if !strings.HasPrefix(routed, hookCheckEligibility+":") || !strings.HasSuffix(routed, " gpt-4o") {
				t.Fatalf("event after the pre-route steps = %q, want the routed target's eligibility check", routed)
			}
			attempts := []string{
				hookBeginAttempt + ":primary " + first + " gpt-4o", hookRewriteUpstream, hookFinishAttempt,
				hookFallbackHopAllowed + ":" + second + " gpt-4o", hookReserveAdd + ":" + second + " gpt-4o",
				hookBeginAttempt + ":failover " + second + " gpt-4o", hookRewriteUpstream, hookFinishAttempt,
			}
			tail := []string{"post-plugins", hookCheckToolCalls, hookIdempotencyFinish, hookSetAgentccHeaders, "finalizer"}
			if stream {
				tail = []string{hookIdempotencyFinish, hookSetAgentccHeaders, "post-plugins", "finalizer"}
			}
			checkOrder(t, got, steps(preRouteSteps, []string{routed, "pre-plugins", hookHedgePlan}, attempts, tail))
		})
	}

	t.Run("model fallback hop", func(t *testing.T) {
		up := startHookUpstream(t, 1)
		ht := newHookOrderTest(t, up.URL, hookModelFallback, false)
		w := ht.chat(t, `{"model":"primary-model","messages":[{"role":"user","content":"hi"}]}`)
		if w.Code != http.StatusOK {
			t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
		}
		got := ht.waitGeneration(t, hookSetAgentccHeaders)
		checkOrder(t, got, steps(preRouteSteps, []string{
			hookCheckEligibility + ":primary primary-model",
			"pre-plugins",
			hookHedgePlan,
			hookBeginAttempt + ":primary primary primary-model", hookRewriteUpstream, hookFinishAttempt,
			hookFallbackHopAllowed + ":fallback fallback-model", hookReserveAdd + ":fallback fallback-model",
			hookBeginAttempt + ":model_fallback fallback fallback-model", hookRewriteUpstream, hookFinishAttempt,
			"post-plugins",
			hookCheckToolCalls, hookIdempotencyFinish, hookSetAgentccHeaders,
			"finalizer",
		}))
	})
}

// refusingBilling refuses every fallback hop's hold.
type refusingBilling struct{ asked atomic.Int32 }

func (b *refusingBilling) ReserveAdd(*models.RequestContext, string, string) (bool, error) {
	b.asked.Add(1)
	return false, nil
}
func (b *refusingBilling) Void(*models.RequestContext, string) {}

// A hop the billing hooks refuse is never dispatched.
func TestRefusedFallbackHopNotDispatched(t *testing.T) {
	up := startHookUpstream(t, 1)
	ht := newHookOrderTest(t, up.URL, hookModelFallback, false)
	billing := &refusingBilling{}
	ht.srv.handlers.SetBilling(billing)
	w := ht.chat(t, `{"model":"primary-model","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want the primary's 502; body %s", w.Code, w.Body.String())
	}
	if billing.asked.Load() != 1 || up.calls.Load() != 1 {
		t.Errorf("ReserveAdd asked %d times, upstream called %d times; want 1 and 1", billing.asked.Load(), up.calls.Load())
	}
}

func TestParseBeforeEffectiveConfig(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := newHookOrderTest(t, up.URL, hookPlain, false)
	var parsed, effective string
	var stripped atomic.Bool
	ht.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		ht.ev.add(hook)
		switch hook {
		case hookParseAgentContext: // parseAgentContext reads body session_id into rc.Agent
			if raw, ok := rc.Request.Extra["session_id"]; ok {
				_ = json.Unmarshal(raw, &parsed)
				rc.Agent.SessionID = parsed
			}
		case hookEffectiveOrgConfig: // effectiveOrgConfig buckets on it
			effective = rc.Agent.SessionID
		case hookApplyRoutingOptions: // applyRoutingOptions strips the routing fields
			rc.Routing.SessionID = rc.Agent.SessionID
			delete(rc.Request.Extra, "session_id")
			stripped.Store(true)
		}
	}
	w := ht.chat(t, `{"model":"gpt-4o","session_id":"sess_body","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if parsed != "sess_body" {
		t.Errorf("parseAgentContext saw body session_id %q, want sess_body (it ran after the strip)", parsed)
	}
	if effective != "sess_body" {
		t.Errorf("effectiveOrgConfig saw rc.Agent.SessionID %q, want sess_body", effective)
	}
	events := ht.ev.all()
	at := func(h string) int { return slices.Index(events, h) }
	if !(at(hookParseAgentContext) < at(hookEffectiveOrgConfig) && at(hookEffectiveOrgConfig) < at(hookApplyRoutingOptions)) {
		t.Errorf("order parse < effective < applyRoutingOptions broken: %v", events)
	}
	if bodies := up.received(); !stripped.Load() || len(bodies) != 1 || bodies[0]["session_id"] != nil {
		t.Errorf("upstream received %v; the stripped session_id must not reach it", bodies)
	}
}

func TestFinalizersRunOnPanicErrorSuccess(t *testing.T) {
	cases := []struct {
		name       string
		failFirst  int32
		panicPre   bool
		body       string
		lock       string
		wantStatus int
	}{
		{"success", 0, false, hookChatBody, "", http.StatusOK},
		{"refused by routing", 0, false, hookChatBody, "no-such-provider", http.StatusNotFound},
		{"upstream error", 5, false, hookChatBody, "", http.StatusBadGateway},
		{"stream", 0, false, `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`, "", http.StatusOK},
		{"panic in a pre-plugin", 0, true, hookChatBody, "", http.StatusInternalServerError},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			up := startHookUpstream(t, tc.failFirst)
			ht := newHookOrderTest(t, up.URL, hookPlain, tc.panicPre)
			var headers []string
			if tc.lock != "" {
				headers = []string{"x-agentcc-provider-lock", tc.lock}
			}
			w := ht.chat(t, tc.body, headers...)
			if w.Code != tc.wantStatus {
				t.Fatalf("status = %d, want %d; body %s", w.Code, tc.wantStatus, w.Body.String())
			}
			if n := slices.Index(ht.ev.all(), "finalizer"); n < 0 {
				t.Fatalf("finalizer never ran: %v", ht.ev.all())
			}
			count := 0
			for _, e := range ht.ev.all() {
				if e == "finalizer" {
					count++
				}
			}
			if count != 1 {
				t.Errorf("finalizer ran %d times, want 1", count)
			}
		})
	}
}

func TestFinalizersRunOnce(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := newHookOrderTest(t, up.URL, hookPlain, false)
	var ran []string
	var mu sync.Mutex
	add := func(rc *models.RequestContext, name string) {
		rc.AddFinalizer(func() {
			mu.Lock()
			ran = append(ran, name)
			mu.Unlock()
		})
	}
	ht.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		switch hook {
		case hookRuntimeIdentity:
			add(rc, "first")
		case hookParseAgentContext:
			add(rc, "second")
		case hookRunBudgetCap:
			add(rc, "third")
		}
	}
	for i := 0; i < 2; i++ { // the second request reuses a pooled context
		mu.Lock()
		ran = nil
		mu.Unlock()
		if w := ht.chat(t, hookChatBody); w.Code != http.StatusOK {
			t.Fatalf("request %d: status = %d", i, w.Code)
		}
		mu.Lock()
		got := append([]string(nil), ran...)
		mu.Unlock()
		if !slices.Equal(got, []string{"third", "second", "first"}) {
			t.Errorf("request %d: finalizers ran %v, want [third second first], once each", i, got)
		}
	}
}

func TestResponseHeaderBuffer(t *testing.T) {
	up := startHookUpstream(t, 0)
	ht := newHookOrderTest(t, up.URL, hookPlain, false)
	ht.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		switch hook {
		case hookRunBudgetCap:
			rc.SetResponseHeader("x-agentcc-order-test", "early")
		case hookCheckToolCalls: // step 17, before the headers are written
			rc.SetResponseHeader("X-Agentcc-Order-Test", "late")
		}
	}
	w := ht.chat(t, hookChatBody)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d", w.Code)
	}
	if got := w.Header().Values("x-agentcc-order-test"); !slices.Equal(got, []string{"late"}) {
		t.Errorf("x-agentcc-order-test = %v, want [late]", got)
	}
	if w.Header().Get("x-agentcc-provider") != "openai" {
		t.Errorf("the gateway's own headers were lost: %v", w.Header())
	}

	w = ht.chat(t, `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-order-test") != "early" {
		t.Errorf("stream: status %d, x-agentcc-order-test = %q, want early", w.Code, w.Header().Get("x-agentcc-order-test"))
	}
	if _, err := io.ReadAll(w.Body); err != nil {
		t.Fatal(err)
	}
}
