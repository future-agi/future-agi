package manifest

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
	"testing/fstest"
)

func TestEEBootRulesForEligibleProviders(t *testing.T) {
	t.Run("projection", func(t *testing.T) {
		cat := mustLoad(t, fixture(t))
		got, ok := EEAppFor(cat.App("linear"))
		if !ok {
			t.Fatal("linear is EE-eligible")
		}
		want := EEApp{
			ID: "linear", Name: "Linear", Description: "Issues, projects and cycles.", Category: "developer", Group: "other",
			OAuth: EEOAuth{AuthorizeURL: "https://linear.app/oauth/authorize", TokenURL: "https://api.linear.app/oauth/token",
				Scopes: "read write", PKCE: true, TokenBody: "form", ClientAuth: "body"},
			MCPURL: "https://mcp.linear.app/mcp", EnvPrefix: "OMEGA_INTEGRATION_LINEAR",
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("EEAppFor(linear) =\n%+v\nwant\n%+v", got, want)
		}
		gmail, ok := EEAppFor(cat.App("gmail"))
		if !ok || gmail.MCPURL != "" || gmail.OAuth.ExtraAuthorizeParams["access_type"] != "offline" {
			t.Fatalf("a rest provider gives EE no mcpUrl (the operator sets _MCP_URL): %+v", gmail)
		}
		for _, id := range []string{"close", "snowflake", "stripe"} {
			if _, ok := EEAppFor(cat.App(id)); ok {
				t.Errorf("%s is not EE-eligible", id)
			}
		}
	})

	const p = "apps/linear/providers/linear-mcp.yaml"
	cases := []struct {
		name      string
		path      string
		old, new  string
		want      string
		excludeOK bool // the same data loads once the provider is excluded from EE
	}{
		{"token in a nested field", p, "    registration: [preregistered, dcr]\n",
			"    registration: [preregistered, dcr]\n    tokenResponse: {accessTokenPath: authed_user.access_token}\n",
			"EE reads the token at the top level; set x-omega.ee: exclude until OM5 adds tokenResponse", true},
		{"resource indicator", p, "    pkce: true\n", "    pkce: true\n    resourceIndicator: true\n",
			"EE does not send the RFC 8707 resource", true},
		{"oauth URL variable without a default", p, "    authorizeUrl: https://linear.app/oauth/authorize\n",
			"    authorizeUrl: https://{region}.linear.app/oauth/authorize\n    urlVariables:\n      region: {description: Region., pattern: \"^[a-z]{2}$\", scope: org}\n",
			"EE needs a default for {region} in auth.oauth2.authorizeUrl", true},
		{"untrimmed name", "apps/linear/app.yaml", "\nname: Linear\n", "\nname: \"Linear \"\n",
			"EE trims name", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantLoadError(t, fsys, "EE", tc.want)
			if tc.excludeOK {
				edit(t, fsys, p, "x-gateway: { timeoutMs: 30000 }\n", "x-gateway: { timeoutMs: 30000 }\nx-omega: {ee: exclude}\n")
				mustLoad(t, fsys)
			}
		})
	}

	t.Run("an oauth URL variable with a default is filled in", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, p, "    authorizeUrl: https://linear.app/oauth/authorize\n",
			"    authorizeUrl: https://{region}.linear.app/oauth/authorize\n    urlVariables:\n      region: {description: Region., pattern: \"^[a-z]{2}$\", default: eu, scope: org}\n")
		ee, ok := EEAppFor(mustLoad(t, fsys).App("linear"))
		if !ok || ee.OAuth.AuthorizeURL != "https://eu.linear.app/oauth/authorize" {
			t.Fatalf("authorizeUrl = %q", ee.OAuth.AuthorizeURL)
		}
	})

	t.Run("at most 64 apps in the EE settings file", func(t *testing.T) {
		fsys := fixture(t)
		for i := 1; i <= 62; i++ { // linear, github and slack already qualify: 65 in all
			id := fmt.Sprintf("ee-app-%02d", i)
			cloneApp(t, fsys, "linear", id)
			edit(t, fsys, "apps/"+id+"/providers/linear-mcp.yaml", "url: https://mcp.linear.app/mcp", "url: https://mcp.example.com/"+id+"/mcp")
		}
		wantLoadError(t, fsys, "EE's settings file holds at most 64 apps; 65 qualify")
		edit(t, fsys, "apps/ee-app-01/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ee: exclude}\n")
		mustLoad(t, fsys)
	})

	t.Run("an MCP URL fits EE's 200 characters", func(t *testing.T) {
		fsys := fixture(t)
		long := "https://mcp.linear.app/" + strings.Repeat("m", 178) // 201 characters
		edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "  url: https://mcp.linear.app/mcp\n", "  url: "+long+"\n")
		wantLoadError(t, fsys, "EE: mcpUrl is 201 characters; EE's settings file allows 200")
		edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "  url: "+long+"\n", "  url: "+long[:200]+"\n")
		mustLoad(t, fsys)
	})

	t.Run("an excluded app is not checked", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/app.yaml", "\nname: Linear\n", "\nname: \"Linear \"\n")
		edit(t, fsys, "apps/linear/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ee: exclude}\n")
		mustLoad(t, fsys)
	})

	t.Run("EE trims as JavaScript does", func(t *testing.T) {
		// Go's TrimSpace keeps U+FEFF, which JavaScript's trim removes;
		// format characters and Go-only spaces are refused at the ends too.
		for _, name := range []string{`\uFEFFLinear`, `Linear\uFEFF`, `Linear\u0085`, `Linear\u00A0`, `\u200BLinear`, `Linear\u2028`, `\u3000Linear`, `\uFEFF`} {
			fsys := fixture(t)
			edit(t, fsys, "apps/linear/app.yaml", "\nname: Linear\n", "\nname: \""+name+"\"\n")
			wantLoadError(t, fsys, "apps/linear/app.yaml: EE: EE trims name")
		}
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/app.yaml", "\nname: Linear\n", "\nname: \"Linear\u00A0App\"\n") // inside is fine
		mustLoad(t, fsys)
	})

	t.Run("group google uses Google's token endpoint", func(t *testing.T) {
		const acme = "apps/acme3/providers/linear-mcp.yaml"
		fsys := fixture(t)
		cloneApp(t, fsys, "linear", "acme3")
		edit(t, fsys, acme, "url: https://mcp.linear.app/mcp", "url: https://mcp.acme3.example/mcp")
		edit(t, fsys, acme, "tokenUrl: https://api.linear.app/oauth/token", "tokenUrl: https://acme3.example/oauth/token")
		edit(t, fsys, "apps/acme3/app.yaml", "group: other", "group: google")
		// EE gives a built-in google app the shared client when its own
		// variables are unset, so its secret would go to acme3.example.
		wantLoadError(t, fsys, acme, "EE: app acme3 is in group google, so EE may give it the shared Google OAuth client", "tokenUrl must be https://oauth2.googleapis.com/token")
		edit(t, fsys, acme, "tokenUrl: https://acme3.example/oauth/token", "tokenUrl: https://oauth2.googleapis.com/token")
		mustLoad(t, fsys)
	})
}

