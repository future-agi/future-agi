package manifest

import (
	"fmt"
	"strings"
	"testing"
	"testing/fstest"
)

func TestToolNameFitsServerNamespace(t *testing.T) {
	const id40 = "a-forty-character-app-id-for-namespacing" // 40 characters
	if len(id40) != 40 {
		t.Fatalf("id40 has %d characters", len(id40))
	}
	withTool := func(t *testing.T, tool string) error {
		fsys := fixture(t)
		cloneApp(t, fsys, "linear", id40)
		edit(t, fsys, "apps/"+id40+"/providers/linear-mcp.yaml", "url: https://mcp.linear.app/mcp", "url: https://mcp.example.com/ns/mcp")
		edit(t, fsys, "apps/"+id40+"/providers/linear-mcp.yaml", "- name: create_issue\n", "- name: "+tool+"\n")
		_, err := LoadDir(fsys)
		return err
	}
	if err := withTool(t, strings.Repeat("t", 22)); err != nil { // 40 + 2 + 22 = 64
		t.Errorf("a 64-character <server>__<tool> name was refused: %v", err)
	}
	wantError(t, withTool(t, strings.Repeat("t", 23)), id40+"__"+strings.Repeat("t", 23)+" is 65 characters; <server>__<tool> must fit in 64")

	t.Run("generated tools count too", func(t *testing.T) {
		fsys := fixture(t)
		name := strings.Repeat("s", 58) // gmail + __ + 58 = 65
		edit(t, fsys, "apps/gmail/providers/futureagi-rest/tools.generated.json", `"name": "send_email"`, `"name": "`+name+`"`)
		wantLoadError(t, fsys, "tools.generated.json", "gmail__"+name+" is 65 characters; <server>__<tool> must fit in 64")
	})
	t.Run("tool name characters", func(t *testing.T) {
		for _, bad := range []string{"-starts-with-dash", "has space", "slash/name", strings.Repeat("x", 65)} {
			fsys := fixture(t)
			edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "- name: create_issue\n", "- name: \""+bad+"\"\n")
			wantLoadError(t, fsys, "/tools/items/1/name")
		}
	})
	t.Run("tool names are unique in a provider", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "- name: create_issue\n", "- name: list_issues\n")
		wantLoadError(t, fsys, "tool list_issues is listed twice")
	})
	t.Run("at most 128 tools", func(t *testing.T) {
		fsys := fixture(t)
		var items strings.Builder
		for i := range 127 {
			fmt.Fprintf(&items, "    - {name: extra_%03d, annotations: {readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true}}\n", i)
		}
		edit(t, fsys, "apps/gmail/providers/futureagi-rest.yaml", "  generated: tools.generated.json\n", "  generated: tools.generated.json\n  items:\n"+items.String())
		wantLoadError(t, fsys, "129 tools; a server lists at most 128")
	})
}

func TestPlatformIdNotAnApp(t *testing.T) {
	fsys := fixture(t)
	src := string(fsys["platforms/relay.yaml"].Data)
	put(fsys, "platforms/linear.yaml", strings.Replace(src, "\nid: relay\n", "\nid: linear\n", 1))
	wantLoadError(t, fsys, "platforms/linear.yaml: platform id linear is also an app id")

	t.Run("platform is reserved for the overlay key", func(t *testing.T) {
		fsys := fixture(t)
		cloneApp(t, fsys, "close", "platform")
		edit(t, fsys, "apps/platform/providers/close-mcp.yaml", "url: https://mcp.close.com/mcp", "url: https://mcp.example.com/p/mcp")
		wantLoadError(t, fsys, "apps/platform/app.yaml: app id platform is reserved")
	})
	t.Run("a partner provider names a platform", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/gmail/providers/relay.yaml", "    platform: relay\n", "    platform: nowhere\n")
		wantLoadError(t, fsys, "apps/gmail/providers/relay.yaml: auth.partner.platform nowhere is not a platform")
	})
}

