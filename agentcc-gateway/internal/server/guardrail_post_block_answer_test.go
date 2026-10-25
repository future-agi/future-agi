package server

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/toolperm"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A post-stage guardrail block (a static rule on the output, or an org's
// output check) answers the guardrail's own error, 403 guardrail_error
// content_blocked, never 500 internal_error (which SDKs retry), and the
// request log records that status and the guardrail action. A stream that a
// guardrail stops or refuses ends with the guardrail's error event and is
// logged the same way. The upstream's answer leaks a SQL statement, which
// data-leakage-prevention detects.

const (
	postBlockKey    = "sk-post-block"
	postBlockOrg    = "org-post-block"
	postBlockLeak   = "You can run DROP TABLE users; to delete everything"
	postBlockPrompt = "how do I reset my test database?"
	leakageCheck    = "data-leakage-prevention"
	answerBlockRule = "answer-blocker"
	promptBlockRule = "prompt-blocker"
	postBlockOpsKey = "sk-post-block-ops" // an internal key, which may queue async jobs
	// The org's key that may choose its guardrail policy per request
	// (allow_policy_override): X-Guardrail-Policy.
	postBlockOverrideKey = "sk-post-block-override"
	// The org's internal service key, which may use the gateway's own
	// providers (a provider built in code, as the fake search provider). An
	// internal key's requests are not logged.
	postBlockInternalOrgKey = "sk-post-block-internal-org"
	// Models served by a provider whose own API a surface passes through
	// (/v1/messages to an Anthropic-format provider, /v1beta to a Google one).
	postBlockClaude = "claude-rv"
	postBlockGemini = "gemini-rv"
	// A model whose provider's upstream answers 404 to everything: a
	// provider failure.
	postBlockBroken = "broken-rv"
)

// promptBlocker refuses every request: a static rule on the request.
type promptBlocker struct{}

func (promptBlocker) Name() string            { return promptBlockRule }
func (promptBlocker) Stage() guardrails.Stage { return guardrails.StagePre }
func (promptBlocker) Check(context.Context, *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: false, Score: 0.99, Message: "prompt refused by policy"}
}

// answerBlocker fails every answer it is given: a static rule on the output.
type answerBlocker struct{}

func (answerBlocker) Name() string            { return answerBlockRule }
func (answerBlocker) Stage() guardrails.Stage { return guardrails.StagePost }
func (answerBlocker) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	if in == nil || in.Response == nil {
		return &guardrails.CheckResult{Pass: true}
	}
	return &guardrails.CheckResult{Pass: false, Score: 0.99, Message: "answer refused by policy"}
}

// leakShortCircuit answers every request itself, before the provider, with
// the leaking answer, as a cache hit does.
type leakShortCircuit struct{}

func (leakShortCircuit) Name() string  { return "leak-short-circuit" }
func (leakShortCircuit) Priority() int { return 45 }
func (leakShortCircuit) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	content, _ := json.Marshal(postBlockLeak)
	return pipeline.ResultShortCircuit(&models.ChatCompletionResponse{ID: "cached", Object: "chat.completion",
		Model: "gpt-4o", Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: content}, FinishReason: "stop"}}})
}
func (leakShortCircuit) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// startLeakUpstream answers every chat completion with postBlockLeak, as JSON
// or as a stream (role chunk, the text in one chunk, finish with usage). The
// other APIs a surface calls answer it too (serveLeakOnOtherAPIs).
func startLeakUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	return startLeakUpstreamWith(t, nil)
}

