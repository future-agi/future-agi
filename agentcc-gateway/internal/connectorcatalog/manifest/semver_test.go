package manifest

import (
	"encoding/json"
	"strings"
	"testing"
	"testing/fstest"
)

// diffAfter loads the fixture before and after mutate and diffs one record.
func diffAfter(t *testing.T, record string, mutate func(t *testing.T, fsys fstest.MapFS)) Change {
	t.Helper()
	return diffEdits(t, record, nil, mutate)
}

// diffEdits loads the fixture with setup, then with setup and mutate, and
// diffs one record.
func diffEdits(t *testing.T, record string, setup, mutate func(t *testing.T, fsys fstest.MapFS)) Change {
	t.Helper()
	load := func(edits ...func(t *testing.T, fsys fstest.MapFS)) *Catalog {
		fsys := fixture(t)
		for _, e := range edits {
			if e != nil {
				e(t, fsys)
			}
		}
		return mustLoad(t, fsys)
	}
	before, after := load(setup), load(setup, mutate)
	pick := func(cat *Catalog) Record {
		app, provider, _ := strings.Cut(record, "/")
		if provider == "" {
			return cat.App(app)
		}
		return cat.App(app).Provider(provider)
	}
	ch, err := SemverDiff(pick(before), pick(after))
	if err != nil {
		t.Fatalf("SemverDiff: %v", err)
	}
	return ch
}

