package generated_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOrgConfigContractRoundTripsIntoGatewayTenantConfig(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "futureagi", "agentcc", "tests", "fixtures", "gateway_org_config.full.json")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}

	var contract gatewayadmin.OrgConfig
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
	emptyPrefix := ""
	contract.Providers["openai"].APIPathPrefix = &emptyPrefix
	encoded, err = json.Marshal(contract)
	if err != nil {
		t.Fatalf("encode generated contract with path prefix: %v", err)
	}
	if err := json.Unmarshal(encoded, &runtime); err != nil {
		t.Fatalf("decode gateway runtime path prefix: %v", err)
	}
	if got := runtime.Providers["openai"].APIPathPrefix; got == nil || *got != "" {
		t.Fatalf("provider api path prefix mismatch: %#v", got)
	}
	if runtime.Guardrails == nil || len(runtime.Guardrails.KeyPolicies) != 1 {
		t.Fatalf("guardrail key policies missing: %#v", runtime.Guardrails)
	}
	keyPolicy := runtime.Guardrails.KeyPolicies[0]
	if keyPolicy.ID != "7d3f9f0e-1c2b-4a5d-9e8f-0a1b2c3d4e5f" || keyPolicy.Name != "payments-rollout" ||
		keyPolicy.Mode != "monitor" || keyPolicy.Priority != 100 ||
		len(keyPolicy.KeyIDs) != 1 || keyPolicy.KeyIDs[0] != "key-gateway-1" {
		t.Fatalf("guardrail key policy mismatch: %#v", keyPolicy)
	}
	if check := keyPolicy.Checks["secret-detection"]; check == nil || !check.Enabled || check.Action != "block" {
		t.Fatalf("guardrail key policy check mismatch: %#v", keyPolicy.Checks)
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