// startLeakUpstreamWith is startLeakUpstream, but first gives each request to
// first, when it is set; a request first writes nothing for is answered as
// startLeakUpstream answers it.
func startLeakUpstreamWith(t *testing.T, first http.HandlerFunc) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if first != nil {
			tracked := &wroteTracker{ResponseWriter: w}
			if first(tracked, r); tracked.wrote {
				return
			}
		}
		if r.URL.Path != "/v1/chat/completions" {
			serveLeakOnOtherAPIs(w, r)
			return
		}
		var req models.ChatCompletionRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		model, _ := json.Marshal(req.Model)
		content, _ := json.Marshal(postBlockLeak)
		if !req.Stream {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1760000000,"model":%s,"choices":[{"index":0,"message":{"role":"assistant","content":%s},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}`, model, content)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		flusher := w.(http.Flusher)
		for _, data := range []string{
			fmt.Sprintf(`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":%s,"choices":[{"index":0,"delta":{"role":"assistant"}}]}`, model),
			fmt.Sprintf(`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":%s,"choices":[{"index":0,"delta":{"content":%s}}]}`, model, content),
			fmt.Sprintf(`{"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":%s,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}`, model),
			"[DONE]",
		} {
			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// wroteTracker records whether a postBlockSetup.upstream handler answered.
type wroteTracker struct {
	http.ResponseWriter
	wrote bool
}

func (w *wroteTracker) WriteHeader(code int) { w.wrote = true; w.ResponseWriter.WriteHeader(code) }
func (w *wroteTracker) Write(b []byte) (int, error) {
	w.wrote = true
	return w.ResponseWriter.Write(b)
}
func (w *wroteTracker) Flush() { w.ResponseWriter.(http.Flusher).Flush() }

// postBlockLogSink is the control plane's log webhook: it keeps every entry.
type postBlockLogSink struct {
	*httptest.Server
	mu      sync.Mutex
	entries []map[string]any
}

func startPostBlockLogSink(t *testing.T) *postBlockLogSink {
	t.Helper()
	s := &postBlockLogSink{}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var batch struct {
			Logs []map[string]any `json:"logs"`
		}
		_ = json.NewDecoder(r.Body).Decode(&batch)
		s.mu.Lock()
		s.entries = append(s.entries, batch.Logs...)
		s.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(s.Close)
	return s
}

// piiFromOrgConfig is the gateway's dynamic factory for an org check with a
// config, for pii-detection and a tool permissions check: the check built
// from the org's config (its remediation, its tool list), as cmd/agentcc's
// factory builds it.
func piiFromOrgConfig(name string, cfg map[string]interface{}) guardrails.Guardrail {
	if name == requestCheck {
		return pii.New(cfg)
	}
	if toolperm.IsToolPermConfig(cfg) {
		return toolperm.New(name, cfg)
	}
	return nil
}

// postBlockSetup says which output checks the gateway runs.
type postBlockSetup struct {
	staticRule string                           // a static block rule on the output: answerBlockRule or leakageCheck
	streaming  *config.StreamingGuardrailConfig // streaming guardrails for the static rule
	orgCheck   bool                             // the org enables data-leakage-prevention in block mode
	capture    bool                             // the request log keeps bodies, which assembles streamed answers
	preAnswer  bool                             // a pre-plugin answers before the provider (as a cache hit)
	preRule    bool                             // a static block rule on the request (promptBlockRule)
	action     string                           // the output rule's and the org check's action: block when empty
	upstream   http.HandlerFunc                 // answers a path before startLeakUpstream does, when it writes

	// requestRule and orgRequestCheck name a check on the request
	// (pii-detection) that the gateway runs: as a rule in the config file at
	// the pre stage, or as the org's check. requestAction is its action
	// (block when empty) and requestConfig its config (pii-detection's
	// remediation).
	requestRule     string
	orgRequestCheck string
	requestAction   string
	requestConfig   map[string]any

	// orgChecks are more checks the org enables, as its config sets them.
	orgChecks map[string]*tenant.GuardrailCheck

	// openaiModels are more models the openai provider serves (embeddings,
	// images, speech, ...), and extraProviders more providers whose upstream
	// is the test's (BaseURL left empty); both are in the org's provider
	// config too. register adds providers built in code (a fake search or
	// video provider) to the registry.
	openaiModels   []string
	extraProviders map[string]config.ProviderConfig
	register       func(*providers.Registry)
	// a2a turns on the A2A server (/v1/a2a), whose tasks are chat requests.
	a2a bool
	// failOpen turns guardrails.fail_open on: a check that times out or
	// fails lets the request go on (the gateway's default).
	failOpen bool
	// checks are more guardrails the gateway's registry holds, by name: a
	// config-file rule (staticRule) or an organization's check (orgChecks)
	// of that name runs it.
	checks map[string]guardrails.Guardrail
	// cache, when set, is the response cache's store: the cache plugin runs
	// with it.
	cache *cache.Store
}

type postBlockGateway struct {
	srv     *Server
	flusher *loggingplugin.LogFlusher
	logs    *postBlockLogSink
}

func newPostBlockGateway(t *testing.T, s postBlockSetup) *postBlockGateway {
	t.Helper()
	upstream := startLeakUpstreamWith(t, s.upstream)
	g := &postBlockGateway{logs: startPostBlockLogSink(t)}

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "post-block", Key: postBlockKey, Owner: "o", KeyType: "byok",
		Metadata: map[string]string{"org_id": postBlockOrg}},
		{Name: "post-block-ops", Key: postBlockOpsKey, Owner: "ops", KeyType: "internal"},
		{Name: "post-block-override", Key: postBlockOverrideKey, Owner: "o", KeyType: "byok",
			Metadata: map[string]string{"org_id": postBlockOrg, "allow_policy_override": "true"}},
		{Name: "post-block-internal-org", Key: postBlockInternalOrgKey, Owner: "ops", KeyType: "internal",
			Metadata: map[string]string{"org_id": postBlockOrg}}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, FailOpen: s.failOpen, DefaultTimeout: 2 * time.Second}
	action := s.action
	if action == "" {
		action = "block"
	}
	if s.staticRule != "" {
		cfg.Guardrails.Rules = []config.GuardrailRuleConfig{
			{Name: s.staticRule, Stage: "post", Mode: "sync", Action: action, Threshold: 0.5},
		}
	}
	if s.preRule {
		cfg.Guardrails.Rules = append(cfg.Guardrails.Rules,
			config.GuardrailRuleConfig{Name: promptBlockRule, Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5})
	}
	requestAction := s.requestAction
	if requestAction == "" {
		requestAction = "block"
	}
	if s.requestRule != "" {
		cfg.Guardrails.Rules = append(cfg.Guardrails.Rules,
			config.GuardrailRuleConfig{Name: s.requestRule, Stage: "pre", Mode: "sync", Action: requestAction, Threshold: 0.5})
	}
	if s.streaming != nil {
		cfg.Guardrails.Streaming = *s.streaming
	}
	cfg.Logging.RequestLogging.IncludeBodies = s.capture
	if s.a2a {
		cfg.A2A.Enabled = true
		cfg.A2A.Card = config.A2ACardConfig{Name: "gw", Description: "d", Version: "1.0.0"}
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "anthropic", Models: []string{postBlockClaude}}
	cfg.Providers["gemini"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "gemini", Models: []string{postBlockGemini}}
	cfg.Providers["broken"] = config.ProviderConfig{BaseURL: upstream.URL + "/broken", APIFormat: "openai", Models: []string{postBlockBroken}}
	openaiModels := append([]string{"gpt-4o"}, s.openaiModels...)
	openai := cfg.Providers["openai"]
	openai.Models = openaiModels
	cfg.Providers["openai"] = openai
	for id, p := range s.extraProviders {
		if p.BaseURL == "" {
			p.BaseURL = upstream.URL
		}
		cfg.Providers[id] = p
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if s.register != nil {
		s.register(registry)
	}
	grRegistry := map[string]guardrails.Guardrail{answerBlockRule: answerBlocker{}, leakageCheck: leakage.New(nil),
		promptBlockRule: promptBlocker{}, requestCheck: pii.New(s.requestConfig)}
	for name, g := range s.checks {
		grRegistry[name] = g
	}
	grEngine := guardrails.NewEngine(cfg.Guardrails, grRegistry)
	org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai":    {Enabled: true, APIKey: "org-key", Models: openaiModels},
		"anthropic": {Enabled: true, APIKey: "org-key", Models: []string{postBlockClaude}},
		"gemini":    {Enabled: true, APIKey: "org-key", Models: []string{postBlockGemini}},
		"broken":    {Enabled: true, APIKey: "org-key", Models: []string{postBlockBroken}},
	}}
	for id, p := range s.extraProviders {
		org.Providers[id] = &tenant.ProviderConfig{Enabled: true, APIKey: "org-key", Models: p.Models}
	}
	if s.orgCheck {
		org.Guardrails = &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
			leakageCheck: {Enabled: true, Action: action, ConfidenceThreshold: 0.1},
		}}
	}
	if s.orgRequestCheck != "" {
		if org.Guardrails == nil {
			org.Guardrails = &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{}}
		}
		org.Guardrails.Checks[s.orgRequestCheck] = &tenant.GuardrailCheck{Enabled: true, Action: requestAction,
			ConfidenceThreshold: 0.5, Config: s.requestConfig}
	}
	for name, check := range s.orgChecks {
		if org.Guardrails == nil {
			org.Guardrails = &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{}}
		}
		org.Guardrails.Checks[name] = check
	}
	tenants := tenant.NewStore()
	tenants.Set(postBlockOrg, org)
	ks := auth.NewKeyStore(cfg.Auth)
	policies := policy.NewStore()
	g.flusher = loggingplugin.NewLogFlusher(g.logs.URL, "", time.Hour, 100)
	logPlugin := loggingplugin.New(config.RequestLoggingConfig{Enabled: true, IncludeBodies: s.capture}, tenants)
	logPlugin.SetFlusher(g.flusher)
	plugins := []pipeline.Plugin{
		authplugin.New(ks, true),
		guardrails.NewPlugin(grEngine, grRegistry, piiFromOrgConfig, policies, tenants),
		logPlugin,
	}
	if s.preAnswer {
		plugins = append(plugins, leakShortCircuit{})
	}
	if s.cache != nil {
		plugins = append(plugins, cacheplugin.NewFromStore(s.cache, time.Minute))
	}
	g.srv = New(cfg, "", registry, pipeline.NewEngine(plugins...), ks, grEngine, policies, nil, testModelDBPtr(), tenants, nil)
	g.srv.ready.Store(true)
	if g.srv.asyncWorker != nil {
		t.Cleanup(g.srv.asyncWorker.Stop)
	}
	return g
}