func TestSemverDiffRequiresMajor(t *testing.T) {
	const linear = "apps/linear/providers/linear-mcp.yaml"
	const gen = "apps/gmail/providers/futureagi-rest/tools.generated.json"
	const closeP = "apps/close/providers/close-mcp.yaml"
	type edit3 struct{ path, old, new string }
	cases := []struct {
		name   string
		record string
		edits  []edit3
		want   Bump
		reason string
	}{
		{"a tool removed", "linear/linear-mcp", []edit3{{linear, "    - name: create_issue\n      title: Create issue\n      description: \"Create an issue in a team, with a title and an optional description.\"\n      annotations:\n        readOnlyHint: false\n        destructiveHint: false\n        idempotentHint: false\n        openWorldHint: true\n      posture: ask\n      scopes: [write]\n", ""}},
			BumpMajor, "tool create_issue removed"},
		{"a required input added", "gmail/futureagi-rest", []edit3{{gen, `"required": ["q"],`, `"required": ["q", "labelIds"],`}, {gen, `"maxResults": {`, `"labelIds": {"type": "array"}, "maxResults": {`}},
			BumpMajor, "tool search_threads: required input labelIds added"},
		{"an input renamed", "gmail/futureagi-rest", []edit3{{gen, `"required": ["q"],`, `"required": ["query"],`}, {gen, "\"q\": {\n            \"type\": \"string\",", "\"query\": {\n            \"type\": \"string\","}},
			BumpMajor, "tool search_threads: input q removed"},
		{"an output key removed", "gmail/futureagi-rest", []edit3{{gen, "\"threads\": {\n            \"type\": \"array\",", "\"messages\": {\n            \"type\": \"array\","}},
			BumpMajor, "tool search_threads: output threads removed"},
		{"auth kind changed", "close/close-mcp", []edit3{{closeP, "  kind: api_key\n  credentialParts:\n    - in: header\n      name: Close-API-Key\n      secret: true\n      label: API key\n      helpUrl: https://developer.close.com/mcp\n    - in: header\n      name: Close-Scope\n      secret: false\n      label: Scope\n      help: The scope your Close admin granted the key.\n", "  kind: mcp_oauth\n"}, {closeP, "  - fact: auth.credentialParts\n    url: https://developer.close.com/mcp\n    fetchedAt: 2026-10-19\n", ""}},
			BumpMajor, "auth.kind api_key -> mcp_oauth"},
		{"a scope added", "linear/linear-mcp", []edit3{{linear, "scopes: [read, write]\n", "scopes: [read, write, admin]\n"}},
			BumpMajor, "scope admin added: users consent again"},
		{"an account mode removed", "linear/linear-mcp", []edit3{{linear, "accountModes: [agent, user]\n", "accountModes: [agent]\n"}},
			BumpMajor, "account mode user removed"},
		{"a credential part added", "close/close-mcp", []edit3{{closeP, "    - in: header\n      name: Close-Scope\n", "    - in: header\n      name: Close-Org\n      secret: true\n    - in: header\n      name: Close-Scope\n"}},
			BumpMajor, "credential part header Close-Org added"},
		{"a URL variable added", "gmail/relay", []edit3{{"apps/gmail/providers/relay.yaml", "/servers/{serverId}", "/{region}/servers/{serverId}"}, {"apps/gmail/providers/relay.yaml", "  urlVariables:\n", "  urlVariables:\n    region: {description: Region., pattern: \"^[a-z]{2}$\", scope: org}\n"}},
			BumpMajor, "URL variable region added"},
		{"the EE id changed", "close", []edit3{{"apps/close/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {eeId: close-crm, envPrefix: OMEGA_INTEGRATION_CLOSE}\n"}},
			BumpMajor, "x-omega.eeId close -> close-crm"},
		{"the env prefix changed", "close", []edit3{{"apps/close/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {envPrefix: OMEGA_INTEGRATION_CLOSE_CRM}\n"}},
			BumpMajor, "x-omega.envPrefix OMEGA_INTEGRATION_CLOSE -> OMEGA_INTEGRATION_CLOSE_CRM"},
		{"the OSS id changed", "close/close-mcp", []edit3{{closeP, "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ossId: close-crm}\n"}},
			BumpMajor, "OSS server close (provider close-mcp) is renamed close-crm, and so are its tools"},
		{"a provider withdrawn from OSS", "close/close-mcp", []edit3{{closeP, "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {oss: exclude}\n"}},
			BumpMajor, "OSS no longer receives server close (provider close-mcp)"},
		{"a provider withdrawn from EE", "linear/linear-mcp", []edit3{{linear, "x-gateway: { timeoutMs: 30000 }\n", "x-gateway: { timeoutMs: 30000 }\nx-omega: {ee: exclude}\n"}},
			BumpMajor, "EE no longer receives the app (it took provider linear-mcp)"},
		{"a public client proven offers the server to OSS", "linear/linear-mcp", []edit3{{linear, "x-gateway: { timeoutMs: 30000 }\n", "x-gateway: { timeoutMs: 30000 }\n" + pcFact("true", "https://linear.app/developers/oauth-2-0-authentication")}},
			BumpMinor, "OSS receives server linear (provider linear-mcp)"},
		{"stating no public client changes nothing OSS lists", "linear/linear-mcp", []edit3{{linear, "x-gateway: { timeoutMs: 30000 }\n", "x-gateway: { timeoutMs: 30000 }\n" + pcFact("false", "https://linear.app/developers/oauth-2-0-authentication")}},
			BumpPatch, "patch"},

		{"a tool added", "linear/linear-mcp", []edit3{{linear, "  items:\n", "  items:\n    - {name: get_issue, posture: auto, scopes: [read], annotations: {readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true}}\n"}},
			BumpMinor, "tool get_issue added"},
		{"an optional input added", "gmail/futureagi-rest", []edit3{{gen, `"maxResults": {`, `"pageToken": {"type": "string"}, "maxResults": {`}},
			BumpMinor, "tool search_threads: optional input pageToken added"},
		{"a scope removed", "linear/linear-mcp", []edit3{{linear, "scopes: [read, write]\n", "scopes: [read]\n"}},
			BumpMinor, "scope write removed"},
		{"a description changed", "linear/linear-mcp", []edit3{{linear, "List issues in the user's workspace,", "List the issues in the user's workspace,"}},
			BumpPatch, "patch"},
		{"a source changed", "linear", []edit3{{"apps/linear/app.yaml", "    url: https://github.com/linear\n    fetchedAt: 2026-10-19\n", "    url: https://github.com/linear\n    fetchedAt: 2026-10-20\n"}},
			BumpPatch, "patch"},
		{"an ossId that names the id OSS already uses", "close/close-mcp", []edit3{{closeP, "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ossId: close}\n"}},
			BumpPatch, "patch"},
		{"excluding a provider EE does not take", "github/local", []edit3{{"apps/github/providers/local.yaml", "x-omega: { ossId: github }\n", "x-omega: { ossId: github, ee: exclude }\n"}},
			BumpPatch, "patch"},
		{"a default that moves nothing EE or OSS receives", "github", []edit3{{"apps/github/app.yaml", "defaultProvider: remote", "defaultProvider: local"}},
			BumpMinor, "defaultProvider remote -> local"},
		{"nothing changed", "linear/linear-mcp", nil, BumpNone, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ch := diffAfter(t, tc.record, func(t *testing.T, fsys fstest.MapFS) {
				for _, e := range tc.edits {
					edit(t, fsys, e.path, e.old, e.new)
				}
			})
			if ch.Bump != tc.want {
				t.Fatalf("bump = %v (%v), want %v", ch.Bump, ch.Reasons, tc.want)
			}
			if tc.reason != "" && !strings.Contains(strings.Join(ch.Reasons, "; "), tc.reason) {
				t.Errorf("reasons %v do not mention %q", ch.Reasons, tc.reason)
			}
		})
	}

	t.Run("what EE and OSS receive", func(t *testing.T) {
		// orderly: alpha and beta (oauth2) and gamma (API key), all vendor
		// MCP servers over http, with the OSS ids they derive.
		derived := func(alphaExcluded bool) func(t *testing.T, fsys fstest.MapFS) {
			return func(t *testing.T, fsys fstest.MapFS) {
				orderApp(t, fsys, "alpha")
				for _, id := range []string{"alpha", "beta", "gamma"} {
					edit(t, fsys, "apps/orderly/providers/"+id+".yaml", "x-omega: {ossId: orderly-"+id+"}\n", "")
				}
				// OSS lists alpha's and beta's OAuth servers only with a
				// proven public client.
				for _, id := range []string{"alpha", "beta"} {
					edit(t, fsys, "apps/orderly/providers/"+id+".yaml", "x-gateway: { timeoutMs: 30000 }\n",
						"x-gateway: { timeoutMs: 30000 }\n"+pcFact("true", "https://linear.app/developers/oauth-2-0-authentication"))
				}
				if alphaExcluded {
					edit(t, fsys, "apps/orderly/providers/alpha.yaml", "x-omega:\n  publicClient:\n", "x-omega:\n  oss: exclude\n  publicClient:\n")
				}
			}
		}
		for _, tc := range []struct {
			name, record  string
			setup, mutate func(t *testing.T, fsys fstest.MapFS)
			reasons       []string
		}{
			{"a new default moves EE and renames OSS servers", "orderly", derived(false), func(t *testing.T, fsys fstest.MapFS) {
				edit(t, fsys, "apps/orderly/app.yaml", "defaultProvider: alpha", "defaultProvider: beta")
			}, []string{"EE takes provider beta instead of alpha", "OSS server orderly (provider alpha) is renamed orderly-alpha", "OSS server orderly-beta (provider beta) is renamed orderly"}},
			{"a provider offered to OSS takes the app id from another", "orderly/alpha", derived(true), func(t *testing.T, fsys fstest.MapFS) {
				edit(t, fsys, "apps/orderly/providers/alpha.yaml", "  oss: exclude\n", "")
			}, []string{"OSS server orderly (provider beta) is renamed orderly-beta", "OSS receives server orderly (provider alpha)"}},
		} {
			t.Run(tc.name, func(t *testing.T) {
				ch := diffEdits(t, tc.record, tc.setup, tc.mutate)
				if ch.Bump != BumpMajor {
					t.Fatalf("bump = %v (%v), want major", ch.Bump, ch.Reasons)
				}
				for _, r := range tc.reasons {
					if !strings.Contains(strings.Join(ch.Reasons, "; "), r) {
						t.Errorf("reasons %v do not mention %q", ch.Reasons, r)
					}
				}
			})
		}
	})

	// OSS lists an OAuth server only with a proven public client
	// (the OSS rule), so these start from one.
	proven := func(t *testing.T, fsys fstest.MapFS) {
		withFact(t, fsys, "true", "https://linear.app/developers/oauth-2-0-authentication")
	}
	for _, tc := range []struct {
		name   string
		mutate func(t *testing.T, fsys fstest.MapFS)
		reason string
	}{
		{"an OAuth sign-in left to self-registering clients withdraws the server from OSS", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, linear, "registration: [preregistered, dcr]\n", "registration: [cimd, dcr]\n")
			edit(t, fsys, linear, "    supported: true\n", "    supported: false\n")
		}, "OSS no longer receives server linear (provider linear-mcp): oauth_client_self_registration_only"},
		{"a public client no longer proven withdraws the server from OSS", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, linear, "    supported: true\n", "    supported: false\n")
		}, "OSS no longer receives server linear (provider linear-mcp): oauth_public_client_not_proven"},
		{"a public-client fact removed withdraws the server from OSS", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, linear, pcFact("true", "https://linear.app/developers/oauth-2-0-authentication"), "")
		}, "OSS no longer receives server linear (provider linear-mcp): oauth_public_client_not_proven"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ch := diffEdits(t, "linear/linear-mcp", proven, tc.mutate)
			if ch.Bump != BumpMajor || !strings.Contains(strings.Join(ch.Reasons, "; "), tc.reason) {
				t.Fatalf("bump = %v (%v), want major: %s", ch.Bump, ch.Reasons, tc.reason)
			}
		})
	}

	t.Run("a pre-registered public client offers the server to OSS again", func(t *testing.T) {
		selfOnly := func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, linear, "registration: [preregistered, dcr]\n", "registration: [dcr]\n")
		}
		ch := diffEdits(t, "linear/linear-mcp", selfOnly, func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, linear, "registration: [dcr]\n", "registration: [preregistered, dcr]\n")
			proven(t, fsys)
		})
		if ch.Bump != BumpMinor || !strings.Contains(strings.Join(ch.Reasons, "; "), "OSS receives server linear (provider linear-mcp)") {
			t.Fatalf("bump = %v (%v), want minor: OSS receives server linear", ch.Bump, ch.Reasons)
		}
	})

	t.Run("the version must move by the required bump", func(t *testing.T) {
		before := mustLoad(t, fixture(t)).App("linear").Provider("linear-mcp")
		bumped := func(version string, change func(t *testing.T, fsys fstest.MapFS)) *Provider {
			fsys := fixture(t)
			change(t, fsys)
			edit(t, fsys, linear, "\nversion: 1.0.0\n", "\nversion: "+version+"\n")
			return mustLoad(t, fsys).App("linear").Provider("linear-mcp")
		}
		addScope := func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, linear, "scopes: [read, write]\n", "scopes: [read, write, admin]\n")
		}
		reword := func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, linear, "List issues in the user's workspace,", "List the issues in the user's workspace,")
		}
		wantError(t, CheckVersionBump(before, bumped("1.1.0", addScope)), "linear/linear-mcp: needs a major version bump (1.0.0 -> 1.1.0)", "scope admin added")
		if err := CheckVersionBump(before, bumped("2.0.0", addScope)); err != nil {
			t.Errorf("2.0.0 after a major change: %v", err)
		}
		wantError(t, CheckVersionBump(before, bumped("1.0.0", reword)), "needs a patch version bump (1.0.0 -> 1.0.0)")
		if err := CheckVersionBump(before, bumped("1.0.1", reword)); err != nil {
			t.Errorf("1.0.1 after a patch change: %v", err)
		}
		wantError(t, CheckVersionBump(before, bumped("0.9.0", reword)), "version went backwards (1.0.0 -> 0.9.0)")
		if _, err := SemverDiff(before, mustLoad(t, fixture(t)).App("linear")); err == nil {
			t.Errorf("diffing a provider against an app should fail")
		}
	})
}

