package server

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/contentmod"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Real socket HTTP at both gateway and synthetic provider. The factory calls the
// production constructor; static fallback uses a separately constructed original.
func newNumericHTTPFixture(t *testing.T, gr *tenant.GuardrailConfig, cfg map[string]interface{}, dynamic bool) (*textViewR4Fixture, *runtimePolicyAudit) {
	t.Helper()
	f := &textViewR4Fixture{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			fmt.Fprint(w, `{"data":[]}`)
			return
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("provider read: %v", err)
			return
		}
		f.mu.Lock()
		f.bodies = append(f.bodies, body)
		f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.Write(textViewR4Reply("numeric-safe-provider-reply"))
	}))
	t.Cleanup(upstream.Close)
	c := config.DefaultConfig()
	c.Auth.Enabled = true
	c.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "sk-agentcc-text-selected", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "numeric-org"}},
		{Name: "unselected", Key: "sk-agentcc-text-unselected", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "numeric-org"}},
	}
	c.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5"}}
	c.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	store := tenant.NewStore()
	store.Set("numeric-org", &tenant.OrgConfig{Guardrails: gr})
	static := map[string]guardrails.Guardrail{"content-moderation": contentmod.New(cfg)}
	var factory guardrails.DynamicFactory
	if dynamic {
		factory = func(name string, cfg map[string]interface{}) guardrails.Guardrail {
			if name == "content-moderation" {
				return contentmod.New(cfg)
			}
			return nil
		}
	}
	engine := guardrails.NewEngine(c.Guardrails, static)
	registry, err := providers.NewRegistry(c)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(c.Auth)
	audit := &runtimePolicyAudit{finalized: make(chan struct{}, 1)}
	srv := New(c, "", registry, pipeline.NewEngine(authplugin.New(keys, true), guardrails.NewPlugin(engine, static, factory, nil, store), audit), keys, engine, nil, nil, testModelDBPtr(), store, nil)
	srv.ready.Store(true)
	t.Cleanup(srv.asyncWorker.Stop)
	f.gateway = httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(f.gateway.Close)
	return f, audit
}

