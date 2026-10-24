package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// attemptUpstream is an OpenAI-shaped upstream for one provider. It fails
// every chat call with status when status is non-zero; otherwise it answers
// with usage (a JSON object) and, when served is set, OpenRouter's served
// `provider` field. A stream waits firstChunkDelay before its first chunk;
// every call waits callDelay first, or until the gateway gives up on it.
type attemptUpstream struct {
	*httptest.Server
	status          atomic.Int32
	usage           string
	served          string
	firstChunkDelay time.Duration
	callDelay       time.Duration
	calls           atomic.Int32
}

func startAttemptUpstream(t *testing.T, usage, served string) *attemptUpstream {
	t.Helper()
	if usage == "" {
		usage = `{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}`
	}
	u := &attemptUpstream{usage: usage, served: served}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		u.calls.Add(1)
		var req models.ChatCompletionRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		if u.callDelay > 0 {
			select {
			case <-time.After(u.callDelay):
			case <-r.Context().Done():
				return
			}
		}
		if s := u.status.Load(); s != 0 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(int(s))
			_, _ = w.Write([]byte(`{"error":{"message":"upstream down","type":"server_error"}}`))
			return
		}
		provider := ""
		if u.served != "" {
			provider = fmt.Sprintf(`,"provider":%q`, u.served)
		}
		if req.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			flusher, _ := w.(http.Flusher)
			time.Sleep(u.firstChunkDelay)
			fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q%s,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"ok\"},\"finish_reason\":null}]}\n\n", req.Model, provider)
			if flusher != nil {
				flusher.Flush()
			}
			fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q%s,\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":%s}\n\n", req.Model, provider, u.usage)
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1,"model":%q%s,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":%s}`, req.Model, provider, u.usage)
	}))
	t.Cleanup(u.Close)
	return u
}

// attemptsCapture is a pipeline plugin that keeps what the request path left
// on rc when its post-plugins ran: the attempts, billing state, the parsed
// response usage and the request's errors, as the request log reads them.
type attemptsCapture struct {
	mu       sync.Mutex
	runs     int
	attempts []models.Attempt
	billing  models.BillingState
	usage    *models.Usage
	errs     []error
}

func (c *attemptsCapture) Name() string  { return "attempts-capture" }
func (c *attemptsCapture) Priority() int { return 900 }
func (c *attemptsCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (c *attemptsCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.runs++
	c.attempts = rc.AttemptsSnapshot()
	c.billing = rc.Billing.Clone()
	c.usage = nil
	if rc.Response != nil && rc.Response.Usage != nil {
		u := *rc.Response.Usage
		c.usage = &u
	}
	c.errs = append([]error(nil), rc.Errors...)
	return pipeline.ResultContinue()
}

func (c *attemptsCapture) last() ([]models.Attempt, models.BillingState, *models.Usage, []error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.attempts, c.billing, c.usage, c.errs
}

const attemptsKey = "sk-attempts-internal"

// attemptsGateway is a real server with providers "first" and "second", each
// on its own upstream, both serving gpt-4o, in that routing order.
type attemptsGateway struct {
	srv           *Server
	capture       *attemptsCapture
	first, second *attemptUpstream
	breakers      *routing.CircuitBreakerRegistry
}

type attemptsGatewayOpts struct {
	failover   bool // provider failover across first and second
	retries    int  // retries per target, 0 for none
	breaker    bool // a breaker that opens on one failure
	cache      bool // the response cache plugin
	usage      string
	served     string
	firstDelay time.Duration
}

func newAttemptsGateway(t *testing.T, o attemptsGatewayOpts) *attemptsGateway {
	t.Helper()
	first := startAttemptUpstream(t, o.usage, o.served)
	second := startAttemptUpstream(t, o.usage, o.served)
	first.firstChunkDelay, second.firstChunkDelay = o.firstDelay, o.firstDelay

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "attempts", Key: attemptsKey, Owner: "t", KeyType: "internal",
		Metadata: map[string]string{"org_id": "org-attempts"}}}
	cfg.Providers["first"] = config.ProviderConfig{BaseURL: first.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["second"] = config.ProviderConfig{BaseURL: second.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	// Least latency picks the first target while neither has a latency yet,
	// so the order is fixed; round robin's counter is shared with routing.
	cfg.Routing.DefaultStrategy = "least-latency"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "first", Weight: 1}, {Provider: "second", Weight: 1}}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	capture := &attemptsCapture{}
	plugins := []pipeline.Plugin{authplugin.New(keyStore, true), capture}
	tenants := tenant.NewStore()
	if o.cache { // response caching is opt-in per org
		plugins = append(plugins, cacheplugin.NewFromStore(cache.NewStore(16), time.Minute))
		tenants.Set("org-attempts", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	g := &attemptsGateway{srv: srv, capture: capture, first: first, second: second}
	if o.failover {
		var retryer *routing.Retryer
		if o.retries > 0 {
			retryer = routing.NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: o.retries, InitialDelay: time.Millisecond, MaxDelay: time.Millisecond})
		}
		if o.breaker {
			g.breakers = routing.NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
		}
		fo := routing.NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusBadGateway}}, registry.Router(), retryer, g.breakers)
		srv.handlers.failover.Store(fo)
	} else {
		srv.handlers.failover.Store(nil)
	}
	return g
}

