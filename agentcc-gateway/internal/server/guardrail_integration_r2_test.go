package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These are the missing nonblocking native-token controls, not a repetition of
// the lane's Enforce Block/token refusal matrix. Every request uses the actual
// native HTTP provider and must preserve both token IDs and supported options.
func TestGuardrailIntegrationR2NativeTokenObservations(t *testing.T) {
	for _, tc := range []struct {
		name, prompt, mode, action, pipeline string
		failOpen                             bool
	}{
		{"monitor-vector", `[101,202]`, "monitor", "block", "sequential", false},
		{"monitor-batch", `[[101,202],[303]]`, "monitor", "block", "parallel", true},
		{"warn-vector", `[101,202]`, "enforce", "warn", "sequential", false},
		{"warn-batch", `[[101,202],[303]]`, "enforce", "warn", "parallel", true},
		{"log-vector", `[101,202]`, "enforce", "log", "sequential", true},
		{"log-batch", `[[101,202],[303]]`, "enforce", "log", "parallel", false},
	} {
		for _, key := range []string{"a", "b"} {
			t.Run(tc.name+"/key="+key, func(t *testing.T) {
				org := routeR2Org(routeR2Policy(tc.mode, tc.action, "pii-detection", nil))
				org.PipelineMode, org.FailOpen = tc.pipeline, tc.failOpen
				srv, wire, audit := routeR2Server(t, routeR2Options{org: org})
				body := `{"model":"gpt-3.5-turbo-instruct","prompt":` + tc.prompt + `,"max_tokens":17,"temperature":0.2,"suffix":"clean suffix","user":"synthetic-user"}`
				var req models.CompletionRequest
				if err := json.Unmarshal([]byte(body), &req); err != nil {
					t.Fatal(err)
				}
				t.Logf("pipeline=%s fail_open=%v timeout_ms=1000 mode=%s action=%s key=%s stage=pre", tc.pipeline, tc.failOpen, tc.mode, tc.action, key)
				w := routeR2Send(srv, "/v1/completions", body, key)
				routeR2AssertWire(t, wire, http.StatusOK, w, "/v1/completions", routeR2JSON(t, req))
				if key == "b" {
					integrationR2AssertUnattributed(t, audit)
					return
				}
				outcome := "logged"
				if tc.action == "warn" && !tc.failOpen {
					outcome = "warned"
				}
				integrationR2AssertUnavailable(t, audit, "pii-detection", tc.mode, outcome, "guardrail_unavailable", tc.action)
			})
		}
	}
}

// Existing lane tests cover Block/Monitor for these capabilities. Add only the
// omitted Warn/Log controls, under parallel execution, with real wire bytes.
func TestGuardrailIntegrationR2UnsupportedNonblocking(t *testing.T) {
	for _, rt := range []struct{ name, path, upstream, body, check, code string }{
		{"embedding-tokens", "/v1/embeddings", "/v1/embeddings", `{"model":"text-embedding-3-small","input":[101,202]}`, "pii-detection", "input_guardrail_unsupported"},
		{"embedding-output", "/v1/embeddings", "/v1/embeddings", `{"model":"text-embedding-3-small","input":"clean text"}`, "r2-post", "output_guardrail_unsupported"},
		{"speech-output", "/v1/audio/speech", "/v1/audio/speech", `{"model":"tts-1","input":"clean synthesis","voice":"alloy"}`, "r2-post", "output_guardrail_unsupported"},
		{"speech-stream-output", "/v1/audio/speech/stream", "/v1/audio/speech", `{"model":"tts-1","input":"clean synthesis","voice":"alloy"}`, "r2-post", "output_guardrail_unsupported"},
	} {
		for _, action := range []string{"warn", "log"} {
			t.Run(rt.name+"/"+action, func(t *testing.T) {
				org := routeR2Org(routeR2Policy("enforce", action, rt.check, nil))
				org.PipelineMode = "parallel"
				srv, wire, audit := routeR2Server(t, routeR2Options{org: org})
				t.Logf("pipeline=parallel fail_open=false timeout_ms=1000 mode=enforce action=%s key=a capability=%s", action, rt.code)
				w := routeR2Send(srv, rt.path, rt.body, "a")
				routeR2AssertWire(t, wire, http.StatusOK, w, rt.upstream, rt.body)
				outcome := "logged"
				if action == "warn" {
					outcome = "warned"
				}
				// Execution errors use the existing sanitized unavailable envelope;
				// the capability reason is not exposed as a successful check.
				integrationR2AssertUnavailable(t, audit, rt.check, "enforce", outcome, "guardrail_unavailable", action)
				if strings.Contains(rt.path, "/audio/speech") {
					routeR2AssertAudio(t, rt.path, w)
				}
			})
		}
	}
}