func TestSourcesRequiredPerFact(t *testing.T) {
	const linearP = "apps/linear/providers/linear-mcp.yaml"
	const gmailP = "apps/gmail/providers/futureagi-rest.yaml"
	line := func(fact, url string) string {
		return "  - fact: " + fact + "\n    url: " + url + "\n    fetchedAt: 2026-10-19\n"
	}
	cases := []struct {
		name, path, old, new, want string
	}{
		{"transport URL", linearP, line("transport.url", "https://linear.app/docs/mcp"), "", "transport.url needs a source"},
		{"authorize URL", linearP, line("auth.oauth2.authorizeUrl", "https://linear.app/developers/oauth-2-0-authentication"), "", "auth.oauth2.authorizeUrl needs a source"},
		{"token URL", linearP, line("auth.oauth2.tokenUrl", "https://linear.app/developers/oauth-2-0-authentication"), "", "auth.oauth2.tokenUrl needs a source"},
		{"scope list", linearP, line("auth.oauth2.scopes", "https://linear.app/developers/oauth-2-0-authentication"), "", "auth.oauth2.scopes needs a source"},
		{"revocation URL", gmailP, line("auth.oauth2.revocation.url", "https://developers.google.com/identity/protocols/oauth2/web-server"), "", "auth.oauth2.revocation.url needs a source"},
		{"vendor domains", "apps/linear/app.yaml", line("vendor.domains", "https://linear.app/docs/mcp"), "", "vendor.domains needs a source"},
		{"vendor GitHub orgs", "apps/linear/app.yaml", line("vendor.githubOrgs", "https://github.com/linear"), "", "vendor.githubOrgs needs a source"},
		{"platform URL pattern", "platforms/relay.yaml", "sources:\n" + line("connect.urlPattern", "https://relay.example/docs/mcp"), "sources: []\n", "connect.urlPattern needs a source"},
		{"verify URL", "apps/close/providers/close-mcp.yaml", "tools: { source: upstream_mcp", "  verify: {http: {method: GET, url: https://api.close.com/api/v1/me/}}\ntools: { source: upstream_mcp",
			"auth.verify.http.url needs a source"},
		{"a source must name a fact the record has", linearP, line("transport.url", "https://linear.app/docs/mcp"),
			line("transport.url", "https://linear.app/docs/mcp") + line("auth.oauth2.revocation.url", "https://linear.app/docs/mcp"),
			"sources names auth.oauth2.revocation.url, which this record does not have"},
		{"a rate limit carries its source", linearP, "    source: https://linear.app/developers/rate-limiting\n", "", "/rateLimits/0/source: is required"},
		{"a review lead time carries its source", gmailP, "      source: https://support.google.com/cloud/answer/13463817\n", "", "/auth/oauth2/review/source: is required"},
		{"fetchedAt is a real date", "apps/linear/app.yaml", "    url: https://github.com/linear\n    fetchedAt: 2026-10-19\n", "    url: https://github.com/linear\n    fetchedAt: 2026-02-30\n",
			"/sources/1/fetchedAt: 2026-02-30 is not a calendar date"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantLoadError(t, fsys, tc.path, tc.want)
		})
	}
	t.Run("our own rest endpoint and empty scopes need no source", func(t *testing.T) {
		cat := mustLoad(t, fixture(t))
		for _, s := range cat.App("gmail").Provider("futureagi-rest").Sources {
			if s.Fact == "transport.url" {
				t.Fatalf("the fixture cites the rest URL; the test needs it uncited")
			}
		}
	})
}

