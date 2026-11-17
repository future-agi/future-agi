package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	auditpkg "github.com/futureagi/agentcc-gateway/internal/audit"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	auditplugin "github.com/futureagi/agentcc-gateway/internal/plugins/audit"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const syntheticDecisionPipelineKey = "synthetic-decision-pipeline-key"
const syntheticDecisionPipelineBody = `{"model":"typesafe/jev-latest","state":{"synthetic":true},"questions":{"synthetic-question":{"type":"boolean"}}}`

type decisionEvidenceCapture struct {
	cost, source, ip string
}

func (p *decisionEvidenceCapture) Name() string  { return "synthetic_decision_evidence" }
func (p *decisionEvidenceCapture) Priority() int { return 950 }
func (p *decisionEvidenceCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *decisionEvidenceCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.cost, p.source, p.ip = rc.Metadata["cost"], rc.Metadata["cost_source"], rc.Metadata["client_ip"]
	return pipeline.ResultContinue()
}

// Every vendor request terminates at a loopback mock. Unlike an outer KeyAuth
// refusal, this fixture exercises the real ipacl/auth/audit pipeline plugins.
func newDecisionEvidenceServer(t *testing.T, proxies []string, rule, response string) (*Server, *decisionUpstream, *decisionEvidenceCapture, *auditpkg.Logger, *bytes.Buffer) {
	t.Helper()
	upstream := newDecisionUpstream(t)
	upstream.setReply(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, response)
	})
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Server.TrustedProxies = proxies
	key := config.AuthKeyConfig{Name: "synthetic-decision-key", Key: syntheticDecisionPipelineKey, KeyType: "byok", Metadata: map[string]string{"org_id": "synthetic-decision-org"}}
	if rule == "key" {
		key.AllowedIPs = []string{"203.0.113.0/24"}
	}
	cfg.Auth.Keys = []config.AuthKeyConfig{key}
	remote := false
	cfg.Providers = map[string]config.ProviderConfig{
		"typesafe": {APIFormat: "systemone", BaseURL: upstream.URL, Models: []string{"jev-latest"}, Local: &remote},
	}
	globalACL := config.IPACLConfig{}
	if rule == "global" {
		globalACL = config.IPACLConfig{Enabled: true, Allow: []string{"203.0.113.0/24"}}
	}
	tenants := tenant.NewStore()
	org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"typesafe": {Enabled: true, APIKey: "synthetic-decision-provider-credential", APIFormat: "systemone", Models: []string{"jev-latest"}},
	}}
	if rule == "org" {
		org.IPACL = &tenant.IPACLConfig{Enabled: true, Allow: []string{"203.0.113.0/24"}}
	}
	tenants.Set("synthetic-decision-org", org)
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { registry.Close() })
	keys := auth.NewKeyStore(cfg.Auth)
	db := modeldb.New(modeldb.BundledModels, nil)
	capture := &decisionEvidenceCapture{}
	var auditBytes bytes.Buffer
	logger := auditpkg.NewLoggerWithSinks([]auditpkg.Sink{auditpkg.NewWriterSink(&auditBytes)}, nil, auditpkg.SeverityInfo, 64)
	t.Cleanup(logger.Close)
	engine := pipeline.NewEngine(ipacl.New(globalACL, tenants), authplugin.New(keys, true),
		cost.New(true, func() *modeldb.ModelDB { return db }, tenants, nil), auditplugin.New(logger, true), capture)
	srv := New(cfg, "", registry, engine, keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	t.Cleanup(func() { srv.Shutdown(context.Background()) })
	return srv, upstream, capture, logger, &auditBytes
}