// R-enforcement-repair:R2-ENF-1: parallel execution must preserve the same
// native inspection-only vs translated wire-backed remediation boundary.
func TestGuardrailIntegrationR2ParallelRemediation(t *testing.T) {
	for _, source := range []string{"static", "org", "key"} {
		for _, translated := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/translated=%v", source, translated), func(t *testing.T) {
				org := routeR2Org(routeR2Policy("enforce", "block", "pii-detection", nil))
				org.PipelineMode = "parallel"
				redact := &tenant.GuardrailCheck{Enabled: true, Action: "log", ConfidenceThreshold: .8, Config: map[string]interface{}{"remediation": "redact"}}
				if source == "org" {
					org.Checks = map[string]*tenant.GuardrailCheck{"pii-detection": redact}
				} else if source == "key" {
					org.KeyPolicies = append(org.KeyPolicies, &tenant.KeyGuardrailPolicy{ID: "r2-redact", Name: "R2 redact first", Mode: "enforce", Priority: 1, KeyIDs: []string{"key_1"}, Checks: map[string]*tenant.GuardrailCheck{"pii-detection": redact}})
				}
				srv, wire, audit := routeR2Server(t, routeR2Options{org: org, staticRedact: source == "static", translated: translated})
				body := `{"model":"gpt-5.5","prompt":"contact alice@example.com","max_tokens":17,"user":"synthetic-user"}`
				status, path, expected := http.StatusForbidden, "/v1/completions", ""
				if translated {
					var req models.CompletionRequest
					if err := json.Unmarshal([]byte(body), &req); err != nil {
						t.Fatal(err)
					}
					chat := req.ToChatRequest()
					chat.Messages[0].Content = json.RawMessage(`"contact [REDACTED:email]"`)
					status, path, expected = http.StatusOK, "/v1/chat/completions", routeR2JSON(t, chat)
				}
				t.Logf("pipeline=parallel fail_open=false timeout_ms=1000 key=a mode=enforce block_priority=2 earlier_redactor=%s translated=%v", source, translated)
				w := routeR2Send(srv, "/v1/completions", body, "a")
				routeR2AssertWire(t, wire, status, w, path, expected)
				if !translated {
					routeR2AssertOutcome(t, audit, "pii-detection", "enforce", "blocked")
				}
			})
		}
	}
}

func integrationR2AssertUnattributed(t *testing.T, audit *runtimePolicyAudit) {
	t.Helper()
	results, _ := audit.snapshot()
	t.Logf("sanitized_attribution=%s", routeR2JSON(t, results))
	if len(results) != 0 {
		t.Fatalf("unselected/disabled/clean request attributed: %+v", results)
	}
}

func integrationR2AssertUnavailable(t *testing.T, audit *runtimePolicyAudit, name, mode, outcome, code, action string) {
	t.Helper()
	r := routeR2AssertOutcome(t, audit, name, mode, outcome)
	if !strings.Contains(r.Message, code) || strings.Contains(r.Message, "restricted") {
		t.Fatalf("missing truthful sanitized execution failure %s: %+v", code, r)
	}
	if mode == "monitor" && r.WouldAction != action {
		t.Fatalf("monitor would_action=%q want=%q", r.WouldAction, action)
	}
}

// Unlike routeR2Server, this fixture starts with no org config and uses the real
// keyword leaf constructor and admin cache-invalidation hook. Only the provider
// is a localhost mock; the admin push, resolver, validator and handlers are real.
func integrationR2ConfigServer(t *testing.T) (*Server, *routeR2Wire, *runtimePolicyAudit) {
	t.Helper()
	wire := &routeR2Wire{}
	up := httptest.NewServer(wire)
	t.Cleanup(up.Close)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "synthetic-integration-admin"
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "sk-agentcc-r2-a", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-r2"}},
		{Name: "other-key", Key: "sk-agentcc-r2-b", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-r2"}},
		{Name: "other-org", Key: "sk-agentcc-r2-c", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "other-org"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5", "text-embedding-3-small", "tts-1"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	store := tenant.NewStore()
	static := map[string]guardrails.Guardrail{"deployed-custom": pii.New(nil)}
	engine := guardrails.NewEngine(cfg.Guardrails, static)
	factory := func(name string, c map[string]interface{}) guardrails.Guardrail {
		if name == "keyword-blocklist" {
			return blocklist.New(c)
		}
		return nil
	}
	gr := guardrails.NewPlugin(engine, static, factory, nil, store)
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	audit := &runtimePolicyAudit{finalized: make(chan struct{}, 1)}
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true), gr, audit), keys, engine, nil, nil, testModelDBPtr(), store, gr.InvalidateDynamicCache)
	srv.handlers.SetCaptureStreamContent(false)
	srv.ready.Store(true)
	return srv, wire, audit
}

func integrationR2Push(t *testing.T, srv *Server, org *tenant.GuardrailConfig) {
	t.Helper()
	body := routeR2JSON(t, map[string]interface{}{"guardrails": org})
	r := httptest.NewRequest(http.MethodPut, "/-/orgs/org-r2/config", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer synthetic-integration-admin")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("config push status=%d body=%s", w.Code, w.Body.String())
	}
	t.Logf("config_push_status=%d pushed_guardrails=%s", w.Code, routeR2JSON(t, org))
}

