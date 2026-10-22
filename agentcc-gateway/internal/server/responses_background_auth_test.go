package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A background /v1/responses request that prepaid credit would pay is
// refused with 400 usage_unavailable, and that refusal runs after
// the key is authenticated (the auth plugin), so a key the auth plugin
// refuses gets that refusal, not 400. A revoked or expired
// key never reaches the handler: the key gate in front of every /v1 route
// (middleware.KeyAuth) answers it 401; those cases are pinned here as well.
// Nothing reaches the provider and nothing is debited in any case.

const (
	respRevokedManagedKey = "sk-resp-managed-revoked-001"
	respExpiredManagedKey = "sk-resp-managed-expired-001"
	respOtherModelKey     = "sk-resp-managed-other-model-001" // may call gpt-4o-mini only
)

// newBackgroundAuthGateway is newResponsesBillingGateway's gateway with
// three more managed keys of the org: one revoked, one expired, and one that
// may call gpt-4o-mini only.
func newBackgroundAuthGateway(t *testing.T) *responsesBillingGateway {
	t.Helper()
	up := startResponsesUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	expired := time.Now().Add(-time.Hour).Format(time.RFC3339)
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "managed", Key: respManagedKey, Owner: "o", KeyType: "managed", CreditBalance: 1,
			Metadata: map[string]string{"org_id": respOrg}},
		{Name: "byok", Key: respByokKey, Owner: "o", KeyType: "byok", Metadata: map[string]string{"org_id": respOrg}},
		{Name: "revoked", Key: respRevokedManagedKey, Owner: "o", KeyType: "managed", CreditBalance: 1,
			Metadata: map[string]string{"org_id": respOrg}},
		{Name: "expired", Key: respExpiredManagedKey, Owner: "o", KeyType: "managed", CreditBalance: 1,
			ExpiresAt: expired, Metadata: map[string]string{"org_id": respOrg}},
		{Name: "other-model", Key: respOtherModelKey, Owner: "o", KeyType: "managed", CreditBalance: 1,
			Models: []string{"gpt-4o-mini"}, Metadata: map[string]string{"org_id": respOrg}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(respOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "org-key", Models: []string{"gpt-4o"}},
	}})
	ks := auth.NewKeyStore(cfg.Auth)
	for _, k := range ks.List() {
		if k.Name == "revoked" && !ks.Revoke(k.ID) {
			t.Fatal("set-up: could not revoke the key")
		}
	}
	mdb := testModelDBPtr()
	probe := &costProbe{}
	engine := pipeline.NewEngine(authplugin.New(ks, true), costplugin.New(true, mdb.Load, tenants, nil),
		creditsplugin.New(true, ks), probe)
	srv := New(cfg, "", registry, engine, ks, nil, nil, nil, mdb, tenants, nil)
	srv.ready.Store(true)
	return &responsesBillingGateway{srv: srv, keyStore: ks, up: up, probe: probe}
}

func (g *responsesBillingGateway) balanceOf(t *testing.T, name string) int64 {
	t.Helper()
	for _, k := range g.keyStore.List() {
		if k.Name == name {
			return k.BalanceMicros()
		}
	}
	t.Fatalf("key %s is not in the store", name)
	return 0
}

func TestResponsesBackground_AKeyTheAuthPluginRefusesGetsItsRefusal(t *testing.T) {
	for _, tc := range []struct {
		name, key, keyName string
		code               int
		says               string
	}{
		// A key without access to the model gets the auth plugin's 403.
		{"no access to the model", respOtherModelKey, "other-model", http.StatusForbidden, "does not have access to model"},
		// The key gate answers these.
		{"revoked", respRevokedManagedKey, "revoked", http.StatusUnauthorized, "invalid_api_key"},
		{"expired", respExpiredManagedKey, "expired", http.StatusUnauthorized, "invalid_api_key"},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", tc.name, stream), func(t *testing.T) {
				g := newBackgroundAuthGateway(t)
				calls, before := g.up.calls.Load(), g.balanceOf(t, tc.keyName)
				code, body := g.sendBody(t, tc.key, fmt.Sprintf(`{"model":"gpt-4o","input":"hi","stream":%t,"background":true}`, stream))
				if code != tc.code || !strings.Contains(body, tc.says) || strings.Contains(body, "usage_unavailable") {
					t.Errorf("a managed key (%s) sending background got %d %s; want %d saying %q", tc.name, code, strings.TrimSpace(body), tc.code, tc.says)
				}
				if n := g.up.calls.Load() - calls; n != 0 {
					t.Errorf("the provider was called %d time(s)", n)
				}
				if debit := before - g.balanceOf(t, tc.keyName); debit != 0 {
					t.Errorf("a refused request debited %d micros", debit)
				}
			})
		}
	}
}

// Controls: an active managed key's background request is refused
// 400 usage_unavailable before the provider, and a BYOK key's is sent.
func TestResponsesBackground_AnActiveManagedKeyIsRefusedAfterAuth(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			g := newBackgroundAuthGateway(t)
			body := fmt.Sprintf(`{"model":"gpt-4o","input":"hi","stream":%t,"background":true}`, stream)
			calls, before := g.up.calls.Load(), g.balance(t)
			if code, resp := g.sendBody(t, respManagedKey, body); code != http.StatusBadRequest || !strings.Contains(resp, "usage_unavailable") {
				t.Errorf("the active managed key got %d %s; want 400 usage_unavailable", code, strings.TrimSpace(resp))
			}
			if n := g.up.calls.Load() - calls; n != 0 {
				t.Errorf("the provider was called %d time(s) for the refused request", n)
			}
			if debit := before - g.balance(t); debit != 0 {
				t.Errorf("the refused request debited %d micros", debit)
			}
			calls = g.up.calls.Load()
			if code, resp := g.sendBody(t, respByokKey, body); code != http.StatusOK || g.up.calls.Load()-calls != 1 {
				t.Errorf("the BYOK key's background request got %d %s; want it sent", code, strings.TrimSpace(resp))
			}
		})
	}
}
