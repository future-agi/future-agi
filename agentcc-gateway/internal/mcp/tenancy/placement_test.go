package tenancy

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A shared server's pushed secret that states a
// placement, or of a server added from the catalog, is built as a credential
// template; a config reaches the pool by any path (the control-plane sync
// stores configs without the admin PUT's checks), so a secret the gateway
// cannot place exactly as stated is refused like any invalid server: it is
// never built, never sent elsewhere or in part, and a last-known-good config
// keeps serving.

const urlTok = "https://t.example.com/mcp/{token}"

func sentWith(up *fakeUpstream) []mcp.AuthConfig {
	up.mu.Lock()
	defer up.mu.Unlock()
	return slices.Clone(up.auths)
}

func TestCredentialPlacementApplied(t *testing.T) {
	const value = "test-placement-value-not-real"
	for name, c := range map[string]struct{ url, secret string }{
		"parts": {urlA, `{"type":"api_key","header":"DD-API-KEY","in":"header","name":"DD-API-KEY","value":"` + value + `",` +
			`"parts":[{"in":"header","name":"DD-APPLICATION-KEY","value":"` + value + `"}]}`},
		"query":                 {urlA, `{"type":"api_key","in":"query","name":"api_key","value":"` + value + `"}`},
		"url_variable":          {urlTok, `{"type":"api_key","in":"url_variable","name":"token","value":"` + value + `"}`},
		"in header, no header":  {urlA, `{"type":"api_key","in":"header","name":"X-Api-Key","value":"` + value + `"}`},
		"in header, its header": {urlA, `{"type":"api_key","in":"header","header":"x-api-key","name":"X-Api-Key","value":"` + value + `"}`},
	} {
		t.Run("built as a template/"+name, func(t *testing.T) {
			r := newRig(t, Options{})
			up := r.factory.upstream(c.url, "a_tool")
			r.push("org-q", 1, `{"enabled":true,"servers":{"s":{"url":"`+c.url+`","credential":{"mode":"shared","secret":`+c.secret+`}}}}`)
			_, pool := r.resolve("org-q")
			eventually(t, "s connected", func() bool { s, _ := pool.Server("s"); return s.Healthy })
			auths := sentWith(up)
			if len(auths) != 1 || auths[0].Template == nil || auths[0].Type != "" || auths[0].Token != "" || auths[0].Key != "" {
				t.Fatalf("built with %d auths, the first a template: %t, type %q", len(auths), len(auths) > 0 && auths[0].Template != nil, auths[0].Type)
			}
		})
	}

	t.Run("a secret pushed as before keeps the header path and its fingerprint", func(t *testing.T) {
		s := tenant.MCPServerSettings{URL: urlA, Era: "auto", CredentialMode: "shared", CredentialState: "active",
			Credential: tenant.MCPServerCredential{Mode: "shared", Secret: &tenant.MCPCredentialSecret{Type: "api_key", Header: "X-Api-Key", Value: value}}}
		sp, err := orgSpec("s", s, nil)
		if err != nil {
			t.Fatal(err)
		}
		if a := sp.client.Auth; a.Template != nil || a.Type != "api_key" || a.Header != "X-Api-Key" {
			t.Fatalf("auth %s %s template %t, want today's api_key header", a.Type, a.Header, a.Template != nil)
		}
		// The fingerprint formula before credential placement, by hand.
		want := fingerprint(map[string]any{
			"url": urlA, "transport": "http", "era": "auto",
			"credential": map[string]any{"mode": "shared", "id": "", "state": "active", "header": "",
				"secret": fingerprint([]string{"api_key", "X-Api-Key", value})},
			"legacy_auth": "", "timeout_ms": 0, "forward_headers": []string(nil), "propagate_depth": false,
		})
		if sp.fp != want {
			t.Fatal("a secret pushed as before changed its fingerprint")
		}
		s.Credential.Secret = &tenant.MCPCredentialSecret{Type: "api_key", In: "query", Name: "api_key", Value: value}
		sq, err := orgSpec("s", s, nil)
		if err != nil || sq.fp == sp.fp {
			t.Fatalf("a move to a query placement must change the fingerprint (err %v)", err)
		}
	})

	refused := map[string]struct{ url, secret, catalogRef, field string }{
		"in outside the enum": {urlA, `{"type":"api_key","in":"cookie","name":"c","value":"` + value + `"}`, "", "credential.secret.in"},
		"name is not the header": {urlA, `{"type":"api_key","in":"header","name":"X-Api-Key","header":"X-Other","value":"` + value + `"}`, "",
			"credential.secret.header"},
		"a variable the URL does not hold": {urlA, `{"type":"api_key","in":"url_variable","name":"token","value":"` + value + `"}`, "",
			"credential.secret.name"},
		"catalog_ref with no catalog loaded": {urlA, `{"type":"api_key","in":"header","name":"Close-API-Key","value":"` + value + `"}`,
			"close/close-mcp@1.0.0", "catalog_ref"},
	}
	for name, c := range refused {
		t.Run("refused/"+name, func(t *testing.T) {
			r := newRig(t, Options{MCP: config.MCPConfig{CatalogEnabled: true}})
			r.factory.upstream(c.url, "a_tool")
			ref := ""
			if c.catalogRef != "" {
				ref = `"catalog_ref":"` + c.catalogRef + `",`
			}
			r.push("org-q", 1, `{"enabled":true,"servers":{"s":{"url":"`+c.url+`",`+ref+`"credential":{"mode":"shared","secret":`+c.secret+`}}}}`)
			_, p, err := r.m.Resolve("org-q")
			if err == nil && p != nil {
				eventually(t, "the server built", func() bool { return r.factory.buildsOf("s") > 0 })
				t.Fatalf("a refused placement was loaded and built")
			}
			if !errors.Is(err, ErrNoLastKnownGood) || !strings.Contains(err.Error(), c.field) {
				t.Fatalf("Resolve: pool %v, err %v; want ErrNoLastKnownGood naming %s", p, err, c.field)
			}
			if strings.Contains(err.Error(), value) {
				t.Fatal("the load error carries the value")
			}
			if n := r.factory.buildsOf("s"); n != 0 {
				t.Fatalf("the refused server was built %d times", n)
			}
		})
	}

	t.Run("a catalog's constant parts", func(t *testing.T) {
		cat := testCatalog{"close/close-mcp": {"1.4.0", []credtemplate.Declared{
			{In: credtemplate.InHeader, Name: "Close-API-Key", Secret: true},
			{In: credtemplate.InHeader, Name: "Close-Scope", Value: "test-scope"},
		}}}
		r := newRig(t, Options{Catalog: cat, MCP: config.MCPConfig{CatalogEnabled: true}})
		up := r.factory.upstream(urlA, "a_tool")
		r.push("org-c", 1, `{"enabled":true,"servers":{"s":{"url":"`+urlA+`","catalog_ref":"close/close-mcp@1.0.0",`+
			`"credential":{"mode":"shared","secret":{"type":"api_key","in":"header","name":"Close-API-Key","value":"`+value+`"}}}}}`)
		_, pool := r.resolve("org-c")
		eventually(t, "s connected", func() bool { s, _ := pool.Server("s"); return s.Healthy })
		auths := sentWith(up)
		if len(auths) != 1 || auths[0].Template == nil || strings.Join(auths[0].Template.HeaderNames(), ",") != "Close-Api-Key,Close-Scope" {
			t.Fatalf("built with %d auths; want one template with the key and the constant scope", len(auths))
		}
	})

	t.Run("last-known-good keeps the header credential", func(t *testing.T) {
		r := newRig(t, Options{})
		up := r.factory.upstream(urlA, "a_tool")
		r.push("org-h", 1, `{"enabled":true,"servers":{"s":{"url":"`+urlA+`","credential":{"mode":"shared",`+
			`"secret":{"type":"api_key","header":"X-Api-Key","value":"`+value+`"}}}}}`)
		snap1, pool := r.resolve("org-h")
		eventually(t, "s connected", func() bool { s, _ := pool.Server("s"); return s.Healthy })

		r.push("org-h", 2, `{"enabled":true,"servers":{"s":{"url":"`+urlA+`","credential":{"mode":"shared",`+
			`"secret":{"type":"api_key","in":"cookie","name":"api_key","value":"`+value+`"}}}}}`)
		snap, p, err := r.m.Resolve("org-h")
		if err != nil || p != pool || snap != snap1 {
			t.Fatalf("v2: snapshot %v, err %v; want the last-known-good v1", snap.Meta, err)
		}
		var shown string
		for _, s := range pool.Servers() {
			if s.ID == "s" {
				shown = s.LastError
			}
		}
		if !strings.HasPrefix(shown, "config_invalid: ") || !strings.Contains(shown, "credential.secret.in") {
			t.Fatalf("status LastError = %q, want config_invalid naming credential.secret.in", shown)
		}
		if got := sentWith(up); r.factory.buildsOf("s") != 1 || len(got) != 1 || got[0].Type != "api_key" || got[0].Header != "X-Api-Key" {
			t.Fatalf("builds %d, want v1's one build with the X-Api-Key header", r.factory.buildsOf("s"))
		}
	})
}

