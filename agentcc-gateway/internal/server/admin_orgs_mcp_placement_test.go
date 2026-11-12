package server

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The gateway places a pushed MCP
// credential secret with a credential template: in a header, a query
// parameter or a secret URL variable, in one part or several. A PUT or bulk
// load whose secret the gateway cannot place exactly as stated (an in outside
// the contract's enum, which the Go decode does not check; a name that
// contradicts its header; a variable the server's URL does not hold; a
// reserved header; ...), or a managed tool credential placed other than in a
// header, is 400 mcp_credential_placement_unsupported with the field as
// param, and nothing is stored: the credential is never sent elsewhere.

const placementCode = "mcp_credential_placement_unsupported"

func serverSecretConfig(secret string) string {
	return `{"mcp":{"enabled":true,"servers":{"s":{"url":"https://s.example.com/mcp",` +
		`"credential":{"mode":"shared","credential_id":"cred_1","state":"active","secret":` + secret + `}}}}}`
}

func TestAdminOrgConfigRefusesUnappliedCredentialPlacement(t *testing.T) {
	const value = "test-placement-value-not-real"
	refused := []struct{ name, cfg, field string }{
		{"in outside the enum", serverSecretConfig(`{"type":"api_key","in":"cookie","name":"session","value":"` + value + `"}`),
			"mcp.servers.s.credential.secret.in"},
		{"in header whose header is another name", serverSecretConfig(`{"type":"api_key","in":"header","name":"X-Api-Key","header":"X-Other","value":"` + value + `"}`),
			"mcp.servers.s.credential.secret.header"},
		{"a bearer secret is never sent in a named header", serverSecretConfig(`{"type":"bearer","in":"header","name":"X-Api-Key","header":"X-Api-Key","value":"` + value + `"}`),
			"mcp.servers.s.credential.secret.name"},
		{"name without in", serverSecretConfig(`{"type":"api_key","name":"X-Api-Key","header":"X-Other","value":"` + value + `"}`),
			"mcp.servers.s.credential.secret.name"},
		{"a url_variable the server's URL does not hold", serverSecretConfig(`{"type":"api_key","in":"url_variable","name":"token","value":"` + value + `"}`),
			"mcp.servers.s.credential.secret.name"},
		{"a reserved header", serverSecretConfig(`{"type":"api_key","in":"header","name":"Mcp-Session-Id","value":"` + value + `"}`),
			"mcp.servers.s.credential.secret.name"},
		{"a part without a valid name", serverSecretConfig(`{"type":"api_key","in":"header","name":"DD-API-KEY","value":"` + value + `",` +
			`"parts":[{"in":"query","name":"a b","value":"` + value + `"}]}`),
			"mcp.servers.s.credential.secret.parts[0].name"},
		{"a bearer in a query parameter", serverSecretConfig(`{"type":"bearer","in":"query","name":"api_key","value":"` + value + `"}`),
			"mcp.servers.s.credential.secret.type"},
		{"a placeholder nothing fills", `{"mcp":{"enabled":true,"servers":{"s":{"url":"https://s.example.com/mcp/{other}",` +
			`"credential":{"mode":"shared","secret":{"type":"api_key","in":"query","name":"api_key","value":"` + value + `"}}}}}}`,
			"mcp.servers.s.url"},
		{"managed credential", `{"mcp":{"enabled":true,"managed_tools":{"enabled":true,"credentials":{"fagi.web":` +
			`{"mode":"shared","credential_id":"cred_2","secret":{"type":"api_key","in":"query","name":"key","value":"` + value + `"}}}}}}`,
			"mcp.managed_tools.credentials.fagi.web.secret.in"},
		{"a disabled server too", `{"mcp":{"enabled":false,"servers":{"s":{"url":"https://s.example.com/mcp","enabled":false,` +
			`"credential":{"mode":"shared","secret":{"type":"api_key","in":"cookie","name":"api_key","value":"` + value + `"}}}}}}`,
			"mcp.servers.s.credential.secret.in"},
	}
	for _, tc := range refused {
		t.Run("refused/"+tc.name, func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "adm", nil)
			w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", tc.cfg)
			assertPlacementRefused(t, w.Code, w.Body.Bytes(), tc.field, value)
			if store.Get("o1") != nil {
				t.Fatal("a refused PUT was stored")
			}
		})
	}

	accepted := []struct{ name, cfg string }{
		{"no placement (today's push form)", serverSecretConfig(`{"type":"api_key","header":"x-api-key","value":"` + value + `"}`)},
		{"bearer with no header", serverSecretConfig(`{"type":"bearer","value":"` + value + `"}`)},
		{"in header, name equal to header", serverSecretConfig(`{"type":"api_key","header":"x-api-key","in":"header","name":"X-API-Key","value":"` + value + `"}`)},
		{"in header without a name", serverSecretConfig(`{"type":"api_key","header":"x-api-key","in":"header","value":"` + value + `"}`)},
		{"in header with a name and no header", serverSecretConfig(`{"type":"api_key","in":"header","name":"X-Api-Key","value":"` + value + `"}`)},
		{"bearer named Authorization", serverSecretConfig(`{"type":"bearer","in":"header","name":"authorization","value":"` + value + `"}`)},
		{"name equal to the credential's header", `{"mcp":{"enabled":true,"servers":{"s":{"url":"https://s.example.com/mcp",` +
			`"credential":{"mode":"shared","header":"X-Api-Key","secret":{"type":"api_key","in":"header","name":"x-api-key","value":"` + value + `"}}}}}}`},
		{"query", serverSecretConfig(`{"type":"api_key","in":"query","name":"api_key","value":"` + value + `"}`)},
		{"url_variable", `{"mcp":{"enabled":true,"servers":{"s":{"url":"https://s.example.com/mcp/{token}",` +
			`"credential":{"mode":"shared","secret":{"type":"api_key","in":"url_variable","name":"token","value":"` + value + `"}}}}}}`},
		{"parts", serverSecretConfig(`{"type":"api_key","header":"DD-API-KEY","in":"header","name":"DD-API-KEY","value":"` + value + `",` +
			`"parts":[{"in":"header","name":"DD-APPLICATION-KEY","value":"` + value + `"}]}`)},
	}
	for _, tc := range accepted {
		t.Run("accepted/"+tc.name, func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "adm", nil)
			if w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", tc.cfg); w.Code != http.StatusOK {
				t.Fatalf("PUT = %d %s", w.Code, w.Body.String())
			}
			if store.Get("o1") == nil {
				t.Fatal("an accepted PUT was not stored")
			}
		})
	}

	// Both goldens name catalog providers: with a catalog
	// that declares them they are accepted; with none loaded the PUT refuses
	// them naming the first shared server's catalog_ref, as the pool would.
	goldenCatalog := connectorTestCatalog{
		"exa/vendor-mcp":         {"2.4.0", []credtemplate.Declared{{In: credtemplate.InHeader, Name: "x-api-key", Secret: true}}},
		"browserbase/vendor-mcp": {"1.0.3", []credtemplate.Declared{{In: credtemplate.InQuery, Name: "browserbaseApiKey", Secret: true}}},
		"hooks/vendor-mcp":       {"1.1.0", []credtemplate.Declared{{In: credtemplate.InURLVariable, Name: "token", Secret: true}}},
		"datadog/vendor-mcp":     closeTestCatalog["datadog/vendor-mcp"],
	}
	for golden, firstShared := range map[string]string{
		"org-config-mcp-catalog-v1.json":          "mcp.servers.browserbase.catalog_ref",
		"org-config-mcp-credential-parts-v1.json": "mcp.servers.datadog.catalog_ref",
	} {
		body, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples", golden))
		if err != nil {
			t.Fatal(err)
		}
		t.Run("the golden "+golden+" is accepted with a catalog that declares it", func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "adm", nil)
			h.SetConnectorCatalog(goldenCatalog)
			if w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", string(body)); w.Code != http.StatusOK {
				t.Fatalf("PUT = %d %s", w.Code, w.Body.String())
			}
			if store.Get("o1") == nil {
				t.Fatal("an accepted PUT was not stored")
			}
		})
		t.Run("the golden "+golden+" is refused with no catalog loaded", func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "adm", nil)
			w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", string(body))
			assertPlacementRefused(t, w.Code, w.Body.Bytes(), firstShared, "not-a-real")
			if store.Get("o1") != nil {
				t.Fatal("a refused PUT was stored")
			}
		})
	}

	t.Run("bulk", func(t *testing.T) {
		store := tenant.NewStore()
		h := NewOrgConfigHandlers(store, "adm", nil)
		good := serverSecretConfig(`{"type":"api_key","header":"x-api-key","value":"` + value + `"}`)
		if w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", good); w.Code != http.StatusOK {
			t.Fatalf("PUT = %d %s", w.Code, w.Body.String())
		}
		bad := serverSecretConfig(`{"type":"api_key","in":"cookie","name":"api_key","value":"` + value + `"}`)
		w := orgsCall(h.BulkLoadOrgConfigs, "POST", "/-/orgs/configs/bulk", `{"o2":`+good+`,"o3":`+bad+`}`)
		assertPlacementRefused(t, w.Code, w.Body.Bytes(), "mcp.servers.s.credential.secret.in", value)
		if !strings.Contains(w.Body.String(), "org o3") {
			t.Fatalf("bulk refusal does not name the org: %s", w.Body.String())
		}
		if store.Get("o2") != nil || store.Get("o3") != nil || store.Get("o1") == nil {
			t.Fatal("a refused bulk load changed the store")
		}
	})
}

