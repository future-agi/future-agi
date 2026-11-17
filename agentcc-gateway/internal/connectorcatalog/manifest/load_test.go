package manifest

import (
	"slices"
	"testing"
	"testing/fstest"
)

func TestLoadsValidFixture(t *testing.T) {
	cat := mustLoad(t, fixture(t))
	var apps []string
	for _, a := range cat.Apps {
		apps = append(apps, a.ID)
	}
	if want := []string{"close", "github", "gmail", "linear", "slack", "snowflake", "stripe"}; !slices.Equal(apps, want) {
		t.Fatalf("apps = %v, want %v", apps, want)
	}
	if len(cat.Platforms) != 1 || cat.Platforms[0].ID != "relay" {
		t.Fatalf("platforms = %v, want [relay]", cat.Platforms)
	}
	gmail := cat.App("gmail")
	rest := gmail.Provider("futureagi-rest")
	if rest == nil || len(rest.GeneratedTools) != 2 || rest.GeneratedTools[0].Name != "search_threads" {
		t.Fatalf("gmail generated tools not loaded: %+v", rest)
	}
	if got := len(rest.AllTools()); got != 2 {
		t.Errorf("AllTools = %d, want 2", got)
	}
	close := cat.App("close").Provider("close-mcp")
	if parts := close.Auth.CredentialParts; len(parts) != 2 || !parts[0].Secret || parts[1].Secret {
		t.Errorf("close credential parts = %+v", parts)
	}
	snow := cat.App("snowflake").Provider("cortex-mcp")
	if len(snow.Transport.URLVariables) != 4 {
		t.Errorf("snowflake URL variables = %v, want 4", snow.Transport.URLVariables)
	}
	if !cat.App("slack").Provider("slack-mcp").Auth.ClientApproval.Pending() {
		t.Errorf("slack client approval should be pending")
	}
}

func TestUnknownKeyRefused(t *testing.T) {
	cases := []struct {
		name, path, old, new, want string
	}{
		{"app top level", "apps/linear/app.yaml", "eeCategory: developer\n", "eeCategory: developer\nclientId: abc\n",
			"apps/linear/app.yaml: /clientId: is not a connector-manifest/v1 field"},
		{"nested oauth2", "apps/linear/providers/linear-mcp.yaml", "    pkce: true\n", "    pkce: true\n    clientSecretEnv: X\n",
			"apps/linear/providers/linear-mcp.yaml: /auth/oauth2/clientSecretEnv: is not a connector-manifest/v1 field"},
		{"tool item", "apps/linear/providers/linear-mcp.yaml", "      posture: auto\n", "      posture: auto\n      price: 1\n",
			"/tools/items/0/price: is not a connector-manifest/v1 field"},
		{"platform", "platforms/relay.yaml", "selfHostable: false\n", "selfHostable: false\nsecret: x\n",
			"platforms/relay.yaml: /secret: is not a connector-manifest/v1 field"},
		{"app x-omega", "apps/gmail/app.yaml", "x-omega: { eeId: gmail,", "x-omega: { clientId: x, eeId: gmail,",
			"/x-omega/clientId: is not a connector-manifest/v1 field"},
		{"app x-gateway defines no keys", "apps/linear/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-gateway: {pricing: managed}\n",
			"/x-gateway/pricing: is not a connector-manifest/v1 field"},
		{"case matters", "apps/linear/app.yaml", "eeCategory: developer\n", "eeCategory: developer\nEECategory: developer\n",
			"/EECategory: is not a connector-manifest/v1 field"},
		{"generated tools", "apps/gmail/providers/futureagi-rest/tools.generated.json", `"posture": "auto",`, `"posture": "auto", "cost": 1,`,
			"tools.generated.json: /tools/0/cost: is not a connector-manifest/v1 field"},
		{"url variable", "apps/gmail/providers/relay.yaml", "      scope: org\n", "      scope: org\n      secret: true\n",
			"/transport/urlVariables/serverId/secret: is not a connector-manifest/v1 field"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantLoadError(t, fsys, tc.want)
		})
	}
}

