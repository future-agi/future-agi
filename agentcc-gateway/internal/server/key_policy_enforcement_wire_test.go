package server

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// policyWireAudit copies only sanitized guardrail attribution, never request bodies.
type policyWireAudit struct{ results []models.GuardrailResult }

func (*policyWireAudit) Name() string  { return "policy-wire-audit" }
func (*policyWireAudit) Priority() int { return 999 }
func (*policyWireAudit) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *policyWireAudit) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.results = append([]models.GuardrailResult(nil), rc.GuardrailResults...)
	return pipeline.ResultContinue()
}

func TestKeyPolicyEnforcementActualWire(t *testing.T) {
	for _, route := range []guardedRoute{guardedRoutes[0], guardedRoutes[3]} {
		for _, order := range []string{"redact-first", "block-first"} {
			for _, mode := range []string{"enforce", "monitor"} {
				for _, selected := range []bool{true, false} {
					t.Run(route.name+"/"+order+"/"+mode+"/selected="+map[bool]string{true: "yes", false: "no"}[selected], func(t *testing.T) {
						var mu sync.Mutex
						var wire [][]byte
						dialect := &dialectUpstream{reply: "safe reply"}
						upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
							body, _ := io.ReadAll(r.Body)
							if r.Method == http.MethodPost {
								mu.Lock()
								wire = append(wire, body)
								mu.Unlock()
							}
							r.Body = io.NopCloser(strings.NewReader(string(body)))
							dialect.ServeHTTP(w, r)
						}))
						defer upstream.Close()
						cfg := config.DefaultConfig()
						cfg.Auth.Enabled = true
						cfg.Auth.Keys = []config.AuthKeyConfig{
							{Name: "selected", Key: "sk-agentcc-wire-a", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-wire"}},
							{Name: "unselected", Key: "sk-agentcc-wire-b", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-wire"}},
						}
						cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5"}}
						cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
						redact := &tenant.KeyGuardrailPolicy{ID: "redact", Name: "Redact", Mode: mode, Priority: 1, KeyIDs: []string{"key_1"}, Checks: map[string]*tenant.GuardrailCheck{"pii-detection": {Enabled: true, Action: "log", ConfidenceThreshold: 0.8, Config: map[string]interface{}{"remediation": "redact"}}}}
						block := &tenant.KeyGuardrailPolicy{ID: "block", Name: "Block", Mode: mode, Priority: 2, KeyIDs: []string{"key_1"}, Checks: map[string]*tenant.GuardrailCheck{"pii-detection": {Enabled: true, Action: "block", ConfidenceThreshold: 0.8}}}
						if order == "block-first" {
							block.Priority = 0
						}
						store := tenant.NewStore()
						store.Set("org-wire", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{redact, block}}})
						grEngine := guardrails.NewEngine(cfg.Guardrails, nil)
						factory := func(name string, c map[string]interface{}) guardrails.Guardrail {
							if name == "pii-detection" {
								return pii.New(c)
							}
							return nil
						}
						registry, err := providers.NewRegistry(cfg)
						if err != nil {
							t.Fatal(err)
						}
						keys := auth.NewKeyStore(cfg.Auth)
						audit := &policyWireAudit{}
						srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true), guardrails.NewPlugin(grEngine, nil, factory, nil, store), audit), keys, grEngine, nil, nil, testModelDBPtr(), store, nil)
						srv.ready.Store(true)
						send := func(prompt string) *httptest.ResponseRecorder {
							req := route.request(false, prompt)
							key := "sk-agentcc-wire-b"
							if selected {
								key = "sk-agentcc-wire-a"
							}
							req.Header.Set("Authorization", "Bearer "+key)
							w := httptest.NewRecorder()
							srv.httpServer.Handler.ServeHTTP(w, req)
							return w
						}
						blocked := selected && mode == "enforce" && (route.name != "chat" || order == "block-first")
						w := send("contact alice@example.com")
						want := 200
						if blocked {
							want = 403
						}
						if w.Code != want {
							t.Fatalf("status=%d want=%d", w.Code, want)
						}
						mu.Lock()
						calls := len(wire)
						var body []byte
						if calls > 0 {
							body = wire[0]
						}
						mu.Unlock()
						if blocked {
							if calls != 0 {
								t.Fatalf("blocked request reached upstream %d times", calls)
							}
						} else {
							if calls != 1 {
								t.Fatalf("calls=%d want=1", calls)
							}
							wantPII := !selected || mode == "monitor" || route.name != "chat"
							if strings.Contains(string(body), "alice@example.com") != wantPII {
								t.Fatal("wire content did not match actual remediation support")
							}
						}
						for _, result := range audit.results {
							encoded, _ := json.Marshal(result)
							if strings.Contains(string(encoded), "alice@example.com") {
								t.Fatal("attribution leaked raw content")
							}
						}
						if blocked {
							found := false
							for _, r := range audit.results {
								if r.PolicyID == "block" && r.Outcome == "blocked" {
									found = true
								}
							}
							if !found {
								t.Fatalf("missing blocking policy attribution: %+v", audit.results)
							}
						}
						if clean := send("contact the team"); clean.Code != 200 {
							t.Fatalf("clean control status=%d", clean.Code)
						}
					})
				}
			}
		}
	}
}