func TestCredentialParts(t *testing.T) {
	const p = "apps/close/providers/close-mcp.yaml"
	cases := []struct {
		name, old, new, want string
	}{
		{"a secret part has no value", "      name: Close-API-Key\n      secret: true\n", "      name: Close-API-Key\n      secret: true\n      value: abc\n",
			"/auth/credentialParts/0/value: a secret part never carries a value"},
		{"at least one secret part", "      name: Close-API-Key\n      secret: true\n", "      name: Close-API-Key\n      secret: false\n",
			"an api_key credential needs at least one secret part"},
		{"header names are HTTP tokens", "      name: Close-Scope\n", "      name: Close Scope\n", "/auth/credentialParts/1/name: \"Close Scope\" is not a header name"},
		{"no transport headers", "      name: Close-Scope\n", "      name: Content-Length\n", "/auth/credentialParts/1/name: Content-Length is set by the transport"},
		{"a prefix only on a header", "    - in: header\n      name: Close-Scope\n      secret: false\n", "    - in: query\n      name: scope\n      secret: false\n      prefix: X\n",
			"/auth/credentialParts/1/prefix: only a header part has a prefix"},
		{"parts are unique", "      name: Close-Scope\n", "      name: close-api-key\n", "credential part header close-api-key appears twice"},
		{"a url_variable part fills the URL", "    - in: header\n      name: Close-Scope\n      secret: false\n", "    - in: url_variable\n      name: token\n      secret: true\n",
			"credential part token is a url_variable but transport.url has no {token}"},
		{"api_key needs parts", "  kind: api_key\n  credentialParts:\n    - in: header\n      name: Close-API-Key\n      secret: true\n      label: API key\n      helpUrl: https://developer.close.com/mcp\n    - in: header\n      name: Close-Scope\n      secret: false\n      label: Scope\n      help: The scope your Close admin granted the key.\n",
			"  kind: api_key\n", "/auth/credentialParts: is required when auth.kind is api_key"},
		{"parts only for api_key", "  kind: api_key\n", "  kind: none\n", "/auth/credentialParts: only an api_key credential has parts"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, p, tc.old, tc.new)
			wantLoadError(t, fsys, p, tc.want)
		})
	}
	t.Run("a secret URL variable and a query key", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, p, "url: https://mcp.close.com/mcp", `url: "https://mcp.close.com/{token}/mcp"`)
		edit(t, fsys, p, "    - in: header\n      name: Close-Scope\n      secret: false\n", "    - in: url_variable\n      name: token\n      secret: true\n")
		edit(t, fsys, p, "    - in: header\n      name: Close-API-Key\n      secret: true\n", "    - in: query\n      name: apiKey\n      secret: true\n")
		edit(t, fsys, "apps/close/app.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {oss: exclude}\n")
		mustLoad(t, fsys)
	})
}

func TestClientApprovalGate(t *testing.T) {
	const p = "apps/slack/providers/slack-mcp.yaml"
	verified := "quality:\n  tier: verified\n  review: {by: connectors-maintainer, at: 2026-10-19}\n"
	fsys := fixture(t)
	edit(t, fsys, p, "quality: { tier: listed }\n", verified)
	wantLoadError(t, fsys, p, "needs vendor approval (Slack Marketplace listing): tier must stay listed until clientApproval.approvedAt is set")

	edit(t, fsys, p, "    source: https://docs.slack.dev/ai/slack-mcp-server/\n", "    source: https://docs.slack.dev/ai/slack-mcp-server/\n    approvedAt: 2026-11-02\n")
	mustLoad(t, fsys)

	t.Run("only OAuth providers have client approval", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/close/providers/close-mcp.yaml", "tools: { source: upstream_mcp",
			"  clientApproval: {required: true, program: P, url: https://close.com/apply, source: https://close.com/docs}\ntools: { source: upstream_mcp")
		wantLoadError(t, fsys, "/auth/clientApproval: only an OAuth provider needs client approval")
	})
}

