package server

// A provider lock never widens a key's provider restriction: a key whose
// allowed providers leave out one of its org's own providers is refused when
// it locks to that provider, with the provider ACL's own 403, and nothing is
// called; the same key still locks to a provider it may use.

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const plRestrictedKey = "sk-agentcc-plock-org-a-openai-only-0123"

// plRestrictedGateway is plGateway's org A with one key limited to the
// provider "openai".
func plRestrictedGateway(t *testing.T, up *plUpstream) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "plock-a-openai-only", Key: plRestrictedKey, Owner: "t", KeyType: "byok", Providers: []string{"openai"}, Metadata: map[string]string{"org_id": plOrgA}},
	}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-openai-key", APIFormat: "openai", Models: []string{"m-chat"}, AutoDiscover: &off}
	cfg.Providers["other"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-other-key", APIFormat: "openai", Models: []string{"other-model"}, AutoDiscover: &off}
	cfg.OrgProviders.AllowPrivateURLs = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })
	tenants := tenant.NewStore()
	tenants.Set(plOrgA, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai":   {Enabled: true, APIKey: "org-a-openai-key", Models: []string{"m-chat"}},
		"acme-llm": {Enabled: true, APIKey: "org-a-acme-key", APIFormat: "openai", BaseURL: up.URL, Models: []string{"m-chat"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv
}

func TestProviderLockKeepsTheKeysProviderRestriction(t *testing.T) {
	t.Run("org-only provider the key may not use", func(t *testing.T) {
		up := newPLUpstream(t)
		srv := plRestrictedGateway(t, up)
		w := plDo(srv, "/v1/chat/completions", plRestrictedKey, "acme-llm", plChatBody)
		const want = "API key does not have access to provider: acme-llm"
		if w.Code != http.StatusForbidden || r7Code(w) != "permission_denied" || plMessage(w) != want {
			t.Fatalf("lock acme-llm -> %d %s %q, want 403 permission_denied %q", w.Code, r7Code(w), plMessage(w), want)
		}
		if calls := up.snapshot(); len(calls) != 0 {
			t.Fatalf("upstream calls = %q, want none", calls)
		}
	})
	t.Run("provider the key may use", func(t *testing.T) {
		up := newPLUpstream(t)
		srv := plRestrictedGateway(t, up)
		w := plDo(srv, "/v1/chat/completions", plRestrictedKey, "openai", plChatBody)
		if w.Code != http.StatusOK {
			t.Fatalf("lock openai -> %d %s %s, want 200", w.Code, r7Code(w), w.Body.String())
		}
		const want = "/v1/chat/completions Bearer org-a-openai-key"
		if calls := up.snapshot(); len(calls) != 1 || calls[0] != want {
			t.Fatalf("upstream calls = %q, want exactly [%q]", calls, want)
		}
	})
}
