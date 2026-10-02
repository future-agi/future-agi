package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/mcpsec"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/toolperm"
)

// The backend pushes each provider-backed check with a provider in its config,
// and dynamicFactory builds the check as the guardrail whose recogniser accepts
// that provider. A provider the check's own guardrail does not accept makes the
// gateway build a different guardrail or, when no recogniser accepts it, skip
// the check without an error. This covers the recognisers, not dynamicFactory's
// calls to them.
func TestGuardrailRecognisersAcceptEveryPushedProvider(t *testing.T) {
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

	// The recogniser of the guardrail each check should be built as; every
	// other check is an external provider.
	recognisers := map[string]func(map[string]interface{}) bool{
		"futureagi-eval":   futureagi.IsFutureAGIConfig,
		"tool-permissions": toolperm.IsToolPermConfig,
		"mcp-security":     mcpsec.IsMCPSecConfig,
	}
	for check, provider := range shared.Checks {
		recognises, ok := recognisers[check]
		if !ok {
			recognises = external.IsExternalProviderConfig
		}
		if !recognises(map[string]interface{}{"provider": provider}) {
			t.Errorf("%s: the guardrail this check should be built as does not recognise provider %q", check, provider)
		}
	}
}