// C-django-frontend:C1 x C-handlers-tenant:C1/C2. The lane already exhaustively
// covers bad shapes/settings on chat. Distribute those shapes over the repaired
// routes here, then exercise admission, isolation and correction on each route.
// This is gateway config-push evidence, not a claim of a Django save/UI test.
func TestGuardrailIntegrationR2ConfigPushAcrossRoutes(t *testing.T) {
	for _, rt := range []struct {
		name, path, upstream, body string
		bad                        map[string]interface{}
	}{
		{"native-string", "/v1/completions", "/v1/completions", `{"model":"gpt-5.5","prompt":"restricted","user":"synthetic-user"}`, map[string]interface{}{"words": "restricted"}},
		{"embedding-empty", "/v1/embeddings", "/v1/embeddings", `{"model":"text-embedding-3-small","input":["clean text","restricted"],"user":"synthetic-user"}`, map[string]interface{}{"words": []interface{}{}}},
		{"speech-missing", "/v1/audio/speech", "/v1/audio/speech", `{"model":"tts-1","input":"restricted","voice":"alloy","speed":1.25}`, map[string]interface{}{}},
		{"speech-stream-whitespace", "/v1/audio/speech/stream", "/v1/audio/speech", `{"model":"tts-1","input":"restricted","voice":"alloy","speed":1.25}`, map[string]interface{}{"words": []interface{}{" \t"}}},
		{"responses-mixed", "/v1/responses", "/v1/responses", `{"model":"gpt-5.5","input":"restricted","user":"restricted"}`, map[string]interface{}{"words": []interface{}{"restricted", 42}}},
	} {
		t.Run(rt.name, func(t *testing.T) {
			srv, wire, audit := integrationR2ConfigServer(t)
			for _, tc := range []struct {
				name, mode, action, pipeline, key string
				failOpen                          bool
				status                            int
			}{
				{"invalid-closed", "enforce", "block", "sequential", "a", false, 503},
				{"invalid-open", "enforce", "block", "parallel", "a", true, 200},
				{"invalid-monitor", "monitor", "block", "parallel", "a", false, 200},
				{"invalid-warn", "enforce", "warn", "sequential", "a", false, 200},
				{"invalid-log", "enforce", "log", "parallel", "a", false, 200},
				{"unselected", "enforce", "block", "parallel", "b", false, 200},
				{"other-org", "enforce", "block", "parallel", "c", false, 200},
				{"corrected", "enforce", "block", "parallel", "a", false, 403},
				{"corrected-clean", "enforce", "block", "parallel", "a", false, 200},
				{"disabled", "enforce", "block", "sequential", "a", false, 200},
				{"custom-static", "enforce", "block", "parallel", "a", false, 403},
			} {
				t.Run(tc.name, func(t *testing.T) {
					name, checkConfig, body := "keyword-blocklist", rt.bad, rt.body
					if strings.HasPrefix(tc.name, "corrected") {
						checkConfig = map[string]interface{}{"words": []interface{}{"restricted"}}
					}
					if tc.name == "corrected-clean" {
						body = strings.ReplaceAll(body, "restricted", "clean text")
					}
					if tc.name == "custom-static" {
						name = "deployed-custom"
						body = strings.ReplaceAll(body, "restricted", "alice@example.com")
					}
					policy := routeR2Policy(tc.mode, tc.action, name, checkConfig)
					policy.Checks[name].Enabled = tc.name != "disabled"
					org := routeR2Org(policy)
					org.FailOpen, org.PipelineMode = tc.failOpen, tc.pipeline
					integrationR2Push(t, srv, org)
					_, before := wire.snapshot()
					w := routeR2Send(srv, rt.path, body, tc.key)
					paths, bodies := wire.snapshot()
					// Reuse the lane's exact-wire assertion on this request's delta,
					// retaining the cumulative capture for real update/cache tests.
					delta := &routeR2Wire{paths: paths[len(before):], bodies: bodies[len(before):]}
					expected := ""
					if tc.status == http.StatusOK {
						expected = body
					}
					t.Logf("route=%s key=%s mode=%s action=%s enabled=%v active=present scope=key priority=2 stage=pre timeout_ms=1000", rt.path, tc.key, tc.mode, tc.action, policy.Checks[name].Enabled)
					routeR2AssertWire(t, delta, tc.status, w, rt.upstream, expected)
					if strings.HasPrefix(tc.name, "invalid-") {
						outcome := "logged"
						if tc.action == "warn" {
							outcome = "warned"
						}
						if tc.status == 503 {
							outcome = "blocked"
							if !strings.Contains(w.Body.String(), "guardrail_unavailable") {
								t.Fatalf("wrong unavailable envelope: %s", w.Body.String())
							}
						}
						integrationR2AssertUnavailable(t, audit, name, tc.mode, outcome, "guardrail_unavailable", tc.action)
					} else if tc.status == http.StatusForbidden {
						routeR2AssertOutcome(t, audit, name, tc.mode, "blocked")
					} else {
						integrationR2AssertUnattributed(t, audit)
					}
					if tc.status == http.StatusOK && strings.Contains(rt.path, "/audio/speech") {
						routeR2AssertAudio(t, rt.path, w)
					}
				})
			}
		})
	}
}