// TestClientGroupSharesOneClient: CN3 resolves an OAuth client by
// clientGroup, else by the app id, so every app id is a group too, and a
// group's secret goes only to the token and revocation endpoints its members
// share.
func TestClientGroupSharesOneClient(t *testing.T) {
	const acme = "apps/acme/providers/linear-mcp.yaml"
	acmeJoinsLinear := func(t *testing.T, tokenURL string) fstest.MapFS {
		fsys := fixture(t)
		cloneApp(t, fsys, "linear", "acme")
		edit(t, fsys, acme, "url: https://mcp.linear.app/mcp", "url: https://mcp.acme.example/mcp")
		edit(t, fsys, acme, "tokenUrl: https://api.linear.app/oauth/token", "tokenUrl: "+tokenURL)
		edit(t, fsys, acme, "    registration: [preregistered, dcr]\n", "    registration: [preregistered, dcr]\n    clientGroup: linear\n")
		return fsys
	}
	t.Run("a clientGroup naming another app joins that app's group", func(t *testing.T) {
		// linear's provider names no clientGroup, so it is group linear.
		wantLoadError(t, acmeJoinsLinear(t, "https://acme.example/oauth/token"),
			"client group linear is one OAuth client (a provider without clientGroup is in its app's group): tokenUrl", "https://acme.example/oauth/token")
		mustLoad(t, acmeJoinsLinear(t, "https://api.linear.app/oauth/token"))
	})
	t.Run("two providers of one app share its client", func(t *testing.T) {
		fsys := fixture(t)
		orderApp(t, fsys, "alpha")
		edit(t, fsys, "apps/orderly/providers/beta.yaml", "tokenUrl: https://api.linear.app/oauth/token", "tokenUrl: https://beta.example/oauth/token")
		wantLoadError(t, fsys, "apps/orderly/providers/beta.yaml: client group orderly is one OAuth client", "tokenUrl https://beta.example/oauth/token differs from apps/orderly/providers/alpha.yaml's")
		edit(t, fsys, "apps/orderly/providers/beta.yaml", "    registration: [preregistered, dcr]\n", "    registration: [preregistered, dcr]\n    clientGroup: orderly-beta\n")
		mustLoad(t, fsys) // its own client
	})

	const gcal = "apps/gcal/providers/futureagi-rest.yaml"
	googleClone := func(t *testing.T) fstest.MapFS {
		fsys := fixture(t)
		cloneApp(t, fsys, "gmail", "gcal")
		edit(t, fsys, gcal, "{connectorsHost}/gmail/mcp", "{connectorsHost}/gcal/mcp")
		edit(t, fsys, "apps/gcal/app.yaml", "x-omega: { eeId: gmail, envPrefix: OMEGA_INTEGRATION_GMAIL }\n", "")
		mustLoad(t, fsys)
		return fsys
	}
	revocation := "    revocation:\n      url: https://oauth2.googleapis.com/revoke\n      method: POST\n      tokenBody: form\n      tokenField: token\n      clientAuth: body\n"
	const revocationSource = "  - fact: auth.oauth2.revocation.url\n    url: https://developers.google.com/identity/protocols/oauth2/web-server\n    fetchedAt: 2026-10-19\n"
	const gmail = "apps/gmail/providers/futureagi-rest.yaml"
	attacker := strings.Replace(revocation, "https://oauth2.googleapis.com/revoke", "https://revoke.attacker.example/revoke", 1)
	// gcal sorts first, so each message is on gmail's file and names both.
	for _, tc := range []struct {
		name string
		edit func(t *testing.T, fsys fstest.MapFS)
		want string
	}{
		{"a revocation URL", func(t *testing.T, fsys fstest.MapFS) { edit(t, fsys, gcal, revocation, attacker) },
			"revocation https://oauth2.googleapis.com/revoke (clientAuth body) differs from apps/gcal/providers/futureagi-rest.yaml's https://revoke.attacker.example/revoke (clientAuth body)"},
		{"revocation client auth", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, gcal, revocation, strings.Replace(revocation, "clientAuth: body", "clientAuth: basic", 1))
		}, "revocation https://oauth2.googleapis.com/revoke (clientAuth body) differs from apps/gcal/providers/futureagi-rest.yaml's https://oauth2.googleapis.com/revoke (clientAuth basic)"},
		// A member that names no revocation endpoint does not let another
		// add one: the attacker's clone is the only member that declares it.
		{"a revocation endpoint only one member declares", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, gmail, revocation, "")
			edit(t, fsys, gmail, revocationSource, "")
			edit(t, fsys, gcal, revocation, attacker)
		}, "revocation none differs from apps/gcal/providers/futureagi-rest.yaml's https://revoke.attacker.example/revoke (clientAuth body)"},
		{"a member without the group's revocation endpoint", func(t *testing.T, fsys fstest.MapFS) {
			edit(t, fsys, gcal, revocation, "")
			edit(t, fsys, gcal, revocationSource, "")
		}, "revocation https://oauth2.googleapis.com/revoke (clientAuth body) differs from apps/gcal/providers/futureagi-rest.yaml's none"},
	} {
		t.Run(tc.name+" differs within a group", func(t *testing.T) {
			fsys := googleClone(t)
			tc.edit(t, fsys)
			wantLoadError(t, fsys, "apps/gmail/providers/futureagi-rest.yaml: client group google is one OAuth client (a provider without clientGroup is in its app's group): "+tc.want)
		})
	}
	t.Run("a group with no revocation endpoint at all", func(t *testing.T) {
		fsys := googleClone(t)
		for _, p := range []string{gmail, gcal} {
			edit(t, fsys, p, revocation, "")
			edit(t, fsys, p, revocationSource, "")
		}
		mustLoad(t, fsys)
	})
}

