package vendors_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

var pilots = []struct{ platform, app string }{
	{"google", "gmail"}, {"google", "google-calendar"},
	{"microsoft", "microsoft-outlook"}, {"microsoft", "microsoft-teams"},
	{"arcade", "customer-gateway"},
}

func TestW4bPlatformContracts(t *testing.T) {
	for _, p := range pilots {
		t.Run(p.platform+"/"+p.app+"/connected", func(t *testing.T) {
			f := newFixture(t, p.platform, p.app, nil)
			tok := f.give(t, "org-a", "u1")
			connected(t, f.call(t, userCaller("org-a", "u1")))
			equal(t, f.fingerprints(), []string{bearerHash(tok)})
			equal(t, f.cp.fetched(), []credentials.Key{key("org-a", "u1")})
		})
	}
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		for _, bad := range []string{"unknown-platform", "wrong-platform", "wrong-org", "wrong-server", "wrong-url", "wrong-app", "wrong-provider", "missing-per-user", "missing-fixed", "missing-binding", "stale-catalog"} {
			t.Run(p.platform+"/refuse/"+bad, func(t *testing.T) {
				f := newFixture(t, p.platform, p.app, func(f *fixture) {
					b := f.bindings["org-a/service"]
					switch bad {
					case "wrong-platform":
						b.Platform = "other"
					case "wrong-org":
						b.OrgID = "org-b"
					case "wrong-server":
						b.ServerID = "other"
					case "wrong-url":
						b.URL += "/other"
					case "wrong-app":
						b.CatalogRef = "unapproved/futureagi-rest@1.0.0"
						f.configs["org-a"].MCP.Servers["service"].CatalogRef = b.CatalogRef
					case "wrong-provider":
						b.CatalogRef = p.app + "/other@1.0.0"
						f.configs["org-a"].MCP.Servers["service"].CatalogRef = b.CatalogRef
					case "missing-per-user":
						f.configs["org-a"].MCP.Servers["service"].Credential.Mode = "none"
					case "missing-fixed":
						b.ToolMode = ""
					case "stale-catalog":
						f.configs["org-a"].MCP.Servers["service"].CatalogRef = "stale/provider@0.0.1"
					}
					f.bindings["org-a/service"] = b
					if bad == "missing-binding" {
						delete(f.bindings, "org-a/service")
					}
				})
				if bad == "unknown-platform" {
					f.bridge.platform = "unknown"
				}
				f.give(t, "org-a", "u1")
				var adapterRefused bool
				f.bridge.inspect = func(_ *mcp.CallContext, a *mcp.AuthOverride, d *mcp.Denial) { adapterRefused = d != nil && a == nil }
				denied(t, f.call(t, userCaller("org-a", "u1")), mcp.CodePolicyUnavailable)
				if !adapterRefused {
					t.Fatal("platform refusal must come from selected adapter before token fetch or dispatch")
				}
				equal(t, len(f.cp.fetched()), 0)
				equal(t, len(f.fingerprints()), 0)
			})
		}
	}
	t.Run("microsoft/delegated-only", func(t *testing.T) {
		f := newFixture(t, "microsoft", "microsoft-outlook", func(f *fixture) {
			b := f.bindings["org-a/service"]
			b.Delegated = false
			f.bindings["org-a/service"] = b
		})
		denied(t, f.call(t, userCaller("org-a", "u1")), mcp.CodePolicyUnavailable)
		equal(t, len(f.cp.fetched()), 0)
	})
}

func TestW4bArcadeOAuthContract(t *testing.T) {
	for _, capability := range []string{"cimd", "dcr"} {
		t.Run(capability, func(t *testing.T) {
			f := newFixture(t, "arcade", "customer-gateway", nil)
			// Reuse the OAuth mock's metadata and authorization-code/PKCE engine.
			// Client ids stand in for registration outcomes; the adapter does not
			// register clients or invent an Arcade-specific REST token channel.
			resp, err := http.Get(f.upstream + "/.well-known/oauth-authorization-server")
			if err != nil {
				t.Fatal(err)
			}
			var as map[string]any
			err = json.NewDecoder(resp.Body).Decode(&as)
			resp.Body.Close()
			if err != nil {
				t.Fatal(err)
			}
			equal(t, as["issuer"], f.upstream)
			equal(t, as["token_endpoint"], f.upstream+"/token")
			equal(t, as["code_challenge_methods_supported"], []any{"S256"})
			clientID := arcadeDCRClient
			if capability == "cimd" {
				clientID = f.upstream + arcadeCIMDPath
			}
			tok := f.oauthGrant(t, clientID)
			connected(t, f.call(t, userCaller("org-a", "u1")))
			equal(t, f.fingerprints(), []string{bearerHash(tok)})
			equal(t, f.cp.fetched(), []credentials.Key{key("org-a", "u1")})
		})
	}
	t.Run("api-key-refused", func(t *testing.T) {
		f := newFixture(t, "arcade", "customer-gateway", func(f *fixture) {
			b := f.bindings["org-a/service"]
			b.AuthKind = "api_key"
			f.bindings["org-a/service"] = b
		})
		denied(t, f.call(t, userCaller("org-a", "u1")), mcp.CodePolicyUnavailable)
		equal(t, len(f.cp.fetched()), 0)
	})
}

func TestW4bFixedToolsRequired(t *testing.T) {
	for _, mode := range []string{"recommendation", "unknown", "", "fixed"} {
		name := mode
		if name == "" {
			name = "missing"
		}
		t.Run(name, func(t *testing.T) {
			f := newFixture(t, "arcade", "customer-gateway", func(f *fixture) { b := f.bindings["org-a/service"]; b.ToolMode = mode; f.bindings["org-a/service"] = b })
			f.give(t, "org-a", "u1")
			a := f.call(t, userCaller("org-a", "u1"))
			if mode == "fixed" {
				connected(t, a)
			} else {
				denied(t, a, mcp.CodePolicyUnavailable)
				equal(t, len(f.cp.fetched()), 0)
				equal(t, len(f.fingerprints()), 0)
			}
		})
	}
}

func TestW4bInertByDefault(t *testing.T) {
	f := newFixture(t, "google", "gmail", nil)
	s := mcp.NewServer(mcp.ServerConfig{})
	defer s.Close()
	before := *s.Deps()
	_ = selected(t, f)
	after := *s.Deps()
	equal(t, reflect.TypeOf(after.VendorCredentials), reflect.TypeOf(before.VendorCredentials))
	equal(t, reflect.TypeOf(after.CredentialResolver), reflect.TypeOf(before.CredentialResolver))
	a, d := after.VendorCredentials.Resolve(&mcp.CallContext{}, "google")
	if a != nil || d != nil {
		t.Fatal("default vendor seam replaced")
	}
	// Remove ONLY the test bridge; the same connected fixture must remain off.
	f.srv.SetDeps(mcp.Deps{BearerVerifier: callers{}})
	f.give(t, "org-a", "u1")
	denied(t, f.call(t, userCaller("org-a", "u1")), mcp.CodePolicyUnavailable)
	equal(t, len(f.cp.fetched()), 0)
	equal(t, len(f.fingerprints()), 0)
	r := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	f.srv.HandlePost(w, r)
	equal(t, w.Code, http.StatusUnauthorized)
}
