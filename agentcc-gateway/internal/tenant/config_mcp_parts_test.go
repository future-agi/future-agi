package tenant

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// AM-C20 (CONNECTORS-PLAN.md): a credential with more than one secret part
// (Datadog's DD-API-KEY and DD-APPLICATION-KEY) pushes its first part as the
// secret's own in, name and value, and each other part in parts. The field is
// optional and additive: the goldens that predate it decode and round-trip
// unchanged, and a secret without parts encodes no parts key. Credential
// templates apply parts (CN7), so the PUT accepts it.

func TestGoldenOrgConfigMCPCredentialParts(t *testing.T) {
	body, err := os.ReadFile(filepath.Join(goldenDir, "org-config-mcp-credential-parts-v1.json"))
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

	t.Run("two secret parts", func(t *testing.T) {
		sec := cfg.MCP.Servers["datadog"].Credential.Secret
		if sec == nil || sec.In != "header" || sec.Name != "DD-API-KEY" || sec.Header != "DD-API-KEY" ||
			sec.Value != "test-dd-api-key-not-a-real-key" {
			t.Fatalf("first part = %+v", sec)
		}
		want := []MCPCredentialSecretPart{{In: "header", Name: "DD-APPLICATION-KEY", Value: "test-dd-application-key-not-a-real-key"}}
		if !reflect.DeepEqual(sec.Parts, want) {
			t.Fatalf("parts = %+v, want %+v", sec.Parts, want)
		}
	})

	t.Run("a secret without parts encodes no parts key", func(t *testing.T) {
		single := mustParse(t, `{"mcp": {"servers": {"s": {
			"url": "https://s.example.com/mcp",
			"credential": {"mode": "shared", "secret": {"type": "api_key", "header": "x-api-key", "value": "test-key-not-a-real-key"}}
		}}}}`)
		raw, err := json.Marshal(single.MCP.Servers["s"].Credential.Secret)
		if err != nil {
			t.Fatalf("encode: %v", err)
		}
		if strings.Contains(string(raw), `"parts"`) {
			t.Fatalf("a single-part secret encodes parts: %s", raw)
		}
	})

	t.Run("accepted: credential templates apply parts (CN7)", func(t *testing.T) {
		// The golden's server names datadog/vendor-mcp@1.0.0, whose two
		// secret header parts the push carries.
		cat := placementCatalog{"datadog/vendor-mcp": {"1.0.0", []credtemplate.Declared{
			{In: credtemplate.InHeader, Name: "DD-API-KEY", Secret: true},
			{In: credtemplate.InHeader, Name: "DD-APPLICATION-KEY", Secret: true},
		}}}
		if perr := cfg.UnappliedMCPCredentialPlacement(cat); perr != nil {
			t.Fatalf("UnappliedMCPCredentialPlacement = %v, want nil", perr)
		}
		if perr := cfg.UnappliedMCPCredentialPlacement(nil); perr == nil || perr.Field != "mcp.servers.datadog.catalog_ref" {
			t.Fatalf("with no catalog loaded: %v, want a refusal naming mcp.servers.datadog.catalog_ref", perr)
		}
	})
}