// orderApp writes app "orderly" with providers alpha, beta and gamma cut
// from linear's vendor MCP provider.
func orderApp(t *testing.T, fsys fstest.MapFS, defaultProvider string) {
	t.Helper()
	cloneApp(t, fsys, "linear", "orderly")
	src := string(fsys["apps/orderly/providers/linear-mcp.yaml"].Data)
	delete(fsys, "apps/orderly/providers/linear-mcp.yaml")
	for _, id := range []string{"alpha", "beta", "gamma"} {
		s := strings.Replace(src, "\nid: linear-mcp\n", "\nid: "+id+"\n", 1)
		s = strings.Replace(s, "url: https://mcp.linear.app/mcp", "url: https://mcp.example.com/"+id+"/mcp", 1)
		put(fsys, "apps/orderly/providers/"+id+".yaml", s)
	}
	edit(t, fsys, "apps/orderly/app.yaml", "defaultProvider: linear-mcp", "defaultProvider: "+defaultProvider)
	// gamma takes an API key, so EE cannot take it.
	edit(t, fsys, "apps/orderly/providers/gamma.yaml", "auth:\n  kind: oauth2\n  oauth2:\n    authorizeUrl: https://linear.app/oauth/authorize\n    tokenUrl: https://api.linear.app/oauth/token\n    scopes: [read, write]\n    pkce: true\n    tokenBody: form\n    clientAuth: body\n    refresh: { supported: true, rotates: false }\n    registration: [preregistered, dcr]\n",
		"auth:\n  kind: api_key\n  credentialParts:\n    - {in: header, name: Authorization, prefix: \"Bearer \", secret: true}\n")
	edit(t, fsys, "apps/orderly/providers/gamma.yaml", "      scopes: [read]\n", "")
	edit(t, fsys, "apps/orderly/providers/gamma.yaml", "      scopes: [write]\n", "")
	edit(t, fsys, "apps/orderly/providers/gamma.yaml", "  - fact: auth.oauth2.authorizeUrl\n    url: https://linear.app/developers/oauth-2-0-authentication\n    fetchedAt: 2026-10-19\n  - fact: auth.oauth2.tokenUrl\n    url: https://linear.app/developers/oauth-2-0-authentication\n    fetchedAt: 2026-10-19\n  - fact: auth.oauth2.scopes\n    url: https://linear.app/developers/oauth-2-0-authentication\n    fetchedAt: 2026-10-19\n", "")
	for _, id := range []string{"alpha", "beta", "gamma"} {
		edit(t, fsys, "apps/orderly/providers/"+id+".yaml", "x-gateway: { timeoutMs: 30000 }\n", "x-gateway: { timeoutMs: 30000 }\nx-omega: {ossId: orderly-"+id+"}\n")
	}
}

