package server

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	ipaclplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Assistants, vector stores and Anthropic count_tokens call the provider
// without running the whole pipeline. These tests drive them over HTTP and
// pin that the global and per-org ip_acl lists and per-key allowed_ips apply
// to them as they do to chat: a caller outside the rules gets 403 and the
// provider is never called, and a caller inside them is served.

const accessTestOrg = "org-access-test"

// accessUpstream answers every request with body and counts the requests
// other than the provider's model listing.
func accessUpstream(t *testing.T, body string) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	hits := &atomic.Int64{}
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/models" {
			hits.Add(1)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, body)
	}))
	t.Cleanup(ts.Close)
	return ts, hits
}

// accessRules are the address rules a test server applies.
type accessRules struct {
	global config.IPACLConfig
	org    *tenant.IPACLConfig
	keyIPs []string
}

// accessRuleCases restrict the caller to 10.0.0.0/8 in each place a rule can
// be set.
var accessRuleCases = map[string]accessRules{
	"key allowed_ips":   {keyIPs: []string{"10.0.0.0/8"}},
	"global allow list": {global: config.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}}},
	"org allow list":    {org: &tenant.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}}},
}

// accessServer builds a real Server whose key belongs to accessTestOrg, with
// the ip_acl plugin bound to the same tenant store as the handlers, and
// request auth on.
func accessServer(t *testing.T, provs map[string]config.ProviderConfig, rules accessRules, extra ...pipeline.Plugin) *Server {
	t.Helper()
	return accessServerWith(t, provs, rules, nil, extra...)
}

// accessServerWith is accessServer with edit applied to the config first.
func accessServerWith(t *testing.T, provs map[string]config.ProviderConfig, rules accessRules, edit func(*config.Config), extra ...pipeline.Plugin) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.IPACL = rules.global
	if edit != nil {
		edit(cfg)
	}
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:       "access-test",
		Key:        clientIPTestKey,
		Owner:      "test-org",
		KeyType:    "internal",
		AllowedIPs: rules.keyIPs,
		Metadata:   map[string]string{"org_id": accessTestOrg},
	}}
	for id, p := range provs {
		cfg.Providers[id] = p
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	store := tenant.NewStore()
	if rules.org != nil {
		store.Set(accessTestOrg, &tenant.OrgConfig{IPACL: rules.org})
	}
	keyStore := authpkg.NewKeyStore(cfg.Auth)
	plugins := append([]pipeline.Plugin{ipaclplugin.New(cfg.IPACL, store), authplugin.New(keyStore, true)}, extra...)
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), store, nil)
	srv.ready.Store(true)
	t.Cleanup(func() { srv.Shutdown(context.Background()) })
	return srv
}

// accessRequest is one call to a gateway route.
type accessRequest struct {
	method, path, body, contentType string
}

func (c accessRequest) sendFrom(s *Server, peer string) *httptest.ResponseRecorder {
	var body io.Reader = http.NoBody
	if c.body != "" {
		body = bytes.NewBufferString(c.body)
	}
	r := httptest.NewRequest(c.method, c.path, body)
	r.RemoteAddr = peer + ":40000"
	ct := c.contentType
	if ct == "" {
		ct = "application/json"
	}
	r.Header.Set("Content-Type", ct)
	r.Header.Set("Authorization", "Bearer "+clientIPTestKey)
	w := httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, r)
	return w
}

// checkAccessRules sends each request from outside and then from inside the
// allowed range: the first must be refused without reaching the provider,
// the second served by it.
func checkAccessRules(t *testing.T, s *Server, hits *atomic.Int64, reqs []accessRequest) {
	t.Helper()
	for _, req := range reqs {
		name := req.method + " " + req.path
		before := hits.Load()
		if w := req.sendFrom(s, outsideClient); w.Code != http.StatusForbidden {
			t.Errorf("%s from %s: got %d %s, want 403", name, outsideClient, w.Code, w.Body.String())
		}
		if n := hits.Load() - before; n != 0 {
			t.Errorf("%s from %s: provider called %d times, want 0", name, outsideClient, n)
		}

		before = hits.Load()
		if w := req.sendFrom(s, allowedClient); w.Code != http.StatusOK {
			t.Errorf("%s from %s: got %d %s, want 200", name, allowedClient, w.Code, w.Body.String())
		}
		if hits.Load() == before {
			t.Errorf("%s from %s: provider never called", name, allowedClient)
		}
	}
}

func TestAssistantsAndVectorStoresApplyAccessRules(t *testing.T) {
	reqs := []accessRequest{
		{method: "GET", path: "/v1/assistants"},
		{method: "POST", path: "/v1/assistants", body: `{"model":"gpt-4o","name":"a"}`},
		{method: "GET", path: "/v1/assistants/asst_1"},
		{method: "DELETE", path: "/v1/assistants/asst_1"},
		{method: "POST", path: "/v1/threads", body: `{}`},
		{method: "GET", path: "/v1/threads/thread_1/messages"},
		{method: "GET", path: "/v1/threads/thread_1/runs/run_1"},
		{method: "POST", path: "/v1/threads/thread_1/runs", body: `{"assistant_id":"asst_1"}`},
		{method: "GET", path: "/v1/vector_stores"},
		{method: "GET", path: "/v1/vector_stores/vs_1"},
	}
	for name, rules := range accessRuleCases {
		t.Run(name, func(t *testing.T) {
			upstream, hits := accessUpstream(t, `{"data":[]}`)
			s := accessServer(t, map[string]config.ProviderConfig{
				"openai": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}},
			}, rules)
			checkAccessRules(t, s, hits, reqs)
		})
	}
}

func TestAnthropicCountTokensAppliesAccessRules(t *testing.T) {
	reqs := []accessRequest{{
		method: "POST", path: "/v1/messages/count_tokens",
		body: `{"model":"claude-access-test","messages":[{"role":"user","content":"Hi"}]}`,
	}}
	for name, rules := range accessRuleCases {
		t.Run(name, func(t *testing.T) {
			upstream, hits := accessUpstream(t, `{"input_tokens":1}`)
			s := accessServer(t, map[string]config.ProviderConfig{
				"anthropic": {BaseURL: upstream.URL, APIKey: "test-key", APIFormat: "anthropic", Models: []string{"claude-access-test"}},
			}, rules)
			checkAccessRules(t, s, hits, reqs)
		})
	}
}