// do sends body to path with the org's key, as an OpenAI, Anthropic and
// Google client would send it, and returns what the client got.
func (g *postBlockGateway) do(t *testing.T, path, body string) (int, string) {
	t.Helper()
	return g.doAs(t, postBlockKey, path, body, nil)
}

func (g *postBlockGateway) doAs(t *testing.T, key, path, body string, headers map[string]string) (int, string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("x-api-key", key)
	req.Header.Set("anthropic-version", "2023-06-01")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

func (g *postBlockGateway) chat(t *testing.T, stream bool) (int, string) {
	t.Helper()
	body := fmt.Sprintf(`{"model":"gpt-4o","stream":%t,"messages":[{"role":"user","content":%q}]}`, stream, postBlockPrompt)
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+postBlockKey)
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

// entry delivers the buffered log entries and returns the only one.
func (g *postBlockGateway) entry(t *testing.T) map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	g.flusher.Close(ctx)
	g.logs.mu.Lock()
	defer g.logs.mu.Unlock()
	if len(g.logs.entries) != 1 {
		t.Fatalf("%d log entries, want 1: %v", len(g.logs.entries), g.logs.entries)
	}
	e := g.logs.entries[0]
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", e); len(errs) > 0 {
		t.Errorf("log entry fails log-entry-v2: %v", errs)
	}
	return e
}

