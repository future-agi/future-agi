package generated

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOrgConfigContractRoundTripsIntoGatewayTenantConfig(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "futureagi", "agentcc", "tests", "fixtures", "gateway_org_config.full.json")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}

	var contract OrgConfig
	if err := json.Unmarshal(body, &contract); err != nil {
		t.Fatalf("decode generated contract DTO: %v", err)
	}

	encoded, err := json.Marshal(contract)
	if err != nil {
		t.Fatalf("encode generated contract DTO: %v", err)
	}

	var runtime tenant.OrgConfig
	if err := json.Unmarshal(encoded, &runtime); err != nil {
		t.Fatalf("decode gateway runtime tenant config: %v", err)
	}

	openai := runtime.Providers["openai"]
	if openai == nil {
		t.Fatal("expected openai provider")
	}
	if openai.APIKey != "sk-test-openai" {
		t.Fatalf("provider api key mismatch: %q", openai.APIKey)
	}
	if runtime.Routing == nil || runtime.Routing.Strategy != "weighted" {
		t.Fatalf("routing strategy mismatch: %#v", runtime.Routing)
	}
	if runtime.Budgets == nil || !runtime.Budgets.Enabled || !runtime.Budgets.HardLimit {
		t.Fatalf("budget config mismatch: %#v", runtime.Budgets)
	}
	teamBudget := runtime.Budgets.Teams["engineering"]
	if teamBudget == nil || teamBudget.Hard == nil || *teamBudget.Hard {
		t.Fatalf("team budget hard flag mismatch: %#v", teamBudget)
	}
	if runtime.MCP == nil || runtime.MCP.Servers["github"] == nil {
		t.Fatalf("mcp server missing: %#v", runtime.MCP)
	}
}
