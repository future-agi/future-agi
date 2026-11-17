package tenancy

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestW07CatalogFilteringKeepsStaticAuthentication(t *testing.T) {
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(`{"mcp":{"enabled":true,"servers":{"static":{"url":"https://fixture.invalid/mcp","auth":{"type":"bearer","token":"synthetic-not-a-real-token"}},"catalogued":{"url":"https://fixture.invalid/catalog","catalog_ref":"fixture/provider@1.0.0"}}}}`), &cfg); err != nil {
		t.Fatal(err)
	}
	for _, on := range []bool{false, true} {
		specs, errs := orgSpecsForRelease(&cfg, nil, on)
		if len(errs) != 0 {
			t.Fatal(errs)
		}
		if specs["static"].client.Auth.Token != "synthetic-not-a-real-token" {
			t.Fatal("shared static credential read was disabled")
		}
		_, catalogued := specs["catalogued"]
		if catalogued != on {
			t.Fatal("catalogue runtime does not match deployment gate")
		}
	}
}