type errorBody struct {
	Error struct {
		Message string `json:"message"`
		Type    string `json:"type"`
		Code    string `json:"code"`
	} `json:"error"`
}

// assertBlockAnswered: the client got the guardrail's error for check, and
// not the answer.
func assertBlockAnswered(t *testing.T, code int, body, check string) {
	t.Helper()
	if strings.Contains(body, "DROP TABLE") {
		t.Errorf("the blocked answer was served: status %d, body %s", code, body)
	}
	if code != http.StatusForbidden {
		t.Errorf("status %d, want 403; body %s", code, body)
	}
	var eb errorBody
	if err := json.Unmarshal([]byte(body), &eb); err != nil {
		t.Fatalf("body is not an error response: %v; body %s", err, body)
	}
	if eb.Error.Type != models.ErrTypeGuardrail || eb.Error.Code != "content_blocked" || !strings.Contains(eb.Error.Message, check) {
		t.Errorf("error = %+v, want guardrail_error content_blocked naming %s", eb.Error, check)
	}
}

// assertBlockLogged: the request's log entry records the block's status and
// the guardrail action; check, when set, is the guardrail it names.
func assertBlockLogged(t *testing.T, e map[string]any, check string) {
	t.Helper()
	if e["status_code"] != float64(http.StatusForbidden) || e["is_error"] != true {
		t.Errorf("log status_code %v is_error %v, want 403 and true", e["status_code"], e["is_error"])
	}
	if e["guardrail_triggered"] != true {
		t.Errorf("log guardrail_triggered = %v, want true", e["guardrail_triggered"])
	}
	if msg, _ := e["error_message"].(string); msg == "" {
		t.Error("log error_message is empty")
	}
	meta, _ := e["metadata"].(map[string]any)
	if meta["guardrail_action"] != "blocked" {
		t.Errorf("log metadata.guardrail_action = %v, want blocked", meta["guardrail_action"])
	}
	if check == "" {
		return
	}
	if meta["guardrail_name"] != check {
		t.Errorf("log metadata.guardrail_name = %v, want %s", meta["guardrail_name"], check)
	}
	results, _ := e["guardrail_results"].([]any)
	found := false
	for _, r := range results {
		if m, _ := r.(map[string]any); m["name"] == check && m["action"] == "block" {
			found = true
		}
	}
	if !found {
		t.Errorf("log guardrail_results = %v, want %s with action block", e["guardrail_results"], check)
	}
}