func TestEEEligibleProviderOrder(t *testing.T) {
	ids := func(ps []*Provider) []string {
		var out []string
		for _, p := range ps {
			out = append(out, p.ID)
		}
		return out
	}
	cases := []struct {
		name, defaultProvider string
		mutate                func(t *testing.T, fsys fstest.MapFS)
		wantOrder             []string
		want                  string // "" when no provider is eligible
	}{
		{"default first when eligible", "beta", nil, []string{"beta", "alpha", "gamma"}, "beta"},
		{"an ineligible default is skipped, then by id", "gamma", nil, []string{"gamma", "alpha", "beta"}, "alpha"},
		{"a provider excluded from EE is skipped", "alpha", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, "apps/orderly/providers/alpha.yaml", "x-omega: {ossId: orderly-alpha}", "x-omega: {ee: exclude, ossId: orderly-alpha}")
		}, []string{"alpha", "beta", "gamma"}, "beta"},
		{"an app excluded from EE has none", "alpha", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, "apps/orderly/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ee: exclude}\n")
		}, []string{"alpha", "beta", "gamma"}, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			orderApp(t, fsys, tc.defaultProvider)
			if tc.mutate != nil {
				tc.mutate(t, fsys)
			}
			app := mustLoad(t, fsys).App("orderly")
			if got := ids(app.ProviderOrder()); !reflect.DeepEqual(got, tc.wantOrder) {
				t.Errorf("ProviderOrder = %v, want %v", got, tc.wantOrder)
			}
			p, ok := EligibleForEE(app)
			switch {
			case tc.want == "" && ok:
				t.Errorf("EligibleForEE = %s, want none", p.ID)
			case tc.want != "" && (!ok || p.ID != tc.want):
				t.Errorf("EligibleForEE = %v %v, want %s", p, ok, tc.want)
			}
		})
	}
	t.Run("only oauth2 over http from a vendor or rest provider", func(t *testing.T) {
		cat := mustLoad(t, fixture(t))
		for app, want := range map[string]string{"github": "remote", "gmail": "futureagi-rest", "linear": "linear-mcp", "slack": "slack-mcp"} {
			if p, ok := EligibleForEE(cat.App(app)); !ok || p.ID != want {
				t.Errorf("EligibleForEE(%s) = %v, want %s", app, p, want)
			}
		}
		for _, app := range []string{"close", "stripe", "snowflake"} {
			if p, ok := EligibleForEE(cat.App(app)); ok {
				t.Errorf("EligibleForEE(%s) = %s, want none", app, p.ID)
			}
		}
	})
}