func (g *attemptsGateway) chat(t *testing.T, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+attemptsKey)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

const attemptsChat = `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`

// --- schema -----------------------------------------------------------------

// attemptSchema is the Attempt definition of the log-entry-v2 contract schema.
func attemptSchema(t *testing.T) (attempt map[string]any, defs map[string]any) {
	t.Helper()
	_, here, _, _ := runtime.Caller(0)
	path := filepath.Join(filepath.Dir(here), "..", "..", "..", "api_contracts", "gateway", "examples", "log-entry-v2.schema.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading the log-entry-v2 schema: %v", err)
	}
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatalf("parsing the log-entry-v2 schema: %v", err)
	}
	defs, _ = schema["$defs"].(map[string]any)
	attempt, _ = defs["Attempt"].(map[string]any)
	if attempt == nil {
		t.Fatal("the log-entry-v2 schema has no $defs/Attempt")
	}
	return attempt, defs
}

// validateAttempt checks a marshalled attempt against schema, covering the
// keywords the Attempt definition uses and failing on any other, so a schema
// change cannot be skipped silently. It mirrors the logging package's
// validator, which a test in another package cannot import.
func validateAttempt(schema, defs map[string]any, inst any, path string) []string {
	var errs []string
	fail := func(format string, args ...any) { errs = append(errs, path+": "+fmt.Sprintf(format, args...)) }
	if ref, ok := schema["$ref"].(string); ok {
		name, found := strings.CutPrefix(ref, "#/$defs/")
		def, _ := defs[name].(map[string]any)
		if !found || def == nil {
			return []string{path + ": unresolvable $ref " + ref}
		}
		return validateAttempt(def, defs, inst, path)
	}
	for k := range schema {
		switch k {
		case "type", "enum", "required", "additionalProperties", "properties", "pattern", "minimum", "maximum", "maxLength", "format", "description":
		default:
			fail("unsupported schema keyword %q", k)
		}
	}
	switch schema["type"] {
	case "object":
		obj, ok := inst.(map[string]any)
		if !ok {
			fail("want an object, got %T", inst)
			return errs
		}
		props, _ := schema["properties"].(map[string]any)
		for _, r := range schema["required"].([]any) {
			if _, ok := obj[r.(string)]; !ok {
				fail("missing required %q", r)
			}
		}
		for k, v := range obj {
			sub, ok := props[k].(map[string]any)
			if !ok {
				if schema["additionalProperties"] == false {
					fail("unexpected property %q", k)
				}
				continue
			}
			errs = append(errs, validateAttempt(sub, defs, v, path+"."+k)...)
		}
		return errs
	case "string":
		s, ok := inst.(string)
		if !ok {
			fail("want a string, got %T", inst)
			return errs
		}
		if p, ok := schema["pattern"].(string); ok && !regexp.MustCompile(p).MatchString(s) {
			fail("%q does not match %s", s, p)
		}
		if m, ok := schema["maxLength"].(float64); ok && len([]rune(s)) > int(m) {
			fail("longer than %v", m)
		}
		if schema["format"] == "date-time" {
			if _, err := time.Parse(time.RFC3339Nano, s); err != nil {
				fail("%q is not a date-time", s)
			}
		}
	case "integer":
		n, ok := inst.(float64)
		if !ok || n != float64(int64(n)) {
			fail("want an integer, got %v", inst)
			return errs
		}
		if m, ok := schema["minimum"].(float64); ok && n < m {
			fail("%v is below %v", n, m)
		}
		if m, ok := schema["maximum"].(float64); ok && n > m {
			fail("%v is above %v", n, m)
		}
	case "boolean":
		if _, ok := inst.(bool); !ok {
			fail("want a boolean, got %T", inst)
		}
	default:
		fail("unsupported type %v", schema["type"])
	}
	if enum, ok := schema["enum"].([]any); ok && !slices.Contains(enum, inst) {
		fail("%v is not one of %v", inst, enum)
	}
	return errs
}

