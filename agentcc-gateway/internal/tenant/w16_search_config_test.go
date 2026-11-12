package tenant

import (
	"encoding/json"
	"reflect"
	"testing"
)

// Reflection keeps this regression compilable on the pre-port assembly. Missing
// product support must fail an assertion, not an undefined-symbol compilation.
func TestW16SearchConfigRoundTrip(t *testing.T) {
	raw := []byte(`{"mcp":{"search":{"enabled":true,"model":"synthetic/embed","calls_per_minute":2}}}`)
	var cfg OrgConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}
	got, err := json.Marshal(&cfg)
	if err != nil {
		t.Fatal(err)
	}
	var view struct {
		MCP struct {
			Search map[string]any `json:"search"`
		} `json:"mcp"`
	}
	if err := json.Unmarshal(got, &view); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"enabled": true, "model": "synthetic/embed", "calls_per_minute": float64(2)}
	if !reflect.DeepEqual(view.MCP.Search, want) {
		t.Fatalf("search config lost: got %v want %v", view.MCP.Search, want)
	}
}

func TestW16SearchConfigFailClosed(t *testing.T) {
	for _, tc := range []struct {
		name    string
		raw     string
		enabled bool
	}{
		{"absent", `{}`, false},
		{"incomplete", `{"mcp":{"search":{"enabled":true}}}`, false},
		{"disabled", `{"mcp":{"search":{"enabled":false,"model":"synthetic/embed","calls_per_minute":2}}}`, false},
		{"zero-limit", `{"mcp":{"search":{"enabled":true,"model":"synthetic/embed","calls_per_minute":0}}}`, false},
		{"excess-limit", `{"mcp":{"search":{"enabled":true,"model":"synthetic/embed","calls_per_minute":601}}}`, false},
		{"blank-model", `{"mcp":{"search":{"enabled":true,"model":" ","calls_per_minute":2}}}`, false},
		{"invalid-topk", `{"mcp":{"search":{"enabled":true,"model":"synthetic/embed","calls_per_minute":2,"top_k":21}}}`, false},
		{"invalid-timeout", `{"mcp":{"search":{"enabled":true,"model":"synthetic/embed","calls_per_minute":2,"timeout_ms":501}}}`, false},
		{"complete", `{"mcp":{"search":{"enabled":true,"model":"synthetic/embed","calls_per_minute":2}}}`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var cfg OrgConfig
			if err := json.Unmarshal([]byte(tc.raw), &cfg); err != nil {
				t.Fatal(err)
			}
			method := reflect.ValueOf(&cfg).MethodByName("MCPSearch")
			if !method.IsValid() {
				t.Fatal("missing fail-closed MCPSearch config accessor")
			}
			result := method.Call(nil)[0]
			if got := result.FieldByName("Enabled").Bool(); got != tc.enabled {
				t.Fatalf("search enabled=%v want %v", got, tc.enabled)
			}
			if tc.enabled && (result.FieldByName("TopK").Int() != 20 || result.FieldByName("TimeoutMS").Int() != 150) {
				t.Fatal("search defaults changed")
			}
		})
	}
}