func TestNullRefused(t *testing.T) {
	cases := []struct {
		name, path, old, new, want string
	}{
		{"required key", "apps/linear/app.yaml", "\nname: Linear\n", "\nname: ~\n", "apps/linear/app.yaml: /name: must not be null"},
		{"empty value", "apps/linear/app.yaml", "docsUrl: https://linear.app/docs/mcp\n", "docsUrl:\n", "/docsUrl: must not be null"},
		{"optional key", "apps/linear/app.yaml", "archetype: issue-tracker\n", "archetype: issue-tracker\nicon: null\n", "/icon: must not be null"},
		{"list element", "apps/linear/app.yaml", "categories: [developer, issue-tracking]", "categories: [developer, null]", "/categories/1: must not be null"},
		{"nested", "apps/linear/providers/linear-mcp.yaml", "refresh: { supported: true, rotates: false }", "refresh: { supported: true, rotates: null }",
			"/auth/oauth2/refresh/rotates: must not be null"},
		{"sketch null means omit", "apps/linear/providers/linear-mcp.yaml", "pricing: { model: free }", "pricing: { model: free, notes: null }",
			"/pricing/notes: must not be null"},
		{"json null", "apps/gmail/providers/futureagi-rest/tools.generated.json", `"title": "Send email",`, `"title": null,`,
			"/tools/1/title: must not be null"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantLoadError(t, fsys, tc.want)
		})
	}
	t.Run("free JSON Schema documents may hold null", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/gmail/providers/futureagi-rest/tools.generated.json",
			"\"threadId\": {\n            \"type\": \"string\",\n            \"description\": \"Reply in this thread.\"\n          }",
			`"threadId": {"type": ["string", "null"], "default": null}`)
		mustLoad(t, fsys)
	})
}

func TestStrictYAML(t *testing.T) {
	cases := []struct {
		name, old, new, want string
	}{
		{"duplicate key", "\nname: Linear\n", "\nname: Linear\nname: Linear 2\n", "/name: duplicate key"},
		{"anchor", "tags: [issues]", "tags: &t [issues]", "anchors and aliases are not allowed"},
		{"explicit tag", "\nname: Linear\n", "\nname: !!str Linear\n", "explicit tags are not allowed"},
		{"second document", "lifecycle: { status: active }\n", "lifecycle: {status: active}\n---\nid: other\n", "more than one YAML document"},
		{"wrong type", "\nname: Linear\n", "\nname: [Linear]\n", "/name: must be a string"},
		{"a YAML 1.1 boolean as text", "\nname: Linear\n", "\nname: yes\n", "/name: write \"yes\" in quotes"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, "apps/linear/app.yaml", tc.old, tc.new)
			wantLoadError(t, fsys, "apps/linear/app.yaml", tc.want)
		})
	}
	t.Run("non-decimal integer", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "    requests: 1500\n", "    requests: 0x5DC\n")
		wantLoadError(t, fsys, "/rateLimits/0/requests: integers are written in decimal")
	})
	t.Run("unquoted date is text", func(t *testing.T) {
		cat := mustLoad(t, fixture(t))
		if got := cat.App("linear").Sources[0].FetchedAt; got != "2026-10-19" {
			t.Fatalf("fetchedAt = %q", got)
		}
	})
}

func TestLayoutRefusals(t *testing.T) {
	t.Run("stray file", func(t *testing.T) {
		fsys := fixture(t)
		put(fsys, "apps/linear/notes.md", "hello\n")
		wantLoadError(t, fsys, "apps/linear/notes.md: not a catalog file")
	})
	t.Run("yml extension", func(t *testing.T) {
		fsys := fixture(t)
		put(fsys, "platforms/other.yml", "id: other\n")
		wantLoadError(t, fsys, "platforms/other.yml: not a catalog file")
	})
	t.Run("id differs from directory", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/app.yaml", "\nid: linear\n", "\nid: linear-app\n")
		wantLoadError(t, fsys, "apps/linear/app.yaml: id linear-app must equal its directory name linear")
	})
	t.Run("provider app field", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "\napp: linear\n", "\napp: github\n")
		wantLoadError(t, fsys, "apps/linear/providers/linear-mcp.yaml: app github must equal its directory name linear")
	})
	t.Run("provider without app.yaml", func(t *testing.T) {
		fsys := fixture(t)
		delete(fsys, "apps/close/app.yaml")
		wantLoadError(t, fsys, "apps/close: has no app.yaml")
	})
	t.Run("generated tools without a provider", func(t *testing.T) {
		fsys := fixture(t)
		put(fsys, "apps/linear/providers/ghost/tools.generated.json", "{}")
		wantLoadError(t, fsys, "apps/linear/providers/ghost: has no ghost.yaml")
	})
	t.Run("default provider missing", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/app.yaml", "defaultProvider: linear-mcp", "defaultProvider: other")
		wantLoadError(t, fsys, "apps/linear/app.yaml: defaultProvider other is not one of its providers")
	})
	t.Run("empty catalog", func(t *testing.T) {
		cat := mustLoad(t, fstest.MapFS{"README.md": &fstest.MapFile{Data: []byte("not a record\n")}})
		if len(cat.Apps) != 0 || len(cat.Platforms) != 0 {
			t.Fatalf("empty tree loaded %d apps", len(cat.Apps))
		}
	})
}
