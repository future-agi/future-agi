package config

// The config file loader keeps conditional routing as the docs write it:
// the routing operators ($eq, $regex, ...) and the logical keys ($and, $or,
// $not) are YAML keys and values, not environment variables. Every other
// $VAR and ${VAR} expands exactly as before. A condition whose op is empty
// or not a routing operator is refused at load, so a mangled file never
// routes silently.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// docsConditionalRoutesYAML is the "Conditional routing" example from the
// gateway's routing docs, verbatim.
const docsConditionalRoutesYAML = `routing:
  conditional_routes:
    - name: "enterprise-to-dedicated"
      priority: 10
      condition:
        field: "metadata.tier"
        op: "$eq"
        value: "enterprise"
      action:
        provider: "openai-dedicated"

    - name: "gpt-models-to-openai"
      priority: 50
      condition:
        field: "model"
        op: "$regex"
        value: "^gpt-"
      action:
        provider: "openai"

    - name: "streaming-to-groq"
      priority: 60
      condition:
        field: "stream"
        op: "$eq"
        value: true
      action:
        provider: "groq"

    - name: "premium-non-streaming"
      priority: 20
      condition:
        $and:
          - field: "metadata.tier"
            op: "$eq"
            value: "premium"
          - field: "stream"
            op: "$eq"
            value: false
      action:
        provider: "openai-premium"
`

func checkDocsRoutes(t *testing.T, label string, routes []ConditionalRouteConfig) {
	t.Helper()
	if len(routes) != 4 {
		t.Errorf("%s: got %d conditional routes, want 4", label, len(routes))
		return
	}
	for i, op := range []string{"$eq", "$regex", "$eq"} {
		if routes[i].Condition.Op != op {
			t.Errorf("%s: route %q op = %q, want %q", label, routes[i].Name, routes[i].Condition.Op, op)
		}
	}
	and := routes[3].Condition.And
	if len(and) != 2 {
		t.Errorf("%s: route %q $and has %d leaves, want 2 (condition=%+v)", label, routes[3].Name, len(and), routes[3].Condition)
		return
	}
	for _, leaf := range and {
		if leaf.Op != "$eq" {
			t.Errorf("%s: route %q $and leaf %q op = %q, want $eq", label, routes[3].Name, leaf.Field, leaf.Op)
		}
	}
}

func writeConfigFile(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// The control: the same bytes decoded with no expansion at all.
func TestDocsConditionalRoutesDecodeWithoutExpansion(t *testing.T) {
	var cfg Config
	if err := yaml.Unmarshal([]byte(docsConditionalRoutesYAML), &cfg); err != nil {
		t.Fatalf("plain decode error: %v", err)
	}
	checkDocsRoutes(t, "plain decode", cfg.Routing.ConditionalRoutes)
}

func TestDocsConditionalRoutesSurviveTheFileLoader(t *testing.T) {
	cfg := DefaultConfig()
	if err := loadFromFile(cfg, writeConfigFile(t, docsConditionalRoutesYAML)); err != nil {
		t.Fatalf("loadFromFile error: %v", err)
	}
	checkDocsRoutes(t, "loadFromFile", cfg.Routing.ConditionalRoutes)
}

func TestDocsConditionalRoutesFileLoads(t *testing.T) {
	cfg, err := Load(writeConfigFile(t, docsConditionalRoutesYAML))
	if err != nil {
		t.Fatalf("Load refused the docs' conditional_routes file: %v", err)
	}
	checkDocsRoutes(t, "Load", cfg.Routing.ConditionalRoutes)
}

// Without the $and block the file always parsed; its operators must not be
// emptied either.
func TestDocsConditionalRoutesPlainRoutesKeepTheirOperators(t *testing.T) {
	first := docsConditionalRoutesYAML[:strings.Index(docsConditionalRoutesYAML, "    - name: \"premium-non-streaming\"")]
	cfg := DefaultConfig()
	if err := loadFromFile(cfg, writeConfigFile(t, first)); err != nil {
		t.Fatalf("loadFromFile error: %v", err)
	}
	routes := cfg.Routing.ConditionalRoutes
	if len(routes) != 3 {
		t.Fatalf("got %d routes, want 3", len(routes))
	}
	for i, op := range []string{"$eq", "$regex", "$eq"} {
		if routes[i].Condition.Op != op {
			t.Errorf("route %q op = %q, want %q", routes[i].Name, routes[i].Condition.Op, op)
		}
	}
}

// An environment variable named like an operator does not change a route.
func TestOperatorNamesStayLiteralWhateverTheEnvironment(t *testing.T) {
	for _, name := range []string{"eq", "regex", "and", "or", "not"} {
		t.Setenv(name, "from-the-environment")
	}
	cfg, err := Load(writeConfigFile(t, docsConditionalRoutesYAML))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	checkDocsRoutes(t, "Load with operator-named variables set", cfg.Routing.ConditionalRoutes)
}

// Everything else in a config file expands exactly as before.
func TestEnvironmentReferencesExpandAsBefore(t *testing.T) {
	t.Setenv("CONDROUTE_TEST_KEY", "k-123")
	t.Setenv("CONDROUTE_TEST_HOST", "redis.internal:6379")
	t.Setenv("eq", "braced-eq")
	body := `providers:
  openai:
    api_key: "$CONDROUTE_TEST_KEY"
    base_url: "https://${CONDROUTE_TEST_HOST}/v1"
    api_format: "openai"
    models: ["m-$CONDROUTE_TEST_KEY", "${eq}", "$CONDROUTE_UNSET_VAR"]
`
	cfg := DefaultConfig()
	if err := loadFromFile(cfg, writeConfigFile(t, body)); err != nil {
		t.Fatalf("loadFromFile: %v", err)
	}
	p := cfg.Providers["openai"]
	if p.APIKey != "k-123" {
		t.Errorf("bare $VAR: api_key = %q, want k-123", p.APIKey)
	}
	if p.BaseURL != "https://redis.internal:6379/v1" {
		t.Errorf("braced ${VAR}: base_url = %q", p.BaseURL)
	}
	want := []string{"m-k-123", "braced-eq", ""}
	if len(p.Models) != len(want) {
		t.Fatalf("models = %q, want %q", p.Models, want)
	}
	for i := range want {
		if p.Models[i] != want[i] {
			t.Errorf("models[%d] = %q, want %q (${eq} is a variable: braces mean the environment)", i, p.Models[i], want[i])
		}
	}
}

// A condition whose op is empty or not a routing operator is refused at load,
// naming the route.
func TestConditionWithEmptyOrUnknownOpIsRefusedAtLoad(t *testing.T) {
	for _, tc := range []struct{ name, op string }{
		{"empty op", `""`},
		{"unknown op", `"$like"`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := `routing:
  conditional_routes:
    - name: "tier-route"
      condition:
        $or:
          - field: "metadata.tier"
            op: ` + tc.op + `
            value: "enterprise"
      action:
        provider: "openai"
`
			_, err := Load(writeConfigFile(t, body))
			if err == nil {
				t.Fatalf("Load accepted a condition with op %s", tc.op)
			}
			if !strings.Contains(err.Error(), `"tier-route"`) || !strings.Contains(err.Error(), "$eq") {
				t.Errorf("error %q should name the route and the operators it accepts", err)
			}
		})
	}
}