func assertPlacementRefused(t *testing.T, code int, body []byte, field, value string) {
	t.Helper()
	if code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 refusing %s: %s", code, field, body)
	}
	var resp struct {
		Error struct {
			Code    string  `json:"code"`
			Param   *string `json:"param"`
			Message string  `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("decode %s: %v", body, err)
	}
	if resp.Error.Code != placementCode || resp.Error.Param == nil || *resp.Error.Param != field ||
		!strings.Contains(resp.Error.Message, field) {
		t.Fatalf("error = %+v (param %v), want code %s naming %s", resp.Error, resp.Error.Param, placementCode, field)
	}
	if strings.Contains(string(body), value) {
		t.Fatalf("the refusal carries the secret value: %s", body)
	}
}

// A config the control-plane sync stores (it skips the PUT's checks)
// can carry a secret with parts; every admin GET masks each part's value as
// it masks the secret's own, and never changes the live config.
func TestRedactMCPSecretParts(t *testing.T) {
	const first, second = "SECRET-part-one-0123456789", "SECRET-part-two-0123456789"
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(serverSecretConfig(`{"type":"api_key","header":"DD-API-KEY","value":"`+first+`",`+
		`"parts":[{"in":"header","name":"DD-APPLICATION-KEY","value":"`+second+`"}]}`)), &cfg); err != nil {
		t.Fatal(err)
	}
	store := tenant.NewStore()
	store.Set("o1", &cfg)
	h := NewOrgConfigHandlers(store, "adm", nil)
	for _, w := range []string{
		orgsCall(h.GetOrgConfig, "GET", "/-/orgs/o1/config?org_id=o1", "").Body.String(),
		orgsCall(h.ListOrgConfigs, "GET", "/-/orgs/configs", "").Body.String(),
	} {
		if strings.Contains(w, first) || strings.Contains(w, second) {
			t.Fatalf("admin GET carries a secret part: %s", w)
		}
		if !strings.Contains(w, `"name":"DD-APPLICATION-KEY","value":"****"`) {
			t.Fatalf("admin GET lost the masked part: %s", w)
		}
	}
	if sec := cfg.MCP.Servers["s"].Credential.Secret; sec.Value != first || sec.Parts[0].Value != second {
		t.Fatal("redaction changed the live config")
	}
}
