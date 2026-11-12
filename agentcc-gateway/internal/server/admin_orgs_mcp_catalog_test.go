package server

import (
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The admin PUT and the bulk load apply
// the credential rules that need the connector catalog, as the MCP pool does
// (credtemplate.ForCatalogSecret). A catalog_ref server whose shared secret
// the pool would refuse (no catalog loaded, a provider the catalog lacks,
// another major version, a declared secret part missing, an undeclared or
// constant part pushed) is 400 mcp_credential_placement_unsupported naming
// the field, and nothing is stored, so it never freezes the org's MCP config
// at last-known-good.

const catalogPlacementValue = "test-catalog-placement-value-not-real"

func closeCatalogServer(ref, secret string) string {
	return `{"mcp":{"enabled":true,"servers":{"close":{"url":"https://mcp.close.example.com/mcp","catalog_ref":"` + ref + `",` +
		`"credential":{"mode":"shared","credential_id":"cred_c","state":"active","secret":` + secret + `}}}}}`
}

const closeKeyOnly = `{"type":"api_key","in":"header","name":"Close-API-Key","value":"` + catalogPlacementValue + `"}`

func TestAdminOrgConfigRefusesCatalogRefWithNoCatalog(t *testing.T) {
	store := tenant.NewStore()
	h := NewOrgConfigHandlers(store, "adm", nil)
	w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", closeCatalogServer("close/api-key@1.0.0", closeKeyOnly))
	assertPlacementRefused(t, w.Code, w.Body.Bytes(), "mcp.servers.close.catalog_ref", catalogPlacementValue)
	if store.Get("o1") != nil {
		t.Fatal("a refused PUT was stored")
	}

	// A catalog server with no shared secret (per-user, or none) needs no
	// catalog at the PUT, as it needs none in the pool.
	perUser := `{"mcp":{"enabled":true,"servers":{"gmail":{"url":"https://connectors.example.com/gmail/mcp",` +
		`"catalog_ref":"gmail/futureagi-rest@1.2.0","credential":{"mode":"per_user"}}}}}`
	if w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", perUser); w.Code != http.StatusOK {
		t.Fatalf("per-user catalog server: PUT = %d %s", w.Code, w.Body.String())
	}
}

// connectorTestCatalog is a credtemplate.Catalog of providers keyed
// app/provider.
type connectorTestCatalog map[string]struct {
	version string
	parts   []credtemplate.Declared
}

func (c connectorTestCatalog) CredentialParts(app, provider string) (string, []credtemplate.Declared, bool) {
	p, ok := c[app+"/"+provider]
	return p.version, p.parts, ok
}

// closeTestCatalog declares Close's API key as the catalog records it: a secret
// Close-API-Key and the constant Close-Scope, and a two-key vendor.
var closeTestCatalog = connectorTestCatalog{
	"close/api-key": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Close-API-Key", Secret: true},
		{In: credtemplate.InHeader, Name: "Close-Scope", Value: "mcp.read"},
	}},
	"datadog/vendor-mcp": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "DD-API-KEY", Secret: true},
		{In: credtemplate.InHeader, Name: "DD-APPLICATION-KEY", Secret: true},
	}},
	"close/scope-unset": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Close-API-Key", Secret: true},
		{In: credtemplate.InHeader, Name: "Close-Scope"},
	}},
}