func TestEEIdsPreserved(t *testing.T) {
	t.Run("built-in ids and env prefixes are emitted unchanged", func(t *testing.T) {
		cat := mustLoad(t, fixture(t))
		for app, prefix := range map[string]string{"gmail": "OMEGA_INTEGRATION_GMAIL", "github": "OMEGA_INTEGRATION_GITHUB",
			"linear": "OMEGA_INTEGRATION_LINEAR", "slack": "OMEGA_INTEGRATION_SLACK"} {
			ee, ok := EEAppFor(cat.App(app))
			if !ok || ee.ID != app || ee.EnvPrefix != prefix {
				t.Errorf("EEAppFor(%s) = %q %q, want %q %q", app, ee.ID, ee.EnvPrefix, app, prefix)
			}
		}
	})
	t.Run("a built-in app cannot take another EE id", func(t *testing.T) {
		fsys := fixture(t)
		cloneApp(t, fsys, "gmail", "google-calendar")
		edit(t, fsys, "apps/google-calendar/providers/futureagi-rest.yaml", "{connectorsHost}/gmail/mcp", "{connectorsHost}/google-calendar/mcp")
		edit(t, fsys, "apps/google-calendar/app.yaml", "eeId: gmail, envPrefix: OMEGA_INTEGRATION_GMAIL", "eeId: gcal")
		wantLoadError(t, fsys, "apps/google-calendar/app.yaml", "EE's built-in app google-calendar must keep its id; x-omega.eeId gcal would rename it")
	})
	t.Run("another app cannot take a built-in EE id", func(t *testing.T) {
		fsys := fixture(t)
		cloneApp(t, fsys, "gmail", "gcal")
		edit(t, fsys, "apps/gcal/providers/futureagi-rest.yaml", "{connectorsHost}/gmail/mcp", "{connectorsHost}/gcal/mcp")
		edit(t, fsys, "apps/gcal/app.yaml", "eeId: gmail, envPrefix: OMEGA_INTEGRATION_GMAIL", "eeId: google-calendar")
		wantLoadError(t, fsys, "apps/gcal/app.yaml", "EE id google-calendar belongs to EE's built-in app google-calendar")
	})
	t.Run("a built-in app keeps its env prefix", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/slack/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {envPrefix: OMEGA_INTEGRATION_SLACK_V2}\n")
		wantLoadError(t, fsys, "apps/slack/app.yaml", "EE's built-in app slack must keep envPrefix OMEGA_INTEGRATION_SLACK")
	})
	t.Run("another app cannot take a built-in's env prefix", func(t *testing.T) {
		// notion is not in the fixture: EE's own list is checked, not only
		// the catalog's apps.
		for prefix, owner := range map[string]string{"OMEGA_INTEGRATION_NOTION": "notion", "OMEGA_INTEGRATION_LINEAR": "linear"} {
			fsys := fixture(t)
			cloneApp(t, fsys, "linear", "acme2")
			edit(t, fsys, "apps/acme2/providers/linear-mcp.yaml", "url: https://mcp.linear.app/mcp", "url: https://mcp.acme2.example/mcp")
			edit(t, fsys, "apps/acme2/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {envPrefix: "+prefix+"}\n")
			wantLoadError(t, fsys, "apps/acme2/app.yaml: EE: env prefix "+prefix+" belongs to EE's built-in app "+owner+", whose OAuth client it would read")
		}
	})
	t.Run("two apps cannot share an env prefix", func(t *testing.T) {
		fsys := fixture(t)
		for _, id := range []string{"foo", "bar"} {
			cloneApp(t, fsys, "linear", id)
			edit(t, fsys, "apps/"+id+"/providers/linear-mcp.yaml", "url: https://mcp.linear.app/mcp", "url: https://mcp.example.com/"+id+"/mcp")
		}
		edit(t, fsys, "apps/foo/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {envPrefix: OMEGA_INTEGRATION_BAR}\n")
		wantLoadError(t, fsys, "apps/foo/app.yaml: EE: env prefix OMEGA_INTEGRATION_BAR is emitted by apps bar and foo; EE would give both one OAuth client")
	})
	t.Run("no app takes the shared Google prefix", func(t *testing.T) {
		fsys := fixture(t)
		cloneApp(t, fsys, "linear", "google") // derives OMEGA_INTEGRATION_GOOGLE
		edit(t, fsys, "apps/google/providers/linear-mcp.yaml", "url: https://mcp.linear.app/mcp", "url: https://mcp.example.com/google/mcp")
		// Its own client group would be google, gmail's; it shares linear's.
		edit(t, fsys, "apps/google/providers/linear-mcp.yaml", "    registration: [preregistered, dcr]\n", "    registration: [preregistered, dcr]\n    clientGroup: linear\n")
		wantLoadError(t, fsys, "apps/google/app.yaml: EE: env prefix OMEGA_INTEGRATION_GOOGLE is EE's shared Google OAuth client")
		edit(t, fsys, "apps/google/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {envPrefix: OMEGA_INTEGRATION_GOOGLE_WORKSPACE}\n")
		mustLoad(t, fsys) // nesting under another prefix shares no variable
	})
	t.Run("two apps cannot emit one EE id", func(t *testing.T) {
		fsys := fixture(t)
		cloneApp(t, fsys, "linear", "alpha")
		cloneApp(t, fsys, "linear", "beta")
		for _, id := range []string{"alpha", "beta"} {
			edit(t, fsys, "apps/"+id+"/providers/linear-mcp.yaml", "url: https://mcp.linear.app/mcp", "url: https://mcp.example.com/"+id+"/mcp")
			edit(t, fsys, "apps/"+id+"/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {eeId: shared-app}\n")
		}
		wantLoadError(t, fsys, "EE id shared-app is emitted by apps alpha and beta")
	})
}