func TestDecisionRoutesRealPipelineClientIPAndAudit(t *testing.T) {
	for _, route := range []string{"/v1/systemone", "/v1/decisions"} {
		for _, rule := range []string{"key", "org", "global"} {
			for _, tc := range []struct {
				name, peer, forwarded, wantIP string
				proxies                       []string
				allowed                       bool
			}{
				{"untrusted_forged_allow", "198.51.100.17", "203.0.113.17", "198.51.100.17", []string{"192.0.2.0/24"}, false},
				{"untrusted_forged_deny", "203.0.113.17", "198.51.100.17", "203.0.113.17", nil, true},
				{"trusted_forwarded_allow", "192.0.2.17", "203.0.113.17", "203.0.113.17", []string{"192.0.2.0/24"}, true},
				{"trusted_forwarded_deny", "192.0.2.17", "198.51.100.17", "198.51.100.17", []string{"192.0.2.0/24"}, false},
			} {
				t.Run(route+"/"+rule+"/"+tc.name, func(t *testing.T) {
					srv, upstream, capture, logger, auditBytes := newDecisionEvidenceServer(t, tc.proxies, rule, `{"usage":{"input_tokens":1000,"output_tokens":0}}`)
					resolver, err := middleware.NewClientIPResolver(config.ServerConfig{TrustedProxies: tc.proxies})
					if err != nil {
						t.Fatal(err)
					}
					// Deliberately bypass the outer auth gate: a 403 must originate
					// inside the real handler pipeline, not hide a missing check.
					handler := middleware.TrustedClientIP(resolver)(srv.router)
					req := httptest.NewRequest(http.MethodPost, route, strings.NewReader(syntheticDecisionPipelineBody))
					req.RemoteAddr = tc.peer + ":41234"
					req.Header.Set("Content-Type", "application/json")
					req.Header.Set("Authorization", "Bearer "+syntheticDecisionPipelineKey)
					req.Header.Set("X-Forwarded-For", tc.forwarded)
					req.Header.Set("X-Agentcc-Metadata", `{"client_ip":"203.0.113.99"}`)
					w := httptest.NewRecorder()
					handler.ServeHTTP(w, req)
					wantStatus, wantCalls := http.StatusForbidden, 0
					if tc.allowed {
						wantStatus, wantCalls = http.StatusOK, 1
					}
					if w.Code != wantStatus || upstream.calls() != wantCalls {
						t.Fatalf("status=%d calls=%d, want %d/%d: %s", w.Code, upstream.calls(), wantStatus, wantCalls, w.Body.String())
					}
					if !tc.allowed {
						detail := errorOf(t, w)
						want := "Access denied: IP address is not allowed"
						if rule == "key" {
							want = "API key is not authorized from this IP address"
						}
						if detail.Code != "permission_denied" || detail.Message != want {
							t.Fatalf("wrong pipeline refusal: %+v", detail)
						}
					}
					if capture.ip != tc.wantIP {
						t.Fatalf("pipeline client_ip=%q, want %q", capture.ip, tc.wantIP)
					}
					logger.Close() // Drain before reading the real audit sink.
					decoder := json.NewDecoder(auditBytes)
					completed := false
					for decoder.More() {
						var event auditpkg.Event
						if err := decoder.Decode(&event); err != nil {
							t.Fatal(err)
						}
						if event.Actor.IP != tc.wantIP {
							t.Fatalf("audit client_ip=%q, want %q", event.Actor.IP, tc.wantIP)
						}
						if event.Action == "request_completed" {
							completed = true
							wantOutcome := "error"
							if tc.allowed {
								wantOutcome = "success"
							}
							if event.Outcome != wantOutcome {
								t.Fatalf("audit outcome=%q, want %q", event.Outcome, wantOutcome)
							}
						}
					}
					if !completed {
						t.Fatal("real audit plugin emitted no completion event")
					}
				})
			}
		}
	}
}

func TestDecisionHandlersRetainUnknownUsageThroughCost(t *testing.T) {
	for _, route := range []string{"/v1/systemone", "/v1/decisions"} {
		for _, tc := range []struct{ name, body, wantCost, wantSource string }{
			{"omitted", `{}`, "", "unpriced"},
			{"empty", `{"usage":{}}`, "", "unpriced"},
			{"partial", `{"usage":{"output_tokens":0}}`, "", "unpriced"},
			{"null", `{"usage":{"input_tokens":null,"output_tokens":null}}`, "", "unpriced"},
			{"negative", `{"usage":{"input_tokens":-1,"output_tokens":-2}}`, "", "unpriced"},
			{"measured_zero", `{"usage":{"input_tokens":0,"output_tokens":0}}`, "0.000000", "modeldb"},
			{"valid_aliases", `{"usage":{"input_tokens":null,"prompt_tokens":1000,"output_tokens":-1,"completion_tokens":0}}`, "0.000042", "modeldb"},
		} {
			t.Run(route+"/"+tc.name, func(t *testing.T) {
				srv, upstream, capture, _, _ := newDecisionEvidenceServer(t, nil, "", tc.body)
				req := httptest.NewRequest(http.MethodPost, route, strings.NewReader(syntheticDecisionPipelineBody))
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("Authorization", "Bearer "+syntheticDecisionPipelineKey)
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				if w.Code != http.StatusOK || upstream.calls() != 1 || w.Body.String() != tc.body {
					t.Fatalf("status=%d calls=%d body=%s", w.Code, upstream.calls(), w.Body.String())
				}
				if capture.cost != tc.wantCost || capture.source != tc.wantSource {
					t.Fatalf("handler cost=%q source=%q, want %q/%q", capture.cost, capture.source, tc.wantCost, tc.wantSource)
				}
			})
		}
	}
}