func checkAttemptsMatchSchema(t *testing.T, attempts []models.Attempt) {
	t.Helper()
	schema, defs := attemptSchema(t)
	for i, a := range attempts {
		raw, _ := json.Marshal(a)
		var inst any
		_ = json.Unmarshal(raw, &inst)
		if errs := validateAttempt(schema, defs, inst, fmt.Sprintf("attempts[%d]", i)); len(errs) > 0 {
			t.Errorf("attempt fails the log-entry-v2 Attempt schema:\n%s\n%s", strings.Join(errs, "\n"), raw)
		}
	}
}

// The validator rejects what the schema forbids, so a pass means something.
func TestAttemptSchemaValidatorRejects(t *testing.T) {
	schema, defs := attemptSchema(t)
	for name, inst := range map[string]map[string]any{
		"no outcome":    {"attempt_id": "r.1", "index": 1, "kind": "primary", "provider": "p", "model": "m", "credential_type": "byok", "started_at": "2026-10-15T12:00:00Z"},
		"bad kind":      {"attempt_id": "r.1", "index": 1, "kind": "second_try", "provider": "p", "model": "m", "credential_type": "byok", "outcome": "success", "started_at": "2026-10-15T12:00:00Z"},
		"index 17":      {"attempt_id": "r.17", "index": 17, "kind": "primary", "provider": "p", "model": "m", "credential_type": "byok", "outcome": "success", "started_at": "2026-10-15T12:00:00Z"},
		"unknown field": {"attempt_id": "r.1", "index": 1, "kind": "primary", "provider": "p", "model": "m", "credential_type": "byok", "outcome": "success", "started_at": "2026-10-15T12:00:00Z", "api_key": "x"},
	} {
		raw, _ := json.Marshal(inst)
		var v any
		_ = json.Unmarshal(raw, &v)
		if errs := validateAttempt(schema, defs, v, name); len(errs) == 0 {
			t.Errorf("%s: the validator accepted an invalid attempt", name)
		}
	}
}

// --- attempts through the real request path ----------------------------------

// TestAttemptGolden runs a request whose first provider fails its call and
// its retry, so it fails over to the second, and compares the attempts the
// request log reads with the golden record, then with the Attempt schema.
func TestAttemptGolden(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{failover: true, retries: 1,
		usage: `{"prompt_tokens":12,"completion_tokens":5,"total_tokens":17,"prompt_tokens_details":{"cached_tokens":8},"completion_tokens_details":{"reasoning_tokens":2}}`})
	g.first.status.Store(http.StatusBadGateway)

	w := g.chat(t, attemptsChat)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body)
	}
	attempts, _, _, _ := g.capture.last()
	requestID := w.Header().Get("x-agentcc-request-id")
	if requestID == "" {
		t.Fatal("no request id header")
	}
	normalized := make([]models.Attempt, len(attempts))
	for i, a := range attempts {
		if !strings.HasPrefix(a.AttemptID, requestID+".") || a.StartedAt.IsZero() {
			t.Errorf("attempt %d id %q start %v, want %s.<index> and a start time", i+1, a.AttemptID, a.StartedAt, requestID)
		}
		a.AttemptID = strings.Replace(a.AttemptID, requestID, "req", 1)
		a.StartedAt = time.Date(2026, 10, 15, 12, 0, 0, 0, time.UTC)
		a.LatencyMs = 0
		normalized[i] = a
	}
	got, _ := json.MarshalIndent(normalized, "", "  ")
	want := `[
  {
    "attempt_id": "req.1",
    "index": 1,
    "kind": "primary",
    "provider": "first",
    "model": "gpt-4o",
    "credential_type": "byok",
    "status_code": 502,
    "outcome": "error",
    "error_class": "upstream_5xx",
    "error_origin": "upstream",
    "error_message": "upstream down",
    "started_at": "2026-10-15T12:00:00Z"
  },
  {
    "attempt_id": "req.2",
    "index": 2,
    "kind": "retry",
    "reason": "status_5xx",
    "provider": "first",
    "model": "gpt-4o",
    "credential_type": "byok",
    "status_code": 502,
    "outcome": "error",
    "error_class": "upstream_5xx",
    "error_origin": "upstream",
    "error_message": "upstream down",
    "started_at": "2026-10-15T12:00:00Z"
  },
  {
    "attempt_id": "req.3",
    "index": 3,
    "kind": "failover",
    "reason": "status_5xx",
    "provider": "second",
    "model": "gpt-4o",
    "credential_type": "byok",
    "status_code": 200,
    "outcome": "success",
    "started_at": "2026-10-15T12:00:00Z",
    "prompt_tokens": 12,
    "completion_tokens": 5,
    "cached_tokens": 8,
    "reasoning_tokens": 2
  }
]`
	if string(got) != want {
		t.Fatalf("attempts =\n%s\nwant\n%s", got, want)
	}
	checkAttemptsMatchSchema(t, attempts)
}