// TestSemverDiffReadsBooleanSchemas: JSON Schema allows true and false as
// subschemas, and one of them must not hide the other inputs from the diff.
func TestSemverDiffReadsBooleanSchemas(t *testing.T) {
	provider := func(input string) *Provider {
		return &Provider{App: "app", ID: "p", Version: "1.0.0",
			Tools: Tools{Items: []Tool{{Name: "t", InputSchema: json.RawMessage(input)}}}}
	}
	old := provider(`{"type": "object", "properties": {"q": {"type": "string"}, "any": true}, "required": ["q"]}`)
	removed := provider(`{"type": "object", "properties": {"any": true}}`)
	ch, err := SemverDiff(old, removed)
	if err != nil || ch.Bump != BumpMajor || !strings.Contains(strings.Join(ch.Reasons, "; "), "tool t: input q removed") {
		t.Fatalf("SemverDiff = %v %v, %v", ch.Bump, ch.Reasons, err)
	}
	malformed := provider(`{"type": "object", "properties": {"q": {"type": "string"}, "any": true}, "required": "q"}`)
	if ch, _ := SemverDiff(old, malformed); ch.Bump == BumpMajor {
		t.Fatalf("a malformed required list counts as none, so nothing became required: %v", ch.Reasons)
	}
}

// TestSemverDiffWithoutApps: a provider built by hand has no app, so its
// x-omega keys are compared as they are.
func TestSemverDiffWithoutApps(t *testing.T) {
	provider := func(x *ProviderXOmega) *Provider {
		return &Provider{App: "app", ID: "p", Version: "1.0.0", XOmega: x}
	}
	for _, tc := range []struct {
		name     string
		old, new *ProviderXOmega
		want     Bump
		reason   string
	}{
		{"an ossId set", nil, &ProviderXOmega{OSSID: "app-p"}, BumpMajor, `x-omega.ossId "" -> "app-p": OSS renames the server and its tools`},
		{"excluded from EE", nil, &ProviderXOmega{EE: "exclude"}, BumpMajor, "x-omega.ee: the provider is withdrawn from EE"},
		{"offered to OSS again", &ProviderXOmega{OSS: "exclude"}, &ProviderXOmega{OSS: "include"}, BumpMinor, "x-omega.oss: the provider is offered to OSS"},
		{"a public client no longer proven", &ProviderXOmega{PublicClient: &PublicClient{Supported: true}}, &ProviderXOmega{PublicClient: &PublicClient{Supported: false}},
			BumpMajor, "x-omega.publicClient: OSS may no longer list the server (oauth_public_client_not_proven)"},
		{"a public-client fact removed", &ProviderXOmega{PublicClient: &PublicClient{Supported: true}}, nil,
			BumpMajor, "x-omega.publicClient: OSS may no longer list the server (oauth_public_client_not_proven)"},
		{"a public client proven", nil, &ProviderXOmega{PublicClient: &PublicClient{Supported: true}}, BumpMinor, "x-omega.publicClient: OSS may list the server"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ch, err := SemverDiff(provider(tc.old), provider(tc.new))
			if err != nil || ch.Bump != tc.want || !strings.Contains(strings.Join(ch.Reasons, "; "), tc.reason) {
				t.Fatalf("SemverDiff = %v %v, %v; want %v with %q", ch.Bump, ch.Reasons, err, tc.want, tc.reason)
			}
		})
	}
}
