package emit

import (
	"fmt"
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// EE's types at 49321478e4 (packages/server/src/workspace/integrations/
// types.ts:24-48): each member of IntegrationAppDefinition and
// IntegrationOAuthSpec in declaration order, whether it is optional, and
// its JSON type. ee-apps.json holds exactly these.
var (
	eeDefinitionShape = []struct {
		key, kind string
		optional  bool
	}{
		{"id", "string", false}, {"name", "string", false}, {"description", "string", false},
		{"category", "string", false}, {"group", "string", false}, {"oauth", "object", false},
		{"mcpUrl", "string", true}, {"envPrefix", "string", false}, {"source", "string", false},
	}
	eeOAuthShape = []struct {
		key, kind string
		optional  bool
	}{
		{"authorizeUrl", "string", false}, {"tokenUrl", "string", false}, {"scopes", "string", false},
		{"pkce", "bool", false}, {"tokenBody", "string", false}, {"clientAuth", "string", false},
		{"extraAuthorizeParams", "object", true},
	}
)

func kindOf(v any) string {
	switch v.(type) {
	case string:
		return "string"
	case bool:
		return "bool"
	case canonjson.Object:
		return "object"
	}
	return fmt.Sprintf("%T", v)
}

// matchShape holds an object to a shape: members in declaration order, none
// missing unless optional, none extra, each of its type.
func matchShape(t *testing.T, what string, o canonjson.Object, shape []struct {
	key, kind string
	optional  bool
}) {
	t.Helper()
	i := 0
	for _, s := range shape {
		if i < len(o) && o[i].Key == s.key {
			if k := kindOf(o[i].Value); k != s.kind {
				t.Errorf("%s.%s is %s, want %s", what, s.key, k, s.kind)
			}
			i++
			continue
		}
		if !s.optional {
			t.Errorf("%s has no %s (members %v)", what, s.key, keys(o))
		}
	}
	if i != len(o) {
		t.Errorf("%s has members outside EE's type, or out of order: %v", what, keys(o))
	}
}

func TestEEAppsShapeMatchesEETypes(t *testing.T) {
	cat, meta := fixture(t)
	doc := parse(t, emitTarget(t, TargetEEApps, cat, meta)["ee-apps.json"])
	if member(doc, "schema") != EEAppsSchema {
		t.Fatalf("schema %v", member(doc, "schema"))
	}
	apps := list(t, doc, "apps")
	var ids []string
	for _, def := range apps {
		id, _ := member(def, "id").(string)
		ids = append(ids, id)
		matchShape(t, "app "+id, def, eeDefinitionShape)
		oauth, _ := member(def, "oauth").(canonjson.Object)
		matchShape(t, "app "+id+" oauth", oauth, eeOAuthShape)
		if member(def, "source") != "built-in" {
			t.Errorf("app %s: source %v", id, member(def, "source"))
		}
		if c, _ := member(def, "category").(string); !slices.Contains(eeCategories, c) {
			t.Errorf("app %s: category %q", id, c)
		}
		if g, _ := member(def, "group").(string); !slices.Contains(eeGroups, g) {
			t.Errorf("app %s: group %q", id, g)
		}
		if b, _ := member(oauth, "tokenBody").(string); b != "form" && b != "json" {
			t.Errorf("app %s: tokenBody %q", id, b)
		}
		if c, _ := member(oauth, "clientAuth").(string); c != "body" && c != "basic" {
			t.Errorf("app %s: clientAuth %q", id, c)
		}
		// The definition is the manifest's projection, unchanged.
		want, ok := manifest.EEAppFor(cat.App(id))
		if !ok {
			t.Fatalf("app %s is emitted but not EE-eligible", id)
		}
		if member(def, "envPrefix") != want.EnvPrefix || member(oauth, "scopes") != want.OAuth.Scopes || member(oauth, "tokenUrl") != want.OAuth.TokenURL {
			t.Errorf("app %s differs from manifest.EEAppFor: %v", id, def)
		}
		if url, _ := member(def, "mcpUrl").(string); url != want.MCPURL {
			t.Errorf("app %s: mcpUrl %q, want %q", id, url, want.MCPURL)
		}
	}
	// Exactly the EE-eligible apps; the rest app gmail has no mcpUrl, and
	// EE's built-in apps keep their ids and env prefixes.
	if want := []string{"github", "gmail", "linear", "slack"}; !slices.Equal(ids, want) {
		t.Fatalf("EE apps %v, want %v", ids, want)
	}
	for _, def := range apps {
		id := member(def, "id").(string)
		if _, has := def.Get("mcpUrl"); has == (id == "gmail") {
			t.Errorf("app %s: mcpUrl present %v", id, has)
		}
		if want := "OMEGA_INTEGRATION_" + strings.ToUpper(id); member(def, "envPrefix") != want {
			t.Errorf("built-in app %s: envPrefix %v, want %s", id, member(def, "envPrefix"), want)
		}
	}
	// The emitter's own guard refuses what EE's boot would refuse.
	bad := vendorApps(1)
	bad.Apps[0].Name = "Trailing space "
	_, err := Target(TargetEEApps, bad, testMeta)
	wantError(t, err, "spaces at an end")
}

func settingsApp(id string) string {
	return `{"id": "` + id + `", "name": "App", "category": "developer", "mcpUrl": "https://mcp.example.com/mcp",
	  "oauth": {"authorizeUrl": "https://a.example.com/authorize", "tokenUrl": "https://a.example.com/token", "scopes": "read", "pkce": true, "tokenBody": "form", "clientAuth": "body"}}`
}

func TestEESettingsAtMost64AndPassesEERules(t *testing.T) {
	cat, meta := fixture(t)
	data := emitTarget(t, TargetEESettings, cat, meta)["ee-settings.json"]
	if err := CheckEESettingsFile(data); err != nil {
		t.Fatalf("the fixture's settings file: %v", err)
	}
	var ids []string
	for _, app := range list(t, parse(t, data), "apps") {
		ids = append(ids, member(app, "id").(string))
		if _, has := app.Get("source"); has {
			t.Errorf("app %v carries source, which EE sets", member(app, "id"))
		}
	}
	// Only OAuth apps through a vendor MCP server with a fixed URL: not the
	// rest app gmail, the api_key apps close and stripe, or snowflake.
	if want := []string{"github", "linear", "slack"}; !slices.Equal(ids, want) {
		t.Fatalf("settings apps %v, want %v", ids, want)
	}

	// 64 apps fit; the 65th is refused by the emitter, never truncated.
	files, err := Target(TargetEESettings, vendorApps(64), testMeta)
	if err != nil {
		t.Fatalf("64 apps: %v", err)
	}
	if n := len(list(t, parse(t, files["ee-settings.json"]), "apps")); n != 64 {
		t.Fatalf("64 apps in, %d out", n)
	}
	_, err = Target(TargetEESettings, vendorApps(65), testMeta)
	wantError(t, err, "65 apps qualify for EE's settings file, which holds at most 64")

	// The emitter reads its own bytes with EE's rules: an MCP URL EE would
	// list as missing is refused.
	local := vendorApps(1)
	local.Apps[0].Providers[0].Transport.URL = "https://localhost/mcp"
	_, err = Target(TargetEESettings, local, testMeta)
	wantError(t, err, "public host")

	// EE's rules, case by case (apps.ts readSettingsApps and settingsApp at
	// 49321478e4).
	if err := CheckEESettingsFile([]byte(`{"apps": [` + settingsApp("one") + `], "other": 1}`)); err != nil {
		t.Errorf("a minimal app, group and envPrefix defaulted, another top-level member: %v", err)
	}
	many := make([]string, 65)
	for i := range many {
		many[i] = settingsApp(fmt.Sprintf("app%02d", i))
	}
	for name, file := range map[string]string{
		"65 apps":                    `{"apps": [` + strings.Join(many, ",") + `]}`,
		"no apps array":              `{"apps": {}}`,
		"an id twice":                `{"apps": [` + settingsApp("one") + `,` + settingsApp("one") + `]}`,
		"an id EE refuses":           `{"apps": [` + settingsApp("One") + `]}`,
		"no mcpUrl":                  `{"apps": [` + strings.Replace(settingsApp("one"), `"mcpUrl": "https://mcp.example.com/mcp",`, ``, 1) + `]}`,
		"an http mcpUrl":             `{"apps": [` + strings.Replace(settingsApp("one"), `"https://mcp.`, `"http://mcp.`, 1) + `]}`,
		"an IP mcpUrl":               `{"apps": [` + strings.Replace(settingsApp("one"), `mcp.example.com`, `10.0.0.1`, 1) + `]}`,
		"a single-label host":        `{"apps": [` + strings.Replace(settingsApp("one"), `mcp.example.com`, `intranet`, 1) + `]}`,
		"a token URL with userinfo":  `{"apps": [` + strings.Replace(settingsApp("one"), `https://a.example.com/token`, `https://u:p@a.example.com/token`, 1) + `]}`,
		"a fragment":                 `{"apps": [` + strings.Replace(settingsApp("one"), `/authorize"`, `/authorize#x"`, 1) + `]}`,
		"a category EE lacks":        `{"apps": [` + strings.Replace(settingsApp("one"), `"developer"`, `"finance"`, 1) + `]}`,
		"a bad env prefix":           `{"apps": [` + strings.Replace(settingsApp("one"), `"name"`, `"envPrefix": "OMEGA_X", "name"`, 1) + `]}`,
		"a name EE trims":            `{"apps": [` + strings.Replace(settingsApp("one"), `"App"`, `" App"`, 1) + `]}`,
		"a name of 202 UTF-16 units": `{"apps": [` + strings.Replace(settingsApp("one"), `"App"`, `"`+strings.Repeat("😀", 101)+`"`, 1) + `]}`,
		"pkce as text":               `{"apps": [` + strings.Replace(settingsApp("one"), `"pkce": true`, `"pkce": "yes"`, 1) + `]}`,
		"a tokenBody EE replaces":    `{"apps": [` + strings.Replace(settingsApp("one"), `"form"`, `"xml"`, 1) + `]}`,
		"a member EE ignores":        `{"apps": [` + strings.Replace(settingsApp("one"), `"name"`, `"logo": "x", "name"`, 1) + `]}`,
		"a non-text authorize param": `{"apps": [` + strings.Replace(settingsApp("one"), `"clientAuth": "body"`, `"clientAuth": "body", "extraAuthorizeParams": {"n": 1}`, 1) + `]}`,
		"source, which EE sets":      `{"apps": [` + strings.Replace(settingsApp("one"), `"name"`, `"source": "settings", "name"`, 1) + `]}`,
	} {
		if err := CheckEESettingsFile([]byte(file)); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}