func TestAutoFallbackAttemptKind(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	g.managed.failFor.Store("qwen/qwen3.7-flash", http.StatusServiceUnavailable)
	snaps := make(chan *models.TraceSnapshot, 1)
	g.srv.handlers.SetGenerationRecorder(func(s *models.TraceSnapshot) { snaps <- s })

	w := g.chat(t, chatBody("auto"), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; body %s", w.Code, w.Body)
	}
	var snap *models.TraceSnapshot
	select {
	case snap = <-snaps:
	case <-time.After(5 * time.Second):
		t.Fatal("no generation record")
	}
	a := snap.Attempts
	if len(a) != 2 {
		t.Fatalf("attempts = %+v, want the auto pick and its runner-up", a)
	}
	if a[0].Kind != models.AttemptPrimary || a[0].Model != "qwen/qwen3.7-flash" || a[0].ErrorClass != models.ErrClassUpstream5xx {
		t.Errorf("first attempt = %+v, want the failed primary on qwen/qwen3.7-flash", a[0])
	}
	if a[1].Kind != models.AttemptAutoFallback || a[1].Outcome != models.OutcomeSuccess || a[1].Reason != models.ReasonStatus5xx {
		t.Errorf("second attempt = %+v, want an auto_fallback success after status_5xx", a[1])
	}
	for i, at := range a {
		if at.CredentialType != models.CredentialManaged || at.Provider != "openrouter" {
			t.Errorf("attempt %d credential %q on %q, want managed on the managed provider", i+1, at.CredentialType, at.Provider)
		}
	}
	checkAttemptsMatchSchema(t, a)
}

func TestStreamAttemptTTFB(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{firstDelay: 40 * time.Millisecond})

	w := g.chat(t, `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "[DONE]") {
		t.Fatalf("status = %d, body %s", w.Code, w.Body)
	}
	attempts, _, _, _ := g.capture.last()
	if len(attempts) != 1 {
		t.Fatalf("attempts = %+v, want one", attempts)
	}
	a := attempts[0]
	if a.Kind != models.AttemptPrimary || a.Outcome != models.OutcomeSuccess || a.StatusCode != 200 {
		t.Fatalf("stream attempt = %+v, want a successful primary", a)
	}
	if a.TTFBMs < 40 || a.TTFBMs > 5000 {
		t.Fatalf("ttfb_ms = %d, want the 40 ms the upstream held its first chunk", a.TTFBMs)
	}
	if a.LatencyMs != 0 {
		t.Fatalf("latency_ms = %d at the first chunk; a stream's latency is only known at its end", a.LatencyMs)
	}
	checkAttemptsMatchSchema(t, attempts)
}

func TestCachedReasoningTokensExtracted(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{usage: `{"prompt_tokens":100,"completion_tokens":50,"total_tokens":150,` +
		`"prompt_tokens_details":{"cached_tokens":40,"audio_tokens":0,"x_future_tokens":7},` +
		`"completion_tokens_details":{"reasoning_tokens":20,"accepted_prediction_tokens":0}}`})

	w := g.chat(t, attemptsChat)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body)
	}
	attempts, _, usage, _ := g.capture.last()
	if len(attempts) != 1 {
		t.Fatalf("attempts = %+v, want one", attempts)
	}
	a := attempts[0]
	if a.PromptTokens != 100 || a.CompletionTokens != 50 || a.CachedTokens != 40 || a.ReasoningTokens != 20 {
		t.Fatalf("attempt tokens = prompt %d completion %d cached %d reasoning %d, want 100/50/40/20",
			a.PromptTokens, a.CompletionTokens, a.CachedTokens, a.ReasoningTokens)
	}
	if usage == nil || usage.PromptDetails.CachedTokens != 40 || usage.CompletionDetails.ReasoningTokens != 20 {
		t.Fatalf("the request's usage details = %+v, want cached 40 and reasoning 20 parsed", usage)
	}
	var body struct {
		Usage struct {
			Prompt     map[string]int `json:"prompt_tokens_details"`
			Completion map[string]int `json:"completion_tokens_details"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Usage.Prompt["x_future_tokens"] != 7 || body.Usage.Prompt["cached_tokens"] != 40 || body.Usage.Completion["reasoning_tokens"] != 20 {
		t.Fatalf("the client's usage details = %+v / %+v, want the upstream's subfields unchanged", body.Usage.Prompt, body.Usage.Completion)
	}
}

