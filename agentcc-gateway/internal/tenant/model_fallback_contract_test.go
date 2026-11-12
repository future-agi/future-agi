package tenant_test

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestModelFallbackContractBoundsAgree(t *testing.T) {
	data, err := os.ReadFile("../../../api_contracts/gateway/agentcc-admin.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schema struct {
		Defs map[string]json.RawMessage `json:"$defs"`
	}
	if err := json.Unmarshal(data, &schema); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"RoutingConfig", "RolloutRoutingConfig"} {
		// Other schema fields can have a boolean additionalProperties value.
		// Decode only the chain schema instead of assuming all fields are maps.
		var definition struct {
			Properties map[string]json.RawMessage `json:"properties"`
		}
		if err := json.Unmarshal(schema.Defs[name], &definition); err != nil {
			t.Fatal(err)
		}
		var chains struct {
			AdditionalProperties struct {
				MaxItems int `json:"maxItems"`
			} `json:"additionalProperties"`
		}
		if err := json.Unmarshal(definition.Properties["model_fallbacks"], &chains); err != nil {
			t.Fatal(err)
		}
		limit := chains.AdditionalProperties.MaxItems
		if limit != tenant.MaxModelFallbacks {
			t.Errorf("%s.model_fallbacks maxItems = %d, tenant.MaxModelFallbacks = %d", name, limit, tenant.MaxModelFallbacks)
		}
	}
}