func TestNumericReadinessRealHTTP(t *testing.T) {
	const trigger = "you are stupid; you're an idiot; shut up"
	for _, dynamic := range []bool{true, false} {
		for _, pipelineMode := range []string{"sequential", "parallel"} {
			for _, tc := range []struct {
				name, mode, action, key, input, invalid, outcome string
				failOpen                                         bool
				status                                           int
			}{
				{name: "block", mode: "enforce", action: "block", key: "selected", input: trigger, outcome: "blocked", status: 403},
				{name: "block-fail-open", mode: "enforce", action: "block", key: "selected", input: trigger, failOpen: true, outcome: "blocked", status: 403},
				{name: "safe", mode: "enforce", action: "block", key: "selected", input: "The weather is pleasant today.", status: 200},
				{name: "safe-fail-open", mode: "enforce", action: "block", key: "selected", input: "The weather is pleasant today.", failOpen: true, status: 200},
				{name: "monitor", mode: "monitor", action: "block", key: "selected", input: trigger, outcome: "would_block", status: 200},
				{name: "warn", mode: "enforce", action: "warn", key: "selected", input: trigger, outcome: "warned", status: 200},
				{name: "log", mode: "enforce", action: "log", key: "selected", input: trigger, outcome: "logged", status: 200},
				{name: "unselected", mode: "enforce", action: "block", key: "unselected", input: trigger, status: 200},
				{name: "overflow-block", mode: "enforce", action: "block", key: "selected", input: "The weather is pleasant today.", invalid: "overflow", outcome: "blocked", status: 503},
				{name: "overflow-fail-open", mode: "enforce", action: "block", key: "selected", input: "The weather is pleasant today.", invalid: "overflow", failOpen: true, outcome: "logged", status: 200},
				{name: "overflow-monitor", mode: "monitor", action: "block", key: "selected", input: "The weather is pleasant today.", invalid: "overflow", outcome: "logged", status: 200},
				{name: "overflow-warn", mode: "enforce", action: "warn", key: "selected", input: "The weather is pleasant today.", invalid: "overflow", outcome: "warned", status: 200},
				{name: "overflow-log", mode: "enforce", action: "log", key: "selected", input: "The weather is pleasant today.", invalid: "overflow", outcome: "logged", status: 200},
				{name: "overflow-unselected", mode: "enforce", action: "block", key: "unselected", input: trigger, invalid: "overflow", status: 200},
				{name: "malformed-count", mode: "enforce", action: "block", key: "selected", input: "The weather is pleasant today.", invalid: "malformed", outcome: "blocked", status: 503},
				{name: "unreachable", mode: "enforce", action: "block", key: "selected", input: "The weather is pleasant today.", invalid: "unreachable", outcome: "blocked", status: 503},
			} {
				t.Run(fmt.Sprintf("dynamic=%v/%s/%s", dynamic, pipelineMode, tc.name), func(t *testing.T) {
					cfg := map[string]interface{}{
						"categories":         []interface{}{"toxicity", "hate_speech", "violence", "sexual", "self_harm"},
						"category_threshold": 3.0,
						"weights":            map[string]interface{}{"toxicity": math.MaxFloat64, "hate_speech": math.Ldexp(1, 969), "violence": math.Ldexp(1, 969), "sexual": math.Ldexp(1, 969), "self_harm": math.Ldexp(1, 969)},
					}
					switch tc.invalid {
					case "overflow":
						cfg["weights"].(map[string]interface{})["sexual"] = math.MaxFloat64
					case "malformed":
						cfg["category_threshold"] = 3.5
					case "unreachable":
						cfg["category_threshold"] = 50.0
					}
					policy := runtimePolicy("content-moderation", tc.mode, tc.action, true, cfg)
					policy.Checks["content-moderation"].ConfidenceThreshold = .8
					gr := &tenant.GuardrailConfig{PipelineMode: pipelineMode, FailOpen: tc.failOpen, KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}
					f, audit := newNumericHTTPFixture(t, gr, cfg, dynamic)
					// First request constructs; subsequent requests hit the real dynamic
					// cache (or reuse the untouched static fallback).
					for attempt := 0; attempt < 4; attempt++ {
						status, body, bodies := f.send(t, tc.key, tc.input)
						select {
						case <-audit.finalized:
						case <-time.After(2 * time.Second):
							t.Fatal("audit did not finalize")
						}
						wantCalls := 0
						if tc.status == 200 {
							wantCalls = attempt + 1
						}
						t.Logf("attempt=%d status=%d provider_calls=%d released_bytes=%d", attempt, status, len(bodies), len(body))
						if status != tc.status || len(bodies) != wantCalls {
							t.Fatalf("status=%d calls=%d want=%d/%d body=%s", status, len(bodies), tc.status, wantCalls, body)
						}
						if status == 200 {
							if !strings.Contains(string(body), "numeric-safe-provider-reply") {
								t.Fatalf("provider response not released: %s", body)
							}
							var request struct {
								Messages []struct {
									Content string `json:"content"`
								} `json:"messages"`
							}
							if err := json.Unmarshal(bodies[len(bodies)-1], &request); err != nil || len(request.Messages) != 1 || request.Messages[0].Content != tc.input {
								t.Fatal("provider did not receive unchanged input")
							}
						} else {
							code := "content_blocked"
							if status == 503 {
								code = "guardrail_unavailable"
							}
							if !strings.Contains(string(body), code) || strings.Contains(string(body), tc.input) || strings.Contains(string(body), "numeric-safe-provider-reply") {
								t.Fatalf("refusal missing or content released: %s", body)
							}
						}
						results, _ := audit.snapshot()
						if tc.outcome == "" {
							if len(results) != 0 {
								t.Fatalf("safe/unselected traffic recorded a failure: %+v", results)
							}
						} else {
							assertRuntimeAttribution(t, audit, tc.outcome, tc.mode)
							if len(results) != 1 || results[0].Name != "content-moderation" || strings.Contains(results[0].Message, "unavailable") != (tc.invalid != "") {
								t.Fatalf("actual verdict versus unavailable attribution mismatch: %+v", results)
							}
							if tc.invalid == "" && results[0].Score != 1 {
								t.Fatalf("nonfinite or unexpected selected score: %g", results[0].Score)
							}
						}
					}
				})
			}
		}
	}
}
