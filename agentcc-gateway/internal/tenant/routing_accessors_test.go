package tenant

import (
	"encoding/json"
	"strings"
	"testing"
)

// TestRoutingFallbackSwitchKeepsUnsetApartFromFalse pins the switch's type:
// unset with chains is on, explicit false is off, and the store's own JSON
// (marshal, then unmarshal, as the Redis copy and GET /-/orgs/{org}/config
// do) keeps each state, including inside a rollout candidate.
func TestRoutingFallbackSwitchKeepsUnsetApartFromFalse(t *testing.T) {
	cases := []struct {
		name, in   string
		on, set    bool
		wantInJSON string // "" = fallback_enabled absent
	}{
		{"unset with chains", `{"model_fallbacks":{"a":["b"]}}`, true, false, ""},
		{"unset without chains", `{"strategy":"weighted"}`, false, false, ""},
		{"explicit false with chains", `{"fallback_enabled":false,"model_fallbacks":{"a":["b"]}}`, false, true, `"fallback_enabled":false`},
		{"explicit true", `{"fallback_enabled":true}`, true, true, `"fallback_enabled":true`},
	}
	for _, tc := range cases {
		var org OrgConfig
		if err := json.Unmarshal([]byte(`{"routing":`+tc.in+`,"meta":{"version":1}}`), &org); err != nil {
			t.Fatalf("%s: %v", tc.name, err)
		}
		for round := 0; round < 2; round++ {
			r := org.Routing
			if r.FallbackOn() != tc.on || r.FallbackSet() != tc.set {
				t.Fatalf("%s (round %d): FallbackOn %v FallbackSet %v, want %v %v", tc.name, round, r.FallbackOn(), r.FallbackSet(), tc.on, tc.set)
			}
			b, err := json.Marshal(&org)
			if err != nil {
				t.Fatal(err)
			}
			if tc.wantInJSON == "" && strings.Contains(string(b), "fallback_enabled") {
				t.Fatalf("%s: marshalled %s, want fallback_enabled absent", tc.name, b)
			}
			if tc.wantInJSON != "" && !strings.Contains(string(b), tc.wantInJSON) {
				t.Fatalf("%s: marshalled %s, want %s", tc.name, b, tc.wantInJSON)
			}
			org = OrgConfig{}
			if err := json.Unmarshal(b, &org); err != nil {
				t.Fatal(err)
			}
		}
	}

	// A rollout candidate's routing block is a RoutingConfig too.
	var org OrgConfig
	if err := json.Unmarshal([]byte(`{"routing":{"rollout":{"version":"2","percent":10,"sticky_by":"key","routing":{"fallback_enabled":false,"model_fallbacks":{"a":["b"]}}}}}`), &org); err != nil {
		t.Fatal(err)
	}
	if c := org.Routing.Rollout.Routing; c.FallbackOn() || !c.FallbackSet() {
		t.Fatalf("candidate routing: FallbackOn %v FallbackSet %v, want off and set", c.FallbackOn(), c.FallbackSet())
	}

	var lit RoutingConfig
	lit.SetFallbackEnabled(false)
	lit.ModelFallbacks = map[string][]string{"a": {"b"}}
	if lit.FallbackOn() {
		t.Fatal("SetFallbackEnabled(false) left the chains on")
	}
	if (*RoutingConfig)(nil).FallbackOn() {
		t.Fatal("nil routing is on")
	}
}
