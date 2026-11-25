package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The post check requires complete output, not a per-chunk approximation.
// Its messages deliberately never include the input or provider response.
type runtimePostCheck struct{}

func (runtimePostCheck) Name() string            { return "runtime-post" }
func (runtimePostCheck) Stage() guardrails.Stage { return guardrails.StagePost }
func (runtimePostCheck) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	if in.Response != nil {
		for _, choice := range in.Response.Choices {
			if strings.Contains(string(choice.Message.Content), "runtime-sensitive") {
				return &guardrails.CheckResult{Score: 1, Message: "synthetic output violation"}
			}
		}
	}
	return &guardrails.CheckResult{Pass: true}
}

type runtimePolicyAudit struct {
	mu        sync.Mutex
	results   []models.GuardrailResult
	usage     *models.Usage
	finalized chan struct{}
}

func (*runtimePolicyAudit) Name() string  { return "runtime-policy-audit" }
func (*runtimePolicyAudit) Priority() int { return 999 }
func (*runtimePolicyAudit) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (a *runtimePolicyAudit) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.results = append([]models.GuardrailResult(nil), rc.GuardrailResults...)
	if rc.Response != nil && rc.Response.Usage != nil {
		usage := *rc.Response.Usage
		a.usage = &usage
	}
	if a.finalized != nil {
		select {
		case a.finalized <- struct{}{}:
		default:
		}
	}
	return pipeline.ResultContinue()
}
func (a *runtimePolicyAudit) snapshot() ([]models.GuardrailResult, *models.Usage) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]models.GuardrailResult(nil), a.results...), a.usage
}

func runtimePolicyServer(t *testing.T, upstreamURL string, cfg *tenant.GuardrailConfig, static map[string]guardrails.Guardrail) (*Server, *runtimePolicyAudit) {
	t.Helper()
	c := config.DefaultConfig()
	c.Auth.Enabled = true
	c.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "sk-agentcc-runtime-a", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-runtime"}},
		{Name: "unselected", Key: "sk-agentcc-runtime-b", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-runtime"}},
		{Name: "other-org", Key: "sk-agentcc-runtime-c", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-other"}},
	}
	c.Providers["openai"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5"}}
	c.Providers["anthropic"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "synthetic", APIFormat: "anthropic", Models: []string{"claude-sonnet-5-5"}}
	c.Providers["gemini"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "synthetic", APIFormat: "gemini", Models: []string{"gemini-3.8-flash"}}
	// No static rules and no persistent/captured stream bodies.
	c.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	store := tenant.NewStore()
	store.Set("org-runtime", &tenant.OrgConfig{Guardrails: cfg})
	engine := guardrails.NewEngine(c.Guardrails, static)
	factory := func(name string, cfg map[string]interface{}) guardrails.Guardrail {
		if name == "runtime-post" {
			return runtimePostCheck{}
		}
		if expression.IsExpressionConfig(cfg) {
			return expression.New(name, cfg)
		}
		return nil
	}
	registry, err := providers.NewRegistry(c)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(c.Auth)
	audit := &runtimePolicyAudit{finalized: make(chan struct{}, 1)}
	srv := New(c, "", registry, pipeline.NewEngine(authplugin.New(keys, true), guardrails.NewPlugin(engine, static, factory, nil, store), audit), keys, engine, nil, nil, testModelDBPtr(), store, nil)
	srv.handlers.SetCaptureStreamContent(false)
	srv.ready.Store(true)
	return srv, audit
}

func runtimePolicy(name, mode, action string, enabled bool, cfg map[string]interface{}) *tenant.KeyGuardrailPolicy {
	return &tenant.KeyGuardrailPolicy{ID: "runtime-policy", Name: "Runtime policy", Mode: mode, KeyIDs: []string{"key_1"}, Checks: map[string]*tenant.GuardrailCheck{
		name: {Enabled: enabled, Action: action, ConfidenceThreshold: .5, Config: cfg},
	}}
}

func runtimePolicySend(srv *Server, rt guardedRoute, stream bool, key string) *httptest.ResponseRecorder {
	r := rt.request(stream, "synthetic clean prompt")
	r.Header.Set("Authorization", "Bearer sk-agentcc-runtime-"+key)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

func assertRuntimeAttribution(t *testing.T, audit *runtimePolicyAudit, outcome, mode string) {
	t.Helper()
	results, _ := audit.snapshot()
	for _, result := range results {
		encoded, _ := json.Marshal(result)
		if strings.Contains(string(encoded), "runtime-sensitive") || strings.Contains(string(encoded), "synthetic clean prompt") {
			t.Fatal("attribution contains request/response content")
		}
		if result.PolicyID == "runtime-policy" && result.PolicyScope == "key" && result.Mode == mode && result.Outcome == outcome {
			return
		}
	}
	t.Fatalf("missing %s/%s policy attribution: %+v", mode, outcome, results)
}

func TestKeyPolicyRuntimePostAdmissionHTTP(t *testing.T) {
	for _, rt := range guardedRoutes {
		for _, failOpen := range []bool{false, true} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/fail_open=%v/stream=%v", rt.name, failOpen, stream), func(t *testing.T) {
					up := &dialectUpstream{reply: "runtime-sensitive"}
					upstream := httptest.NewServer(up)
					defer upstream.Close()
					cfg := &tenant.GuardrailConfig{FailOpen: failOpen, KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
					srv, audit := runtimePolicyServer(t, upstream.URL, cfg, nil)
					w := runtimePolicySend(srv, rt, stream, "a")
					wantCalls := 1
					code := "content_blocked"
					// Native envelopes retain the safe gateway code, not arbitrary
					// evaluator text that could itself contain rejected content.
					if stream {
						wantCalls, code = 0, "stream_guardrail_unsupported"
					}
					if w.Code != 403 || up.served() != wantCalls {
						t.Fatalf("status=%d calls=%d; want 403/%d (stream=%v)", w.Code, up.served(), wantCalls, stream)
					}
					if !strings.Contains(w.Body.String(), code) {
						t.Fatalf("missing explicit %s error: %s", code, w.Body.String())
					}
					for _, forbidden := range []string{"runtime-sensitive", "[DONE]", "response.completed", "message_stop", "finishReason"} {
						if strings.Contains(w.Body.String(), forbidden) {
							t.Fatalf("rejected response contains %q", forbidden)
						}
					}
					assertRuntimeAttribution(t, audit, "blocked", "enforce")
					if !stream {
						_, usage := audit.snapshot()
						if usage == nil || usage.TotalTokens != 8 {
							t.Fatalf("post block lost billed usage: %+v", usage)
						}
					}
				})
			}
		}
	}
}

