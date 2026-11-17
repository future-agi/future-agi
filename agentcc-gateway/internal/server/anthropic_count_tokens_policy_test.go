package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const countPolicyBody = `{"model":"claude-policy-test","system":"Count this too","messages":[{"role":"user","content":"hello"}],"tools":[{"name":"lookup","input_schema":{"type":"object"}}]}`
const countPolicyResponse = `{"input_tokens":17,"synthetic_extension":"preserved"}`

// A real native adapter sends only to this synthetic upstream. The key belongs
// to org-count-policy, whose own provider credentials override the operator's.
func newCountPolicyServer(t *testing.T, routing *tenant.RoutingConfig, attested bool) (*Server, *atomic.Int32, <-chan string) {
	t.Helper()
	calls := new(atomic.Int32)
	bodies := make(chan string, 8)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read upstream request: %v", err)
		}
		bodies <- string(body)
		if r.Method != http.MethodPost || r.URL.Path != "/v1/messages/count_tokens" {
			t.Errorf("unexpected upstream request: %s %s", r.Method, r.URL.Path)
		}
		for name, want := range map[string]string{
			"x-api-key": "synthetic-provider-key", "anthropic-version": "2023-06-01",
			"anthropic-beta": "synthetic-beta", "anthropic-dangerous-direct-browser-access": "true",
		} {
			if got := r.Header.Get(name); got != want {
				t.Errorf("upstream header %s = %q, want %q", name, got, want)
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, countPolicyResponse)
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	off := false
	cfg.Providers["native"] = config.ProviderConfig{
		BaseURL: upstream.URL, APIKey: "operator-key-not-used", APIFormat: "anthropic",
		// Disable discovery and the startup connectivity probe so every
		// observed upstream call comes from a request under test.
		Models: []string{"claude-global-only"}, AutoDiscover: &off, Local: &off,
	}
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "count-policy", Key: "sk-count-policy", Owner: "synthetic-user", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-count-policy"},
	}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	orgs := tenant.NewStore()
	orgs.Set("org-count-policy", &tenant.OrgConfig{
		Routing: routing,
		Providers: map[string]*tenant.ProviderConfig{"native": {
			Enabled: true, APIKey: "synthetic-provider-key", APIFormat: "anthropic",
			Models: []string{"claude-policy-test"}, ZDRAttested: attested, DataRegion: "us",
		}},
	})
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), orgs, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv, calls, bodies
}

func countPolicyRequest(srv *Server, path string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(countPolicyBody))
	req.Header.Set("x-api-key", "sk-count-policy")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	req.Header.Set("anthropic-beta", "synthetic-beta")
	req.Header.Set("anthropic-dangerous-direct-browser-access", "true")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func TestAnthropicCountTokensPolicyRefusal(t *testing.T) {
	for _, tc := range []struct {
		name    string
		routing *tenant.RoutingConfig
		reason  string
	}{
		{"provider_denied", &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{
			Enabled: true, DenyProviders: []string{"native"},
		}}, eligibility.CodeOrgProviderDenied},
		{"provider_not_allowed", &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{
			Enabled: true, AllowedProviders: []string{"other"},
		}}, eligibility.CodeOrgProviderDenied},
		{"zdr_required", &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{ZDR: true}}, eligibility.CodeDataPolicyZDR},
		{"collection_denied", &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{DataCollection: "deny"}}, eligibility.CodeDataPolicyCollection},
		{"region_disallowed", &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{AllowedRegions: []string{"eu"}}}, eligibility.CodeRegionNotAllowed},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, calls, _ := newCountPolicyServer(t, tc.routing, false)
			chat := countPolicyRequest(srv, "/v1/chat/completions")
			expectRefused(t, chat, http.StatusForbidden, codeModelNotPermitted, tc.reason)
			if got := calls.Load(); got != 0 {
				t.Fatalf("refused chat dispatched %d upstream requests", got)
			}

			count := countPolicyRequest(srv, "/v1/messages/count_tokens")
			// The same policy verdict uses this endpoint's native envelope.
			// Gateway-originated Anthropic refusals expose type/message, not
			// OpenAI code/param/metadata; pin the entire response, not a substring.
			message, err := json.Marshal(errorObject(t, chat)["message"])
			if err != nil {
				t.Fatal(err)
			}
			expectDXAnswer(t, count, http.StatusForbidden, `{"type":"error","error":{"type":"permission_error","message":`+string(message)+`}}`)
			if count.Header().Get("Content-Type") != "application/json" {
				t.Errorf("count content type = %q", count.Header().Get("Content-Type"))
			}
			if got := calls.Load(); got != 0 {
				t.Errorf("refused count_tokens dispatched %d upstream requests, want zero", got)
			}
		})
	}
}

func TestAnthropicCountTokensPolicyAllowed(t *testing.T) {
	for _, tc := range []struct {
		name     string
		routing  *tenant.RoutingConfig
		attested bool
	}{
		{name: "no_policy"},
		{name: "allowed_and_attested", attested: true, routing: &tenant.RoutingConfig{
			ProviderLock: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"native"}},
			DataPolicy:   &tenant.DataPolicyConfig{ZDR: true, DataCollection: "deny", AllowedRegions: []string{"us"}},
		}},
		{name: "disabled_lock", routing: &tenant.RoutingConfig{
			ProviderLock: &tenant.ProviderLockConfig{Enabled: false, DenyProviders: []string{"native"}},
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, calls, bodies := newCountPolicyServer(t, tc.routing, tc.attested)
			count := countPolicyRequest(srv, "/v1/messages/count_tokens")
			if count.Code != http.StatusOK || count.Body.String() != countPolicyResponse || count.Header().Get("Content-Type") != "application/json" {
				t.Fatalf("count_tokens = %d %s, want unchanged native response", count.Code, count.Body)
			}
			if got := calls.Load(); got != 1 {
				t.Fatalf("upstream calls = %d, want exactly one", got)
			}
			if body := <-bodies; body != countPolicyBody {
				t.Errorf("upstream body = %s, want unchanged %s", body, countPolicyBody)
			}
		})
	}
}