func TestGenericTools(t *testing.T) {
	const p = "apps/stripe/providers/stripe-mcp.yaml"
	cat := mustLoad(t, fixture(t))
	for _, tool := range cat.App("stripe").Provider("stripe-mcp").Tools.Items {
		if !tool.Generic {
			t.Errorf("%s should be generic", tool.Name)
		}
	}
	t.Run("genericArguments needs generic", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, p, "      posture: deny\n      generic: true\n", "      posture: deny\n      genericArguments: [path]\n")
		wantLoadError(t, fsys, "/tools/items/1/genericArguments: needs generic: true")
	})
	t.Run("genericArguments name input properties", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/gmail/providers/futureagi-rest/tools.generated.json", `"posture": "ask",`, `"posture": "ask", "generic": true, "genericArguments": ["route"],`)
		wantLoadError(t, fsys, "genericArguments names route, which is not an inputSchema property")
	})
}

func TestURLVariables(t *testing.T) {
	const snow = "apps/snowflake/providers/cortex-mcp.yaml"
	cat := mustLoad(t, fixture(t))
	if vars := cat.App("snowflake").Provider("cortex-mcp").Transport.URLVariables; vars["accountUrl"].Scope != "org" || len(vars) != 4 {
		t.Fatalf("snowflake variables = %v", vars)
	}
	cases := []struct {
		name, path, old, new, want string
	}{
		{"an undeclared variable", snow, "/mcp-servers/{serverName}", "/mcp-servers/{serverName}/{extra}", "transport.url uses {extra}, which is not declared"},
		{"an unused variable", snow, "/mcp-servers/{serverName}", "/mcp-servers/server", "transport.urlVariables declares serverName, which transport.url does not use"},
		{"connectorsHost is for rest providers", "apps/close/providers/close-mcp.yaml", "url: https://mcp.close.com/mcp", `url: "https://{connectorsHost}/close/mcp"`,
			"transport.url uses {connectorsHost}, which only a rest provider may use"},
		{"a rest provider is served from connectorsHost", "apps/gmail/providers/futureagi-rest.yaml", "https://{connectorsHost}/gmail/mcp", "https://mcp.example.com/gmail/mcp",
			"a rest provider's transport.url is https://{connectorsHost}/gmail/mcp"},
		{"a default matches the pattern", snow, `      description: "The account host, such as myorg-myaccount.snowflakecomputing.com."` + "\n",
			`      description: "The account host, such as myorg-myaccount.snowflakecomputing.com."` + "\n      default: example.com\n",
			"default example.com does not match its pattern"},
		{"a pattern compiles", snow, "    database:\n      description: The database that holds the MCP server object.\n      pattern: \"^[A-Za-z_][A-Za-z0-9_]{0,254}$\"\n",
			"    database:\n      description: The database that holds the MCP server object.\n      pattern: \"^[A-Za-z_($\"\n", "pattern does not compile"},
		{"oauth URL variables are declared", snow, `    tokenUrl: "https://{accountUrl}/oauth/token-request"` + "\n", `    tokenUrl: "https://{account}/oauth/token-request"` + "\n",
			"auth.oauth2.tokenUrl uses {account}, which is not declared"},
		{"a vendor MCP URL is public", "apps/close/providers/close-mcp.yaml", "url: https://mcp.close.com/mcp", "url: https://mcp.internal/mcp",
			"/transport/url: host mcp.internal is not a public host"},
		{"a variable of both blocks is one value", snow, "        description: The account host.\n        pattern: \"^[a-z0-9-]+([.][a-z0-9-]+)*[.]snowflakecomputing[.]com$\"\n",
			"        description: The account host.\n        pattern: \"^[a-z0-9.-]+$\"\n",
			"auth.oauth2.urlVariables.accountUrl and transport.urlVariables.accountUrl are one value, so their pattern, scope and default agree"},
		{"a user never chooses where the client secret goes", snow, "        description: The account host.\n        pattern: \"^[a-z0-9-]+([.][a-z0-9-]+)*[.]snowflakecomputing[.]com$\"\n        scope: org\n",
			"        description: The account host.\n        pattern: \"^[a-z0-9-]+([.][a-z0-9-]+)*[.]snowflakecomputing[.]com$\"\n        scope: user\n",
			"auth.oauth2.tokenUrl: {accountUrl} is in the host the client secret is sent to, so it is filled per org (scope: org), never per user"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantLoadError(t, fsys, tc.path, tc.want)
		})
	}
	t.Run("a user may choose the authorize host, which gets the client id only", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "    authorizeUrl: https://linear.app/oauth/authorize\n",
			"    authorizeUrl: https://{region}.linear.app/oauth/authorize\n    urlVariables:\n      region: {description: Region., pattern: \"^[a-z]{2}$\", default: eu, scope: user}\n")
		mustLoad(t, fsys)
	})
	t.Run("a host that ends in a number is an IPv4 address", func(t *testing.T) {
		// WHATWG URL parsing, which EE uses, reads each of these as an
		// address (the first four are private), or refuses the URL.
		for _, host := range []string{"127.1", "10.1", "0x7f.0.0.1", "0177.0.0.1", "8.8.8.010", "0x08080808", "example.123", "mcp.0x1"} {
			fsys := fixture(t)
			edit(t, fsys, "apps/close/providers/close-mcp.yaml", "url: https://mcp.close.com/mcp", "url: https://"+host+"/mcp")
			wantLoadError(t, fsys, "apps/close/providers/close-mcp.yaml: /transport/url: host "+host+" ends in a number")
		}
		for host, ok := range map[string]bool{"8.8.8.8": true, "10.0.0.1": false, "123.example.com": true, "mcp.0xfoo.example": true} {
			fsys := fixture(t)
			edit(t, fsys, "apps/close/providers/close-mcp.yaml", "url: https://mcp.close.com/mcp", "url: https://"+host+"/mcp")
			if _, err := LoadDir(fsys); (err == nil) != ok {
				t.Errorf("host %s: LoadDir error %v, want ok=%v", host, err, ok)
			}
		}
	})
}

