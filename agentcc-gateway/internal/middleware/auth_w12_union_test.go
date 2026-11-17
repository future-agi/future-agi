package middleware

import (
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These seam controls use only in-process handlers and synthetic keys. The
// original MCP-KEY and server sign-in suites remain unchanged.
func requireW12GateError(t *testing.T, r *http.Request, got *httptest.ResponseRecorder, want *models.APIError) {
	t.Helper()
	expected := httptest.NewRecorder()
	writeGateError(expected, r, want)
	if got.Code != expected.Code || got.Body.String() != expected.Body.String() || !reflect.DeepEqual(got.Header(), expected.Header()) {
		t.Fatalf("key gate changed the exact status, native envelope or challenge: status=%d want=%d", got.Code, expected.Code)
	}
}

func TestW12UnionPendingConfigKeyNeverHandsOff(t *testing.T) {
	const seed = "synthetic-w12-pending-deployment-key"
	ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: seed, KeyType: "internal"}}}, true)
	if !ks.ConfigKeyPending(seed) {
		t.Fatal("fixture must be a known deployment key awaiting its list")
	}
	for _, signIn := range []bool{false, true} {
		for _, path := range []string{"/mcp", "/mcp/v/one", "/v1/models"} {
			for _, header := range []string{"Authorization", "X-Api-Key"} {
				r := httptest.NewRequest(http.MethodPost, path, nil)
				value := seed
				if header == "Authorization" {
					value = "Bearer " + seed
				}
				r.Header.Set(header, value)
				calls := 0
				h := KeyAuthScoped(ks, true, nil, KeyScopes{MCPRoot: "/mcp", MCPSignIn: signIn}, "/mcp")(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls++ }))
				w := httptest.NewRecorder()
				h.ServeHTTP(w, r)
				requireW12GateError(t, r, w, &models.APIError{Status: http.StatusServiceUnavailable,
					Type: models.ErrTypeServer, Code: "key_list_not_loaded", Message: "Waiting for the first control-plane key list"})
				if calls != 0 {
					t.Fatal("pending deployment key reached MCP sign-in or a handler")
				}
			}
		}
	}
}

func TestW12UnionNoGatewayKeyHandoffIsEndpointOnly(t *testing.T) {
	ks := auth.NewKeyStore(config.AuthConfig{})
	store := tenant.NewStore()
	store.Set("allowed-org", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"198.51.100.0/24"}}})
	store.Set("denied-org", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"203.0.113.0/24"}}})
	acl := ipacl.New(config.IPACLConfig{Enabled: true}, store)
	const discovery = `Bearer resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource/mcp"`
	for _, signIn := range []bool{false, true} {
		for _, route := range []struct {
			path string
			mcp  bool
		}{
			{"/mcp", true}, {"/mcp/v/one", true}, {"/./mcp/v/one", true},
			{"/v1/models", false}, {"/v1/messages", false}, {"/v1beta/models/test", false},
			{"/a2a", false}, {"/mcpx", false},
		} {
			for _, raw := range []string{"", "synthetic-oauth-access-token", "sk-agentcc-synthetic-unknown"} {
				r := httptest.NewRequest(http.MethodPost, route.path, nil)
				r.RemoteAddr = "198.51.100.7:4242"
				if raw != "" {
					r.Header.Set("Authorization", "Bearer "+raw)
				}
				calls := 0
				h := KeyAuthScoped(ks, true, acl, KeyScopes{MCPRoot: "/mcp", MCPSignIn: signIn}, "/mcp", "/a2a", "/mcpx", GenAIRoot)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					check, ok := OrgAddressCheck(r.Context())
					if !ok {
						t.Fatal("MCP handoff lost the organization address check")
					}
					if check("allowed-org") != nil {
						t.Error("MCP handoff rejected the allowed organization/address")
					}
					if err := check("denied-org"); err == nil || err.Status != http.StatusForbidden {
						t.Error("MCP handoff bypassed the organization's address rules")
					}
					if _, ok := KeyIdentity(r.Context()); ok {
						t.Error("MCP handoff invented a gateway key identity")
					}
					w.Header().Set("WWW-Authenticate", discovery)
					w.WriteHeader(http.StatusUnauthorized)
				}))
				w := httptest.NewRecorder()
				h.ServeHTTP(w, r)
				if signIn && route.mcp {
					if calls != 1 || w.Code != http.StatusUnauthorized || w.Header().Get("WWW-Authenticate") != discovery {
						t.Fatal("missing/non-gateway credential did not reach the MCP discovery challenge exactly once")
					}
				} else {
					if calls != 0 {
						t.Fatal("sign-in OFF or a non-MCP route bypassed gateway key auth")
					}
					requireW12GateError(t, r, w, ErrUnauthorizedKeyFor(route.path, raw))
				}
			}
		}
	}
}

