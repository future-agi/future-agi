package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/mcpsec"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/toolperm"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The backend pushes each provider-backed check with a provider in its config,
// and dynamicFactory builds the check from that provider. A provider the
// check's own guardrail does not accept makes the gateway build a different
// guardrail or, when nothing accepts it, skip the check without an error.
func TestDynamicFactoryBuildsEachPushedCheckAsItsOwnGuardrail(t *testing.T) {
	// Shared with the backend's test of the providers it pushes.
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "guardrail-providers.json"))
	if err != nil {
		t.Fatalf("reading the shared guardrail providers: %v", err)
	}
	var shared struct {
		Checks map[string]string `json:"checks"`
	}
	if err := json.Unmarshal(raw, &shared); err != nil {
		t.Fatalf("parsing the shared guardrail providers: %v", err)
	}
	if len(shared.Checks) == 0 {
		t.Fatal("the shared guardrail providers list no checks")
	}

	// The guardrail each check should be built as; every other check is an
	// external provider.
	builtAs := map[string]guardrails.Guardrail{
		"futureagi-eval":   &futureagi.FutureAGIGuardrail{},
		"tool-permissions": &toolperm.ToolPermGuardrail{},
		"mcp-security":     &mcpsec.MCPSecGuardrail{},
	}
	for check, provider := range shared.Checks {
		want, ok := builtAs[check]
		if !ok {
			want = &external.ExternalGuardrail{}
		}
		got := dynamicFactory(check, map[string]interface{}{"provider": provider})
		if reflect.TypeOf(got) != reflect.TypeOf(want) {
			t.Errorf("%s: provider %q builds %T, want %T", check, provider, got, want)
		}
	}
}

// A tool-permissions check as the backend pushes it runs at the gateway: it
// blocks a request that offers a denied tool and lets other tools through.
func TestPushedToolPermissionsCheckBlocksDeniedTools(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{
		Checks: map[string]*tenant.GuardrailCheck{
			"tool-permissions": {
				Enabled:             true,
				Action:              "block",
				ConfidenceThreshold: 0.8,
				Config: map[string]interface{}{
					"provider": "tool_permission",
					"mode":     "denylist",
					"tools":    "file_*",
					"apply_to": "request",
				},
			},
		},
	}})
	plugin := guardrails.NewPlugin(nil, nil, dynamicFactory, nil, store)

	tests := []struct {
		tool        string
		wantBlocked bool
	}{
		{"file_delete", true},
		{"get_weather", false},
	}
	for _, tt := range tests {
		t.Run(tt.tool, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["org_id"] = "org-1"
			rc.Request = &models.ChatCompletionRequest{
				Tools: []models.Tool{{Type: "function", Function: models.ToolFunction{Name: tt.tool}}},
			}

			result := plugin.ProcessRequest(context.Background(), rc)

			if blocked := result.Error != nil; blocked != tt.wantBlocked {
				t.Fatalf("blocked = %v, want %v (result %+v)", blocked, tt.wantBlocked, result)
			}
			if tt.wantBlocked && result.Error.Code != "content_blocked" {
				t.Errorf("error code = %q, want content_blocked", result.Error.Code)
			}
		})
	}
}