func TestProviderKindRules(t *testing.T) {
	const linear = "apps/linear/providers/linear-mcp.yaml"
	cases := []struct {
		name, path, old, new, want string
	}{
		{"vendor MCP over stdio", "apps/github/providers/remote.yaml", "transport: { kind: http, url: https://api.githubcopilot.com/mcp/ }", "transport: {kind: stdio, command: npx}",
			"/transport/kind: a vendor_mcp provider uses http"},
		{"http hosts are hosted", linear, "hosted: true\n", "hosted: false\n", "/hosted: an http provider is hosted"},
		{"trusted needs a review and tier", "apps/github/providers/remote.yaml", "trusted: false\n", "trusted: true\n",
			"/trusted: true only after a maintainer review, at tier verified or optimized"},
		{"oauth2 needs its block", "apps/github/providers/remote.yaml", "  kind: oauth2\n", "  kind: mcp_oauth\n", "/auth/oauth2: only an oauth2 provider has an oauth2 block"},
		{"vendorStatus cites its label", linear, "vendorStatus: { label: ga, source: https://linear.app/docs/mcp }", "vendorStatus: {label: ga}",
			"/vendorStatus/source: is required when the label is ga"},
		{"a sunset needs its date", linear, "lifecycle: { status: active }\nsources:", "lifecycle: {status: sunset}\nsources:", "/lifecycle/sunsetAt: is required when status is sunset"},
		{"replacedBy names a provider", linear, "lifecycle: { status: active }\nsources:", "lifecycle: {status: deprecated, replacedBy: other}\nsources:",
			"lifecycle.replacedBy other is not a provider of linear"},
		{"generated tools are for rest providers", linear, "  defaultPosture: ask\n  snapshot", "  defaultPosture: ask\n  generated: tools.generated.json\n  snapshot",
			"/tools/generated: only an operations source has generated tools"},
		{"the generated spec matches upstream.spec", "apps/gmail/providers/futureagi-rest/tools.generated.json", `"sha256": "a0408c1f`, `"sha256": "b0408c1f`,
			"tools.generated.json: spec sha256 b0408c1f"},
		{"verify names a tool", linear, "verify: { tool: list_issues }", "verify: {tool: no_such_tool}", "auth.verify.tool no_such_tool is not one of its tools"},
		{"reserved authorize parameters", "apps/gmail/providers/futureagi-rest.yaml", "      access_type: offline\n", "      redirect_uri: https://evil.example\n      access_type: offline\n",
			"/auth/oauth2/extraAuthorizeParams: redirect_uri is set by the connect flow"},
		{"never forward credentials", linear, "x-gateway: { timeoutMs: 30000 }", "x-gateway: {timeoutMs: 30000, forwardHeaders: [Authorization]}",
			"/x-gateway/forwardHeaders: Authorization carries a credential and is never forwarded"},
		{"never forward the gateway's own headers", linear, "x-gateway: { timeoutMs: 30000 }", "x-gateway: {timeoutMs: 30000, forwardHeaders: [X-Request-Id, X-Agentcc-Virtual-Key]}",
			"/x-gateway/forwardHeaders: the gateway never forwards X-Agentcc-Virtual-Key"},
		{"never forward Omega's headers", linear, "x-gateway: { timeoutMs: 30000 }", "x-gateway: {timeoutMs: 30000, forwardHeaders: [x-omega-run]}",
			"/x-gateway/forwardHeaders: the gateway never forwards x-omega-run"},
		{"pricing never holds a price", linear, "pricing: { model: free }", "pricing: {model: vendor_metered, notes: Costs $0.002 per call}",
			"/pricing/notes: holds a price; prices live in the gateway-only overlay"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantLoadError(t, fsys, tc.path, tc.want)
		})
	}
	t.Run("community packages stay out of the public catalog", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, "apps/github/providers/local.yaml", "publisher: vendor\n", "publisher: community\n")
		cat := mustLoad(t, fsys) // an operator catalog may hold them
		wantError(t, CheckPublic(cat), "apps/github/providers/local.yaml: publisher community: the public catalog holds no community packages")
		if err := CheckPublic(mustLoad(t, fixture(t))); err != nil {
			t.Fatalf("CheckPublic(fixture) = %v", err)
		}
	})
}

// TestOverlayNeverInCatalog: price-overlay data stays out of the open data
// (Critic C4.2). An overlay document is not any record kind, and no file
// outside the layout is accepted.
func TestOverlayNeverInCatalog(t *testing.T) {
	overlay := "schema: connector-price-overlay/v1\nkey: gmail/relay\nbillingMode: byok\n" +
		"estimate: [{unit: tool_call, usdPerUnit: 0.0003, source: https://relay.example/pricing, checkedAt: 2026-10-19}]\n"
	fsys := fixture(t)
	put(fsys, "platforms/relay-pricing.yaml", overlay)
	wantLoadError(t, fsys, "platforms/relay-pricing.yaml: /schema: must be \"connector-manifest/v1#platform\"")

	fsys = fixture(t)
	put(fsys, "apps/gmail/providers/relay/pricing.yaml", overlay)
	wantLoadError(t, fsys, "apps/gmail/providers/relay/pricing.yaml: not a catalog file")
}