func TestCacheHitNoAttempts(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{cache: true})

	if w := g.chat(t, attemptsChat); w.Code != http.StatusOK {
		t.Fatalf("first call status = %d, body %s", w.Code, w.Body)
	}
	if attempts, _, _, _ := g.capture.last(); len(attempts) != 1 {
		t.Fatalf("the miss recorded %d attempts, want 1", len(attempts))
	}
	w := g.chat(t, attemptsChat)
	if w.Code != http.StatusOK || g.first.calls.Load()+g.second.calls.Load() != 1 {
		t.Fatalf("second call status = %d after %d upstream calls; want a cache hit", w.Code, g.first.calls.Load()+g.second.calls.Load())
	}
	if attempts, _, _, _ := g.capture.last(); len(attempts) != 0 {
		t.Fatalf("the cache hit recorded attempts %+v, want none (attempt_count 0)", attempts)
	}
}

func TestServedUpstreamRecorded(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{served: "Fireworks"})
	if w := g.chat(t, attemptsChat); w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body)
	}
	attempts, billing, _, _ := g.capture.last()
	if len(attempts) != 1 || attempts[0].ServedUpstreamProvider != "Fireworks" || billing.ServedUpstreamProvider != "Fireworks" {
		t.Fatalf("served upstream: attempts %+v, billing %q; want Fireworks on both", attempts, billing.ServedUpstreamProvider)
	}

	// A stream reports it on its chunks: an attempt finished with its first
	// chunk records it the same way, and a failed attempt records none.
	h := g.srv.handlers
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-stream"
	rc.Request = &models.ChatCompletionRequest{Model: "vendor/m", Stream: true}
	_, finish := h.beginAttempt(context.Background(), rc, models.AttemptPrimary, "", "first", "vendor/m", models.CredentialBYOK)
	finish(&models.StreamChunk{UpstreamProvider: "DeepInfra"}, nil, nil)
	_, failed := h.beginAttempt(context.Background(), rc, models.AttemptRetry, "", "first", "vendor/m", models.CredentialBYOK)
	failed(&models.StreamChunk{UpstreamProvider: "Other"}, nil, &models.APIError{Status: 502})
	got := rc.AttemptsSnapshot()
	if got[0].ServedUpstreamProvider != "DeepInfra" || got[1].ServedUpstreamProvider != "" || rc.Billing.ServedUpstreamProvider != "DeepInfra" {
		t.Fatalf("stream served upstream: attempts %+v, billing %q", got, rc.Billing.ServedUpstreamProvider)
	}
}

// Every attempt's finish feeds the per-model health the wiring installs; a
// cancelled call says nothing about the model and is left out.
func TestAttemptFinishFeedsModelHealth(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{failover: true})
	health := routing.NewModelHealth()
	g.srv.handlers.SetInjectedDeps(InjectedDeps{ModelHealth: health})
	g.first.status.Store(http.StatusBadGateway)

	if w := g.chat(t, attemptsChat); w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body)
	}
	first, second := health.Snapshot("first", "gpt-4o"), health.Snapshot("second", "gpt-4o")
	if first.N != 1 || first.ErrorRate != 1 || first.LastErrorClass != string(models.ErrClassUpstream5xx) {
		t.Errorf("first's health = %+v, want one upstream_5xx failure", first)
	}
	if second.N != 1 || second.SuccessRate != 1 {
		t.Errorf("second's health = %+v, want one success", second)
	}

	rc := models.AcquireRequestContext()
	defer rc.Release()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, finish := g.srv.handlers.beginAttempt(ctx, rc, models.AttemptPrimary, "", "first", "other-model", models.CredentialBYOK)
	finish(nil, nil, models.ErrGatewayTimeout("provider request timed out"))
	if snap := health.Snapshot("first", "other-model"); snap.N != 0 {
		t.Errorf("a client-cancelled call fed the health: %+v", snap)
	}
	if a := rc.AttemptsSnapshot(); a[0].Outcome != models.OutcomeCancelled || a[0].ErrorOrigin != models.OriginClient {
		t.Errorf("cancelled attempt = %+v, want outcome cancelled from the client", a[0])
	}
}

// --- a refused fallback hop --------------------------------------------------

// newRefusedHopGateway fails every call to first, with two retries per
// target, and refuses the hop to second at its hold (the billing seam);
// fallbackHopAllowed allows every hop here. refusingBilling is
// the hook-order tests' fake, which counts the holds asked for.
func newRefusedHopGateway(t *testing.T) (*attemptsGateway, *refusingBilling) {
	t.Helper()
	g := newAttemptsGateway(t, attemptsGatewayOpts{failover: true, retries: 2, breaker: true})
	g.first.status.Store(http.StatusBadGateway)
	billing := &refusingBilling{}
	g.srv.handlers.SetBilling(billing)
	return g, billing
}