func TestAdminOrgConfigChecksCatalogRefAgainstTheCatalog(t *testing.T) {
	v := catalogPlacementValue
	refused := []struct{ name, cfg, field string }{
		{"a provider the catalog does not have", closeCatalogServer("close/oauth-app@1.0.0", closeKeyOnly),
			"mcp.servers.close.catalog_ref"},
		{"another major version", closeCatalogServer("close/api-key@2.0.0", closeKeyOnly),
			"mcp.servers.close.catalog_ref"},
		{"a pushed part the provider does not declare", closeCatalogServer("close/api-key@1.0.0",
			`{"type":"api_key","in":"header","name":"Close-API-Key","value":"`+v+`","parts":[{"in":"header","name":"X-Other","value":"`+v+`"}]}`),
			"mcp.servers.close.credential.secret.parts[0].name"},
		{"the constant part pushed", closeCatalogServer("close/api-key@1.0.0",
			`{"type":"api_key","in":"header","name":"Close-API-Key","value":"`+v+`","parts":[{"in":"header","name":"Close-Scope","value":"`+v+`"}]}`),
			"mcp.servers.close.credential.secret.parts[0].name"},
		{"a declared secret part missing", closeCatalogServer("datadog/vendor-mcp@1.0.0",
			`{"type":"api_key","in":"header","name":"DD-API-KEY","value":"`+v+`"}`),
			"mcp.servers.close.credential.secret.parts"},
		{"a non-secret part with no value, which nothing supplies", closeCatalogServer("close/scope-unset@1.0.0", closeKeyOnly),
			"mcp.servers.close.catalog_ref"},
		{"a disabled server too", `{"mcp":{"enabled":false,"servers":{"close":{"url":"https://mcp.close.example.com/mcp","enabled":false,` +
			`"catalog_ref":"close/api-key@2.0.0","credential":{"mode":"shared","secret":` + closeKeyOnly + `}}}}}`,
			"mcp.servers.close.catalog_ref"},
	}
	for _, tc := range refused {
		t.Run("refused/"+tc.name, func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "adm", nil)
			h.SetConnectorCatalog(closeTestCatalog)
			w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", tc.cfg)
			assertPlacementRefused(t, w.Code, w.Body.Bytes(), tc.field, v)
			if store.Get("o1") != nil {
				t.Fatal("a refused PUT was stored")
			}
		})
	}

	accepted := []struct{ name, cfg string }{
		{"the declared secret part; the constant comes from the catalog", closeCatalogServer("close/api-key@1.0.0", closeKeyOnly)},
		{"a minor version apart", closeCatalogServer("close/api-key@1.4.2", closeKeyOnly)},
		{"both declared secret parts", closeCatalogServer("datadog/vendor-mcp@1.0.0",
			`{"type":"api_key","in":"header","name":"DD-API-KEY","value":"`+v+`","parts":[{"in":"header","name":"DD-APPLICATION-KEY","value":"`+v+`"}]}`)},
		{"a server pushed as before, with no catalog_ref", serverSecretConfig(`{"type":"api_key","header":"x-api-key","value":"` + v + `"}`)},
	}
	for _, tc := range accepted {
		t.Run("accepted/"+tc.name, func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "adm", nil)
			h.SetConnectorCatalog(closeTestCatalog)
			if w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", tc.cfg); w.Code != http.StatusOK {
				t.Fatalf("PUT = %d %s", w.Code, w.Body.String())
			}
			if store.Get("o1") == nil {
				t.Fatal("an accepted PUT was not stored")
			}
		})
	}

	t.Run("bulk", func(t *testing.T) {
		store := tenant.NewStore()
		h := NewOrgConfigHandlers(store, "adm", nil)
		h.SetConnectorCatalog(closeTestCatalog)
		good := closeCatalogServer("close/api-key@1.0.0", closeKeyOnly)
		bad := closeCatalogServer("close/api-key@2.0.0", closeKeyOnly)
		w := orgsCall(h.BulkLoadOrgConfigs, "POST", "/-/orgs/configs/bulk", `{"o2":`+good+`,"o3":`+bad+`}`)
		assertPlacementRefused(t, w.Code, w.Body.Bytes(), "mcp.servers.close.catalog_ref", v)
		if !strings.Contains(w.Body.String(), "org o3") || store.Get("o2") != nil || store.Get("o3") != nil {
			t.Fatalf("a refused bulk load: %s", w.Body.String())
		}
		if w := orgsCall(h.BulkLoadOrgConfigs, "POST", "/-/orgs/configs/bulk", `{"o2":`+good+`}`); w.Code != http.StatusOK || store.Get("o2") == nil {
			t.Fatalf("bulk = %d %s", w.Code, w.Body.String())
		}
	})
}
