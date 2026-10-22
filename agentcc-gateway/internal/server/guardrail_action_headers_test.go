package server

// A guardrail that triggered without blocking names its action and checks
// in response headers, so the console can say which checks warned.

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// tripwireCheck is a request check that always triggers.
type tripwireCheck struct{ name string }

func (c tripwireCheck) Name() string            { return c.name }
func (c tripwireCheck) Stage() guardrails.Stage { return guardrails.StagePre }
func (c tripwireCheck) Check(context.Context, *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: false, Score: 0.99, Message: c.name + " tripped"}
}

// newGuardrailHeadersGateway runs each rule (check name -> action) on every
// request of the BYOK key.
func newGuardrailHeadersGateway(t *testing.T, rules map[string]string) *Server {
	t.Helper()
	up := startHeadersUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "byok", Key: reqHdrByokKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": reqHdrOrg}},
	}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"m-node"}, AutoDiscover: &off}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second}
	reg := map[string]guardrails.Guardrail{}
	for name, action := range rules {
		cfg.Guardrails.Rules = append(cfg.Guardrails.Rules,
			config.GuardrailRuleConfig{Name: name, Stage: "pre", Mode: "sync", Action: action, Threshold: 0.5})
		reg[name] = tripwireCheck{name: name}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(reqHdrOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: "org-key", Enabled: true, Models: []string{"m-chat"}},
	}})
	ks := auth.NewKeyStore(cfg.Auth)
	grEngine := guardrails.NewEngine(cfg.Guardrails, reg)
	policies := policy.NewStore()
	plugins := []pipeline.Plugin{authplugin.New(ks, true), guardrails.NewPlugin(grEngine, reg, nil, policies, tenants)}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), ks, grEngine, policies, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

func TestGuardrailActionHeaders(t *testing.T) {
	cases := []struct {
		name               string
		rules              map[string]string
		wantStatus         int
		wantAction, wantNm string // "" = header absent
	}{
		{"warn and log", map[string]string{"warn-b": "warn", "log-a": "log"}, http.StatusOK, "warn", "log-a,warn-b"},
		{"log only", map[string]string{"log-a": "log"}, http.StatusOK, "log", "log-a"},
		{"block", map[string]string{"block": "block", "warn-b": "warn"}, http.StatusForbidden, "", ""},
		{"nothing triggered", nil, http.StatusOK, "", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newGuardrailHeadersGateway(t, tc.rules)
			w := doRequest(srv, "POST", "/v1/chat/completions", headersChatBody, reqHdrByokKey, nil)
			if w.Code != tc.wantStatus {
				t.Fatalf("status %d, want %d; body %s", w.Code, tc.wantStatus, w.Body.String())
			}
			if tc.wantStatus == http.StatusForbidden && !strings.Contains(w.Body.String(), `"type":"guardrail_error"`) {
				t.Errorf("a block's body %s, want the 403 guardrail_error", w.Body.String())
			}
			if got, ok := w.Header()["X-Agentcc-Guardrail-Action"]; (tc.wantAction == "") == ok || (ok && got[0] != tc.wantAction) {
				t.Errorf("x-agentcc-guardrail-action = %v (present %v), want %q", got, ok, tc.wantAction)
			}
			if got, ok := w.Header()["X-Agentcc-Guardrails-Triggered"]; (tc.wantNm == "") == ok || (ok && got[0] != tc.wantNm) {
				t.Errorf("x-agentcc-guardrails-triggered = %v (present %v), want %q", got, ok, tc.wantNm)
			}
			if tc.wantAction != "" && w.Header().Get("x-agentcc-guardrail-triggered") != "true" {
				t.Errorf("x-agentcc-guardrail-triggered = %q, want true", w.Header().Get("x-agentcc-guardrail-triggered"))
			}
		})
	}
}