func TestSkippedHopNotRecordedAsFailure(t *testing.T) {
	g, billing := newRefusedHopGateway(t)

	w := g.chat(t, attemptsChat)
	if w.Code != http.StatusBadGateway || g.second.calls.Load() != 0 || billing.asked.Load() == 0 {
		t.Fatalf("status = %d, second called %d times, %d holds; want first's 502 with the hop to second refused",
			w.Code, g.second.calls.Load(), billing.asked.Load())
	}
	if state := g.breakers.Get(routing.BreakerKey("first", "gpt-4o", "")).State(); state != routing.StateOpen {
		t.Fatalf("first's breaker is %v after its real failures; the test proves nothing", state)
	}
	if state := g.breakers.Get(routing.BreakerKey("second", "gpt-4o", "")).State(); state != routing.StateClosed {
		t.Fatalf("second's breaker is %v after a hop the gateway refused; a refusal is not a provider failure", state)
	}
	attempts, _, _, _ := g.capture.last()
	if len(attempts) != 4 {
		t.Fatalf("attempts = %+v, want first's three calls and the refused hop", attempts)
	}
	skipped := attempts[3]
	if skipped.Kind != models.AttemptFailover || skipped.Provider != "second" || skipped.Outcome != models.OutcomeSkipped ||
		skipped.Reason != models.ReasonSkippedInsufficientCredits || skipped.ErrorClass != models.ErrClassInsufficientCredits ||
		skipped.ErrorOrigin != models.OriginGateway || skipped.LatencyMs != 0 {
		t.Fatalf("refused hop's attempt = %+v, want a skipped failover to second for skipped_insufficient_credits", skipped)
	}
	checkAttemptsMatchSchema(t, attempts)
}

func TestSkippedHopNotRetried(t *testing.T) {
	g, billing := newRefusedHopGateway(t)

	_ = g.chat(t, attemptsChat)
	if n := billing.asked.Load(); n != 1 {
		t.Fatalf("the hop to second took %d holds, want exactly 1: a retried refused hop runs ReserveAdd again", n)
	}
	if n := g.first.calls.Load(); n != 3 {
		t.Fatalf("first was called %d times, want its primary call and 2 retries", n)
	}
}

func TestSkippedHopKeepsPreviousError(t *testing.T) {
	g, _ := newRefusedHopGateway(t)

	w := g.chat(t, attemptsChat)
	if w.Code != http.StatusBadGateway || !strings.Contains(w.Body.String(), "upstream down") || strings.Contains(w.Body.String(), "skipped") {
		t.Fatalf("status = %d, body %s; want first's own 502", w.Code, w.Body)
	}
	_, _, _, errs := g.capture.last()
	if len(errs) == 0 {
		t.Fatal("the request recorded no error")
	}
	err := errs[len(errs)-1]
	var apiErr *models.APIError
	if errors.Is(err, routing.ErrHopSkipped) || !errors.As(err, &apiErr) || apiErr.Status != http.StatusBadGateway {
		t.Fatalf("the request's error = %v, want first's provider error, not the skip", err)
	}
}

// Past models.MaxAttempts an attempt is not recorded, but its finish still
// runs and still feeds the model's health.
func TestAttemptsPastTheCapStillFeedHealth(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{})
	health := routing.NewModelHealth()
	g.srv.handlers.SetInjectedDeps(InjectedDeps{ModelHealth: health})
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-cap"
	for i := 0; i < models.MaxAttempts+2; i++ {
		_, finish := g.srv.handlers.beginAttempt(context.Background(), rc, models.AttemptRetry, models.ReasonStatus5xx, "first", "gpt-4o", models.CredentialBYOK)
		finish(nil, nil, &models.APIError{Status: http.StatusBadGateway})
	}
	if n := rc.AttemptCount(); n != models.MaxAttempts {
		t.Fatalf("recorded %d attempts, want the cap of %d", n, models.MaxAttempts)
	}
	if snap := health.Snapshot("first", "gpt-4o"); snap.N != models.MaxAttempts+2 {
		t.Fatalf("health saw %d attempts, want all %d", snap.N, models.MaxAttempts+2)
	}
}