// TestCatalogGoldenThroughThePool: the catalog golden (org-config-mcp-catalog-v1:
// a header, a query and a url_variable placement, each on a server added from
// the catalog, beside two per-user servers) loads in the pool when a catalog
// declares its providers, every shared server built as a template, and the
// catalog_ref majors checked against the golden's own refs (exa is @2.1.0).
// With no catalog loaded the whole org config is refused (no last-known-good
// to fall back to), naming catalog_ref: the blast radius, as a fact.
func TestCatalogGoldenThroughThePool(t *testing.T) {
	raw, err := os.ReadFile("../../../../api_contracts/gateway/examples/org-config-mcp-catalog-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		MCP json.RawMessage `json:"mcp"`
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	shared := map[string]string{
		"exa":         "https://mcp.exa.example.com/mcp",
		"browserbase": "https://mcp.browserbase.example.com/mcp",
		"hooks":       "https://hooks.example.com/mcp/{token}",
	}
	register := func(r *rig) map[string]*fakeUpstream {
		ups := map[string]*fakeUpstream{}
		for id, u := range shared {
			ups[id] = r.factory.upstream(u, id+"_tool")
		}
		r.factory.upstream("https://connectors.example.com/gmail/mcp", "gmail_tool")
		r.factory.upstream("https://acme-prod.snowflake.example.com/api/v2/databases/SALES/schemas/PUBLIC/mcp-servers/analyst", "sf_tool")
		return ups
	}

	t.Run("with the catalog", func(t *testing.T) {
		cat := testCatalog{
			"exa/vendor-mcp":         {"2.4.0", []credtemplate.Declared{{In: credtemplate.InHeader, Name: "x-api-key", Secret: true}}},
			"browserbase/vendor-mcp": {"1.0.3", []credtemplate.Declared{{In: credtemplate.InQuery, Name: "browserbaseApiKey", Secret: true}}},
			"hooks/vendor-mcp":       {"1.1.0", []credtemplate.Declared{{In: credtemplate.InURLVariable, Name: "token", Secret: true}}},
			"gmail/futureagi-rest":   {"1.2.0", nil},
			"snowflake/vendor-mcp":   {"1.0.0", nil},
		}
		r := newRig(t, Options{Catalog: cat, MCP: config.MCPConfig{CatalogEnabled: true}})
		ups := register(r)
		r.push("org-g", 1, string(golden.MCP))
		_, pool := r.resolve("org-g")
		for id, up := range ups {
			eventually(t, id+" connected", func() bool { s, _ := pool.Server(id); return s.Healthy })
			if auths := sentWith(up); len(auths) != 1 || auths[0].Template == nil || auths[0].Type != "" {
				t.Fatalf("%s: built with %d auths, want one credential template", id, len(auths))
			}
		}
	})

	t.Run("another major version is refused", func(t *testing.T) {
		cat := testCatalog{
			"exa/vendor-mcp":         {"3.0.0", []credtemplate.Declared{{In: credtemplate.InHeader, Name: "x-api-key", Secret: true}}},
			"browserbase/vendor-mcp": {"1.0.3", []credtemplate.Declared{{In: credtemplate.InQuery, Name: "browserbaseApiKey", Secret: true}}},
			"hooks/vendor-mcp":       {"1.1.0", []credtemplate.Declared{{In: credtemplate.InURLVariable, Name: "token", Secret: true}}},
		}
		r := newRig(t, Options{Catalog: cat, MCP: config.MCPConfig{CatalogEnabled: true}})
		register(r)
		r.push("org-g", 1, string(golden.MCP))
		if _, _, err := r.m.Resolve("org-g"); !errors.Is(err, ErrNoLastKnownGood) || !strings.Contains(err.Error(), "catalog_ref") {
			t.Fatalf("Resolve: err %v; want ErrNoLastKnownGood naming catalog_ref", err)
		}
	})

	t.Run("with no catalog loaded", func(t *testing.T) {
		r := newRig(t, Options{MCP: config.MCPConfig{CatalogEnabled: true}})
		register(r)
		r.push("org-g", 1, string(golden.MCP))
		_, p, err := r.m.Resolve("org-g")
		if !errors.Is(err, ErrNoLastKnownGood) || !strings.Contains(err.Error(), "catalog_ref") || p != nil {
			t.Fatalf("Resolve: pool %v, err %v; want ErrNoLastKnownGood naming catalog_ref", p, err)
		}
		for id := range shared {
			if n := r.factory.buildsOf(id); n != 0 {
				t.Fatalf("%s was built %d times with no catalog", id, n)
			}
		}
	})
}

// testCatalog is a credtemplate.Catalog of providers keyed app/provider.
type testCatalog map[string]struct {
	version string
	parts   []credtemplate.Declared
}

func (c testCatalog) CredentialParts(app, provider string) (string, []credtemplate.Declared, bool) {
	p, ok := c[app+"/"+provider]
	return p.version, p.parts, ok
}

// TestPlacedCredentialReachesTheUpstream: through the pool's real client
// (mcp.DefaultClientFactory, mcp.upstreamHTTPClient with the template), a
// pushed key in a query parameter reaches the upstream on every request of
// the MCP session, exactly once, and never as a header.
func TestPlacedCredentialReachesTheUpstream(t *testing.T) {
	const secret = "test-query-key/7+=&?"
	fix, err := mcpmock.LoadFixtures("../../../scripts/scenarios/fixtures/mcp/common")
	if err != nil {
		t.Fatal(err)
	}
	era, err := mcpmock.ParseEra("2025-06-18")
	if err != nil {
		t.Fatal(err)
	}
	mock, err := mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "cn7"}, fix)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var seen []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if got := q["browserbaseApiKey"]; len(got) != 1 || got[0] != secret || strings.Contains(r.Header.Get("Authorization"), secret) {
			http.Error(w, "missing or wrong key", http.StatusUnauthorized)
			return
		}
		mu.Lock()
		seen = append(seen, r.Method)
		mu.Unlock()
		q.Del("browserbaseApiKey")
		r.URL.RawQuery = q.Encode()
		mock.Handler().ServeHTTP(w, r)
	}))
	defer srv.Close()

	body, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
		"enabled": true, "servers": map[string]any{"bb": map[string]any{"url": srv.URL + "/mcp", "credential": map[string]any{
			"mode": "shared", "secret": map[string]any{"type": "api_key", "in": "query", "name": "browserbaseApiKey", "value": secret}}}}}})
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(body, &cfg); err != nil {
		t.Fatal(err)
	}
	store := tenant.NewStore()
	store.Set("org-bb", &cfg)
	m := New(Options{Store: store, InitialWait: 10 * time.Second, Deps: func() *mcp.Deps {
		return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManaged{}}
	}})
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = m.Close(ctx)
	}()
	_, pool, err := m.Resolve("org-bb")
	if err != nil {
		t.Fatal(err)
	}
	uc, err := pool.Client("bb", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatalf("the pool has no client: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	res, err := uc.CallTool(ctx, "echo", json.RawMessage(`{"text":"cn7"}`), mcp.CallOptions{})
	if err != nil || !strings.Contains(string(res), "cn7") {
		t.Fatalf("tools/call through the pool: %s, %v", res, err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(seen) < 2 {
		t.Fatalf("the upstream accepted %d requests, want the whole session", len(seen))
	}
}