// sseEvents is the data payloads of a stream, in order.
func sseEvents(body string) []string {
	var out []string
	sc := bufio.NewScanner(strings.NewReader(body))
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		if data, ok := strings.CutPrefix(sc.Text(), "data: "); ok {
			out = append(out, data)
		}
	}
	return out
}

// streamError is the stream's error event and its position, or -1.
func streamError(events []string) (errorBody, int) {
	for i, data := range events {
		var eb errorBody
		if json.Unmarshal([]byte(data), &eb) == nil && eb.Error.Type != "" {
			return eb, i
		}
	}
	return errorBody{}, -1
}

func leakPosition(events []string) int {
	for i, data := range events {
		if strings.Contains(data, "DROP TABLE") {
			return i
		}
	}
	return -1
}

// assertStreamEndsWithGuardrailError: the stream carries one error event,
// the guardrail's (wantCode), and no internal error.
func assertStreamEndsWithGuardrailError(t *testing.T, body, wantCode string) (events []string, at int) {
	t.Helper()
	events = sseEvents(body)
	eb, at := streamError(events)
	if at < 0 {
		t.Fatalf("the stream carries no error event; body %s", body)
	}
	if eb.Error.Type != models.ErrTypeGuardrail || eb.Error.Code != wantCode {
		t.Errorf("stream error = %+v, want guardrail_error %s", eb.Error, wantCode)
	}
	n := 0
	for _, data := range events {
		var e errorBody
		if json.Unmarshal([]byte(data), &e) == nil && e.Error.Type != "" {
			n++
		}
	}
	if n != 1 {
		t.Errorf("the stream carries %d error events, want 1; body %s", n, body)
	}
	return events, at
}

// A static rule on the output that blocks the answer: 403 content_blocked
// naming the rule, logged with that status and the guardrail action.
func TestPostBlockNonStream_StaticRuleAnswersTheGuardrailError(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{staticRule: answerBlockRule})
	code, body := g.chat(t, false)
	assertBlockAnswered(t, code, body, answerBlockRule)
	assertBlockLogged(t, g.entry(t), answerBlockRule)
}

// An org's data-leakage-prevention check in block mode: the answer is
// withheld and answered with 403 content_blocked.
func TestPostBlockNonStream_OrgCheckAnswersTheGuardrailError(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{orgCheck: true})
	code, body := g.chat(t, false)
	assertBlockAnswered(t, code, body, leakageCheck)
	assertBlockLogged(t, g.entry(t), leakageCheck)
}

// An answer a pre-plugin gave (as a cache hit) that the output check then
// blocks: 403 content_blocked for a non-stream request and for a stream
// request.
func TestPostBlock_PreAnsweredRequestAnswersTheGuardrailError(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(streamLabel(stream), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{staticRule: answerBlockRule, preAnswer: true})
			code, body := g.chat(t, stream)
			assertBlockAnswered(t, code, body, answerBlockRule)
			assertBlockLogged(t, g.entry(t), answerBlockRule)
		})
	}
}