// An org's own key on a provider id that is also a managed global provider is
// the org's credential: the attempt is byok, as the request is billed. The
// provider id alone would say managed.
func TestOrgKeyOnManagedProviderIDIsByok(t *testing.T) {
	g := newManagedTestGateway(t, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openrouter": {APIKey: "org-or-key", BaseURL: "http://10.255.255.1:8080", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
	}})
	h := g.srv.handlers
	if !h.registry.IsManaged("openrouter") {
		t.Fatal("the test gateway's openrouter is not managed; the test proves nothing")
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-org-or"
	rc.Request = &models.ChatCompletionRequest{Model: "org-model"}
	rc.Metadata["billing_mode"] = billingBYOK
	_, finish := h.beginAttempt(context.Background(), rc, models.AttemptPrimary, "", "openrouter", "org-model", credentialType(rc))
	finish(nil, nil, nil)
	if got := rc.AttemptsSnapshot()[0].CredentialType; got != models.CredentialBYOK {
		t.Fatalf("the org's own key on provider id openrouter recorded credential %q, want byok", got)
	}

	// The managed instance itself is managed.
	rc.Metadata["billing_mode"] = billingManaged
	_, finish = h.beginAttempt(context.Background(), rc, models.AttemptPrimary, "", "openrouter", "anthropic/claude-sonnet-5.5", credentialType(rc))
	finish(nil, nil, nil)
	if got := rc.AttemptsSnapshot()[1].CredentialType; got != models.CredentialManaged {
		t.Fatalf("the managed provider recorded credential %q, want managed", got)
	}

	// A hop to a provider that is not managed is not, whatever billing mode an
	// earlier attempt left on the request.
	_, finish = h.beginAttempt(context.Background(), rc, models.AttemptModelFallback, "", "operator", "gpt-4o", credentialType(rc))
	finish(nil, nil, nil)
	if got := rc.AttemptsSnapshot()[2].CredentialType; got != models.CredentialBYOK {
		t.Fatalf("a hop to the non-managed operator recorded credential %q, want byok", got)
	}

	// An internal key (no billing mode) dispatched to the node's managed
	// instance, as provider failover does, is paid for by FutureAGI: managed.
	delete(rc.Metadata, "billing_mode")
	_, finish = h.beginAttempt(context.Background(), rc, models.AttemptFailover, models.ReasonStatus5xx, "openrouter", "anthropic/claude-sonnet-5.5", credentialType(rc))
	finish(nil, nil, nil)
	if got := rc.AttemptsSnapshot()[3].CredentialType; got != models.CredentialManaged {
		t.Fatalf("an internal key on the managed openrouter instance recorded credential %q, want managed", got)
	}
}

// --- what feeds the per-model health -------------------------------------------

// healthN is how many calls the health saw on gpt-4o, on either provider.
func healthN(health *routing.ModelHealth) int {
	return health.Snapshot("first", "gpt-4o").N + health.Snapshot("second", "gpt-4o").N
}

// A request's own fault says nothing about the model, whose health every org
// on the node shares: 20 requests the upstream rejects as invalid leave it
// untouched, while 20 requests the upstream fails count against it. The
// attempts themselves keep their classes.
func TestRequestErrorsLeaveModelHealth(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{failover: true})
	health := routing.NewModelHealth()
	g.srv.handlers.SetInjectedDeps(InjectedDeps{ModelHealth: health})

	g.first.status.Store(http.StatusBadRequest)
	g.second.status.Store(http.StatusBadRequest)
	for i := 0; i < 20; i++ {
		if w := g.chat(t, attemptsChat); w.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, body %s; want the upstream's 400", w.Code, w.Body)
		}
	}
	if attempts, _, _, _ := g.capture.last(); len(attempts) != 1 || attempts[0].ErrorClass != models.ErrClassInvalidRequest {
		t.Fatalf("attempts = %+v, want one invalid_request attempt", attempts)
	}
	if n := healthN(health); n != 0 {
		t.Fatalf("20 invalid requests fed the health %d times: first %+v, second %+v", n,
			health.Snapshot("first", "gpt-4o"), health.Snapshot("second", "gpt-4o"))
	}

	g.first.status.Store(http.StatusBadGateway)
	g.second.status.Store(http.StatusBadGateway)
	for i := 0; i < 20; i++ {
		_ = g.chat(t, attemptsChat)
	}
	first, second := health.Snapshot("first", "gpt-4o"), health.Snapshot("second", "gpt-4o")
	if first.N+second.N != 40 || first.SuccessRate != 0 || second.SuccessRate != 0 {
		t.Fatalf("20 requests failed upstream on both targets: first %+v, second %+v; want 40 failures", first, second)
	}
}

