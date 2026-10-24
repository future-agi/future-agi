package guardrails_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/orgfactory"
)

// The console offers "mask" for an organization's check exactly when the
// gateway takes it: on a check type that can rewrite the request it reads
// (guardrails.Masker). api_contracts/gateway/guardrail-check-actions.json
// names the console's check types and the ones that take mask; the
// console's test reads it too (checkActions.test.jsx), so neither side can
// drift from it unnoticed.

type checkActionsContract struct {
	Actions []string                     `json:"actions"`
	Mask    []string                     `json:"mask"`
	Checks  map[string]map[string]string `json:"checks"`
}

func readCheckActionsContract(t *testing.T) checkActionsContract {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "guardrail-check-actions.json"))
	if err != nil {
		t.Fatal(err)
	}
	var c checkActionsContract
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	return c
}

// orgCheckType builds the guardrail an organization's check of name runs,
// with cfg as the control plane pushes it (a model check's provider in its
// config), with the gateway's own factory for org checks: orgfactory.New,
// which cmd/agentcc/main.go wires. A nil dialer is the guard with no
// exceptions, and no check here makes a call.
func orgCheckType(name string, cfg map[string]interface{}) guardrails.Guardrail {
	return orgfactory.New(nil)(name, cfg)
}

// Each check type the console offers can mask exactly when the contract
// lists it in mask: pii-detection, and no other.
func TestCheckActionsContract_MaskIsOfferedExactlyWhereTheGatewayTakesIt(t *testing.T) {
	c := readCheckActionsContract(t)
	if !slices.Equal(c.Actions, []string{"block", "warn", "log"}) {
		t.Errorf("actions %v: every check blocks, warns or logs", c.Actions)
	}
	for _, name := range c.Mask {
		if _, ok := c.Checks[name]; !ok {
			t.Errorf("%s takes mask but is not a check the console offers", name)
		}
	}
	for name, spec := range c.Checks {
		cfg := map[string]interface{}{}
		if provider := spec["provider"]; provider != "" {
			cfg["provider"] = provider
		}
		g := orgCheckType(name, cfg)
		if g == nil {
			t.Errorf("%s (config %v): the gateway builds no check for it", name, cfg)
			continue
		}
		_, canMask := g.(interface{ Masked() guardrails.Guardrail })
		if listed := slices.Contains(c.Mask, name); canMask != listed {
			t.Errorf("%s: the gateway can mask it: %v; the contract offers mask: %v", name, canMask, listed)
		}
	}
}