// The streaming checker stops the stream at the chunk the rule matches: the
// client gets the guardrail's error event and not the chunk, and the log
// records the block.
func TestPostBlockStream_CheckerStopsTheStream(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck,
		streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 5, FailureAction: "stop"}})
	code, body := g.chat(t, true)
	if code != http.StatusOK {
		t.Fatalf("status %d, want the stream's 200; body %s", code, body)
	}
	events, _ := assertStreamEndsWithGuardrailError(t, body, "stream_blocked")
	if at := leakPosition(events); at >= 0 {
		t.Errorf("the stream sent the leaking chunk; body %s", body)
	}
	assertBlockLogged(t, g.entry(t), "")
}

// The streaming checker refuses the stream at its end: the stream it held
// carries none of the answer, only the checker's error event and [DONE], and
// the log records the block.
func TestPostBlockStream_CheckerBlocksAtTheEnd(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck,
		streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 1 << 20, FailureAction: "stop"}})
	code, body := g.chat(t, true)
	if code != http.StatusOK {
		t.Fatalf("status %d, want the stream's 200; body %s", code, body)
	}
	events, at := assertStreamEndsWithGuardrailError(t, body, "stream_blocked")
	if leak := leakPosition(events); leak >= 0 {
		t.Errorf("the stream sent the blocked answer (at %d, error at %d); body %s", leak, at, body)
	}
	if at != 0 || events[len(events)-1] != "[DONE]" || len(events) != 2 {
		t.Errorf("want only the error event, then [DONE]; body %s", body)
	}
	assertBlockLogged(t, g.entry(t), "")
}

// With no streaming checker, the output checks run on the assembled answer
// when the stream ends. A block there, by a static rule or an org's check,
// holds the stream: it carries none of the answer, only the guardrail's error
// event and [DONE], and the log records the block.
func TestPostBlockStream_EndOfStreamCheckAnswersTheGuardrailError(t *testing.T) {
	for _, tc := range []struct {
		name  string
		setup postBlockSetup
	}{
		{"static rule", postBlockSetup{staticRule: leakageCheck, capture: true}},
		{"org check", postBlockSetup{orgCheck: true, capture: true}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			code, body := g.chat(t, true)
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %s", code, body)
			}
			events, at := assertStreamEndsWithGuardrailError(t, body, "content_blocked")
			if leak := leakPosition(events); leak >= 0 {
				t.Errorf("the stream sent the blocked answer (at %d, error at %d); body %s", leak, at, body)
			}
			if last := events[len(events)-1]; last != "[DONE]" || at != 0 || len(events) != 2 {
				t.Errorf("want only the error event, then [DONE]; body %s", body)
			}
			assertBlockLogged(t, g.entry(t), leakageCheck)
		})
	}
}

// In disclaimer mode the streaming checker's disclaimer stands: the stream
// gets the disclaimer and no error event.
func TestPostBlockStream_DisclaimerModeKeepsTheDisclaimer(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck, capture: true,
		streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 1 << 20, FailureAction: "disclaimer"}})
	code, body := g.chat(t, true)
	if code != http.StatusOK {
		t.Fatalf("status %d, want the stream's 200; body %s", code, body)
	}
	if !strings.Contains(body, "Content warning") {
		t.Errorf("the stream has no disclaimer; body %s", body)
	}
	if _, at := streamError(sseEvents(body)); at >= 0 {
		t.Errorf("a disclaimer-mode stream got an error event; body %s", body)
	}
}

// Control: with no output check that fires, the answer is served and logged
// with 200.
func TestPostBlock_PassingAnswerUnchanged(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(streamLabel(stream), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{capture: true})
			code, body := g.chat(t, stream)
			if code != http.StatusOK || !strings.Contains(body, "DROP TABLE") {
				t.Fatalf("status %d, body %s; want 200 with the answer", code, body)
			}
			if _, at := streamError(sseEvents(body)); at >= 0 {
				t.Errorf("an unchecked answer got an error event; body %s", body)
			}
			e := g.entry(t)
			if e["status_code"] != float64(http.StatusOK) || e["is_error"] != false || e["guardrail_triggered"] != false {
				t.Errorf("log status_code %v is_error %v guardrail_triggered %v, want 200, false, false",
					e["status_code"], e["is_error"], e["guardrail_triggered"])
			}
		})
	}
}