// An attempt made with an org's own credentials is that org's (its key, its
// quota, possibly its own upstream): it does not feed the health of the
// provider id it shares with the node's instance, here the managed
// openrouter. The node's own instances, managed or not, do.
func TestOrgCredentialAttemptsLeaveSharedHealth(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	h := g.srv.handlers
	health := routing.NewModelHealth()
	h.SetInjectedDeps(InjectedDeps{ModelHealth: health})
	const model = "anthropic/claude-sonnet-4.5"
	run := func(billing string, n int, err error) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.RequestID = "req-health-" + billing
		rc.Request = &models.ChatCompletionRequest{Model: model}
		rc.Metadata["billing_mode"] = billing
		for i := 0; i < n; i++ {
			_, finish := h.beginAttempt(context.Background(), rc, models.AttemptPrimary, "", "openrouter", model, credentialType(rc))
			finish(nil, nil, err)
		}
	}

	run(billingBYOK, 20, &models.APIError{Status: http.StatusBadGateway})
	run(billingBYOK, 20, &models.APIError{Status: http.StatusTooManyRequests})
	if snap := health.Snapshot("openrouter", model); snap.N != 0 {
		t.Fatalf("an org's own key on provider id openrouter fed the shared health: %+v", snap)
	}

	run(billingManaged, 10, &models.APIError{Status: http.StatusBadGateway})
	run("", 10, &models.APIError{Status: http.StatusBadGateway}) // an internal key on the node's instance
	if snap := health.Snapshot("openrouter", model); snap.N != 20 || snap.SuccessRate != 0 {
		t.Fatalf("the node's own openrouter instance: %+v, want 20 failures", snap)
	}
}

// A gateway timeout counts against the model only when the model had the
// node's own time to answer: a caller (x-agentcc-timeout) or an org that asks
// for less must not mark the model slow for everyone.
func TestCallerDeadlineTimeoutsLeaveModelHealth(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{})
	health := routing.NewModelHealth()
	g.srv.handlers.SetInjectedDeps(InjectedDeps{ModelHealth: health})
	g.first.callDelay, g.second.callDelay = 5*time.Second, 5*time.Second
	g.srv.handlers.defaultTimeout = 500 * time.Millisecond

	chat := func(timeoutHeader string) {
		t.Helper()
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(attemptsChat))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+attemptsKey)
		if timeoutHeader != "" {
			req.Header.Set("x-agentcc-timeout", timeoutHeader)
		}
		g.srv.httpServer.Handler.ServeHTTP(httptest.NewRecorder(), req)
		attempts, _, _, _ := g.capture.last()
		if len(attempts) != 1 || attempts[0].Outcome != models.OutcomeTimeout || attempts[0].ErrorOrigin != models.OriginGateway {
			t.Fatalf("attempts = %+v, want one gateway timeout", attempts)
		}
	}

	for i := 0; i < 3; i++ {
		chat("20ms")
	}
	if n := healthN(health); n != 0 {
		t.Fatalf("timeouts the caller asked for fed the health %d times", n)
	}

	for i := 0; i < 3; i++ {
		chat("") // the node's own 500 ms
	}
	first, second := health.Snapshot("first", "gpt-4o"), health.Snapshot("second", "gpt-4o")
	if first.N+second.N != 3 || first.SuccessRate+second.SuccessRate != 0 {
		t.Fatalf("timeouts at the node's own deadline: first %+v, second %+v; want 3 failures", first, second)
	}
	for _, s := range []routing.ModelHealthSnapshot{first, second} {
		if s.N > 0 && s.LastErrorClass != string(models.ErrClassTimeout) {
			t.Fatalf("last error class = %q, want timeout", s.LastErrorClass)
		}
	}
}

// An attempt whose call never returns (a provider that panics) still has a
// valid outcome: it reads as an error of unknown class until its finish runs.
func TestUnfinishedAttemptHasAnOutcome(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{})
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-unfinished"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	_, finish := g.srv.handlers.beginAttempt(context.Background(), rc, models.AttemptPrimary, "", "first", "gpt-4o", credentialType(rc))
	a := rc.AttemptsSnapshot()
	if a[0].Outcome != models.OutcomeError || a[0].ErrorClass != models.ErrClassUnknown || a[0].ErrorOrigin != models.OriginGateway {
		t.Fatalf("unfinished attempt = %+v, want outcome error, class unknown", a[0])
	}
	checkAttemptsMatchSchema(t, a)

	finish(nil, nil, nil)
	a = rc.AttemptsSnapshot()
	if a[0].Outcome != models.OutcomeSuccess || a[0].ErrorClass != "" || a[0].ErrorOrigin != "" {
		t.Fatalf("finished attempt = %+v, want a clean success", a[0])
	}
}