func TestW12UnionDelegatedPrincipalNeverBecomesSignInHandoff(t *testing.T) {
	const seed = "synthetic-w12-delegated-pending-key"
	ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: seed, KeyType: "internal"}}}, true)
	for _, signIn := range []bool{false, true} {
		for _, state := range []string{"active", "revoked", "expired"} {
			r := httptest.NewRequest(http.MethodPost, "/mcp/v/one", nil)
			// The trusted delegation, not this known-pending network key, is
			// authoritative. Redeclaring apiKey would incorrectly return 503.
			r.Header.Set("Authorization", "Bearer "+seed)
			exp := time.Now().Add(time.Hour).Unix()
			if state == "expired" {
				exp = time.Now().Add(-time.Hour).Unix()
			}
			r = r.WithContext(auth.WithDelegatedPrincipal(r.Context(), auth.EnvelopeClaims{ID: "union", Kind: "playground", Org: "delegated-org", User: "delegated-person", Workspace: "-", Exp: exp}, "synthetic-grant"))
			if state == "revoked" {
				auth.DelegatedPrincipal(r.Context()).Status = "revoked"
			}
			calls := 0
			h := KeyAuthScoped(ks, true, nil, KeyScopes{MCPRoot: "/mcp", MCPSignIn: signIn}, "/mcp")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				identity, ok := KeyIdentity(r.Context())
				if !ok || identity != (auth.Identity{KeyID: "dlg_union", OrgID: "delegated-org"}) {
					t.Error("earlier delegated principal was replaced or lost")
				}
				if _, handoff := OrgAddressCheck(r.Context()); handoff {
					t.Error("delegated principal was handed to MCP sign-in")
				}
				w.WriteHeader(http.StatusNoContent)
			}))
			w := httptest.NewRecorder()
			h.ServeHTTP(w, r)
			if state == "active" {
				if calls != 1 || w.Code != http.StatusNoContent {
					t.Fatal("active delegated principal did not retain incoming key-gate behavior")
				}
			} else {
				if calls != 0 {
					t.Fatal("inactive delegated principal reached MCP or a handler")
				}
				requireW12GateError(t, r, w, ErrUnauthorizedKeyFor(r.URL.Path, ""))
			}
		}
	}
}

func TestW12UnionIPAndScopeGuardsPrecedeDispatch(t *testing.T) {
	const raw = "synthetic-w12-scoped-gateway-key"
	for _, signIn := range []bool{false, true} {
		for _, guard := range []string{"global-without-key", "organization", "key-address", "scope"} {
			store := tenant.NewStore()
			global := config.IPACLConfig{Enabled: true}
			key := config.AuthKeyConfig{Key: raw, Metadata: map[string]string{"org_id": "key-org"}}
			switch guard {
			case "global-without-key":
				global.Deny = []string{"198.51.100.0/24"}
			case "organization":
				store.Set("key-org", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Deny: []string{"198.51.100.0/24"}}})
			case "key-address":
				key.AllowedIPs = []string{"203.0.113.0/24"}
			case "scope":
				key.Metadata["scopes"] = "models"
			}
			ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{key}})
			acl := ipacl.New(global, store)
			r := httptest.NewRequest(http.MethodPost, "/mcp", nil)
			r.RemoteAddr = "198.51.100.7:4242"
			if guard != "global-without-key" {
				r.Header.Set("Authorization", "Bearer "+raw)
			}
			calls := 0
			h := KeyAuthScoped(ks, true, acl, KeyScopes{MCPRoot: "/mcp", MCPSignIn: signIn}, "/mcp")(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls++ }))
			w := httptest.NewRecorder()
			h.ServeHTTP(w, r)
			if calls != 0 {
				t.Fatal("IP or scope guard allowed MCP handoff/dispatch")
			}
			want := models.ErrForbidden("Access denied: IP address is not allowed")
			if guard == "key-address" {
				want = models.ErrForbidden("API key is not authorized from this IP address")
			} else if guard == "scope" {
				want = keyscope.ErrScopeDenied(keyscope.Parse(key.Metadata), r.Method, r.URL.Path)
			}
			requireW12GateError(t, r, w, want)
		}
	}
}