func TestKeyPolicyRuntimeUnavailableHTTP(t *testing.T) {
	for _, name := range []string{"pii-detectoin", "custom-risk"} {
		for _, mode := range []string{"enforce", "monitor"} {
			for _, failOpen := range []bool{false, true} {
				for _, enabled := range []bool{false, true} {
					for _, stream := range []bool{false, true} {
						t.Run(fmt.Sprintf("%s/%s/open=%v/enabled=%v/stream=%v", name, mode, failOpen, enabled, stream), func(t *testing.T) {
							up := &dialectUpstream{reply: "safe reply"}
							upstream := httptest.NewServer(up)
							defer upstream.Close()
							cfg := &tenant.GuardrailConfig{FailOpen: failOpen, KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy(name, mode, "block", enabled, map[string]interface{}{})}}
							srv, audit := runtimePolicyServer(t, upstream.URL, cfg, nil)
							w := runtimePolicySend(srv, guardedRoutes[0], stream, "a")
							blocked := enabled && mode == "enforce" && !failOpen
							wantStatus, wantCalls := 200, 1
							if blocked {
								wantStatus, wantCalls = 503, 0
							}
							if w.Code != wantStatus || up.served() != wantCalls {
								t.Fatalf("status=%d calls=%d; want %d/%d", w.Code, up.served(), wantStatus, wantCalls)
							}
							if blocked && !strings.Contains(w.Body.String(), "guardrail_unavailable") {
								t.Fatal("missing unavailable error")
							}
							if enabled {
								outcome := "logged"
								if blocked {
									outcome = "blocked"
								}
								assertRuntimeAttribution(t, audit, outcome, mode)
							} else if results, _ := audit.snapshot(); len(results) != 0 {
								t.Fatalf("disabled check recorded results: %+v", results)
							}
						})
					}
				}
			}
		}
	}
}

func TestKeyPolicyRuntimeAdmissionControlsHTTP(t *testing.T) {
	for _, rt := range guardedRoutes {
		for _, tc := range []struct {
			name, mode, action, key string
			enabled                 bool
			check                   string
			cfg                     map[string]interface{}
			static                  bool
		}{
			{name: "unselected", mode: "enforce", action: "block", key: "b", enabled: true, check: "runtime-post"},
			{name: "other-org", mode: "enforce", action: "block", key: "c", enabled: true, check: "runtime-post"},
			{name: "monitor", mode: "monitor", action: "block", key: "a", enabled: true, check: "runtime-post"},
			{name: "disabled", mode: "enforce", action: "block", key: "a", check: "runtime-post"},
			{name: "log", mode: "enforce", action: "log", key: "a", enabled: true, check: "runtime-post"},
			{name: "pre-expression", mode: "enforce", action: "block", key: "a", enabled: true, check: "custom-risk", cfg: map[string]interface{}{"expression": "false"}},
			{name: "static-fallback", mode: "enforce", action: "block", key: "a", enabled: true, check: "custom-risk", static: true},
		} {
			t.Run(rt.name+"/"+tc.name, func(t *testing.T) {
				up := &dialectUpstream{reply: "runtime-sensitive"}
				upstream := httptest.NewServer(up)
				defer upstream.Close()
				cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy(tc.check, tc.mode, tc.action, tc.enabled, tc.cfg)}}
				var static map[string]guardrails.Guardrail
				if tc.static {
					static = map[string]guardrails.Guardrail{"custom-risk": expression.New("custom-risk", map[string]interface{}{"expression": "false"})}
				}
				srv, _ := runtimePolicyServer(t, upstream.URL, cfg, static)
				w := runtimePolicySend(srv, rt, true, tc.key)
				if w.Code != 200 || up.served() != 1 || !strings.Contains(w.Body.String(), "runtime-sensitive") {
					t.Fatalf("unaffected stream status=%d calls=%d", w.Code, up.served())
				}
			})
		}
	}
}
