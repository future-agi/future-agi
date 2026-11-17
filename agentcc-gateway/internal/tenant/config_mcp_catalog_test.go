package tenant

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// A server added from the connector
// catalog records catalog_ref (<app>/<provider>@<semver>) and the org's
// url_variables, and its pushed credential secret says where the value goes
// (in: header, query or url_variable; name). The fields are optional and
// additive: the goldens that predate them decode and round-trip unchanged
// (TestGoldenOrgConfigMCPv2, TestParseContractV2), and a server without them
// encodes none of their keys.

func TestGoldenOrgConfigMCPCatalog(t *testing.T) {
	body, err := os.ReadFile(filepath.Join(goldenDir, "org-config-mcp-catalog-v1.json"))
	if err != nil {
		t.Fatalf("read golden: %v", err)
	}
	cfg, err := parseViaContract(body)
	if err != nil {
		t.Fatalf("golden rejected by the contract: %v", err)
	}

	t.Run("round trip keeps every field", func(t *testing.T) {
		var want any
		if err := json.Unmarshal(body, &want); err != nil {
			t.Fatalf("parse golden: %v", err)
		}
		if diffs := jsonDiff("", want, asJSONValue(t, cfg)); len(diffs) > 0 {
			sort.Strings(diffs)
			t.Fatalf("tenant.OrgConfig does not mirror the golden:\n%v", diffs)
		}
	})

	t.Run("fields read back", func(t *testing.T) {
		servers := cfg.MCP.Servers
		secret := func(id string) MCPCredentialSecret {
			s := servers[id]
			if s == nil || s.Credential == nil || s.Credential.Secret == nil {
				t.Fatalf("server %s has no pushed secret", id)
			}
			return *s.Credential.Secret
		}
		checks := map[string][2]any{
			"Gmail.CatalogRef":     {servers["gmail"].CatalogRef, "gmail/futureagi-rest@1.2.0"},
			"Gmail.URLVariables":   {len(servers["gmail"].URLVariables), 0},
			"Snowflake.CatalogRef": {servers["snowflake"].CatalogRef, "snowflake/vendor-mcp@1.0.0"},
			"Snowflake.URLVariables": {servers["snowflake"].URLVariables, map[string]string{
				"accountUrl": "acme-prod.snowflake.example.com",
				"database":   "SALES",
				"schema":     "PUBLIC",
				"serverName": "analyst",
			}},
			"Exa.In":             {secret("exa").In, "header"},
			"Exa.Name":           {secret("exa").Name, "x-api-key"},
			"Exa.Header":         {secret("exa").Header, "x-api-key"},
			"Browserbase.In":     {secret("browserbase").In, "query"},
			"Browserbase.Name":   {secret("browserbase").Name, "browserbaseApiKey"},
			"Browserbase.Header": {secret("browserbase").Header, ""},
			"Hooks.In":           {secret("hooks").In, "url_variable"},
			"Hooks.Name":         {secret("hooks").Name, "token"},
		}
		for name, c := range checks {
			if !reflect.DeepEqual(c[0], c[1]) {
				t.Errorf("%s = %#v, want %#v", name, c[0], c[1])
			}
		}
	})

	t.Run("a server without them encodes none of their keys", func(t *testing.T) {
		legacy := mustParse(t, `{"mcp": {"servers": {"s": {
			"url": "https://s.example.com/mcp",
			"credential": {"mode": "shared", "secret": {"type": "bearer", "header": "Authorization", "value": "test-token-not-a-real-token"}}
		}}}}`)
		raw, err := json.Marshal(legacy.MCP.Servers["s"])
		if err != nil {
			t.Fatalf("encode: %v", err)
		}
		for _, key := range []string{`"catalog_ref"`, `"url_variables"`, `"in"`, `"name"`} {
			if strings.Contains(string(raw), key) {
				t.Errorf("a server that sets none of these fields encodes %s: %s", key, raw)
			}
		}
	})
}
