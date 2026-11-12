package mcp

import (
	"encoding/json"
	"testing"
)

// The admin's Try a tool answer names the rule that refused the call
// ("rule", from the tool_denied's metadata), so the console's backend can
// say why without reading the gateway's text. A call that ran has none.
func TestAdminTryToolResultNamesTheRefusingRule(t *testing.T) {
	f := newTryToolFixture(t, newOrgConfig)
	wire := func(t *testing.T, res TestToolResult) map[string]any {
		t.Helper()
		raw, err := json.Marshal(res)
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		if err := json.Unmarshal(raw, &m); err != nil {
			t.Fatal(err)
		}
		return m
	}

	refused := wire(t, f.adminRun(t, map[string]string{HeaderMCPAllowTools: "github__other"}))
	if refused["code"] != "tool_denied" || refused["rule"] != ruleHeader {
		t.Fatalf("a refused Try a tool answered %v, want code tool_denied and rule %q", refused, ruleHeader)
	}
	ran := wire(t, f.adminRun(t, nil))
	if _, has := ran["rule"]; has || ran["code"] != nil {
		t.Fatalf("a Try a tool that ran answered %v, want no code and no rule", ran)
	}
}
