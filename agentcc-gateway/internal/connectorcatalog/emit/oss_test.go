package emit

import (
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// McpCatalogServer's members in declaration order (OSS
// packages/core/src/mcp-registry.ts:38-74 at 2afe4a0cc7), as each kind of
// server carries them.
var (
	ossHTTPKeys  = []string{"id", "name", "description", "transport", "url", "trusted", "auth", "hosted", "tags"}
	ossStdioKeys = []string{"id", "name", "description", "transport", "command", "args", "trusted", "hosted", "tags"}
)

func TestOSSRegistryKeepsExistingIds(t *testing.T) {
	cat, meta := fixture(t)
	// This test pins ids and members. Which OAuth servers OSS can sign in
	// to is TestOSSRegistryListsOnlyServersWithAPublicClient's, so here every
	// OAuth provider has a proven public client.
	for _, a := range cat.Apps {
		for _, p := range a.Providers {
			if p.Auth.Kind == manifest.AuthOAuth2 || p.Auth.Kind == manifest.AuthMCPOAuth {
				proven(p)
			}
		}
	}
	doc := parse(t, emitTarget(t, TargetOSSRegistry, cat, meta)["oss-registry.json"])
	if member(doc, "schema") != OSSRegistrySchema {
		t.Fatalf("schema %v", member(doc, "schema"))
	}
	byID := map[string]map[string]any{}
	for _, s := range list(t, doc, "servers") {
		id := member(s, "id").(string)
		want := ossHTTPKeys
		if member(s, "transport") == "stdio" {
			want = ossStdioKeys
		}
		if !slices.Equal(keys(s), want) {
			t.Errorf("server %s: members %v, want %v", id, keys(s), want)
		}
		byID[id] = map[string]any{}
		for _, m := range s {
			byID[id][m.Key] = m.Value
		}
	}
	// OSS's existing ids, through the providers that carry them today: the
	// GitHub app's stdio server is github and its remote github-remote;
	// linear and stripe keep their ids and URLs.
	for _, b := range OSSBuiltIns {
		got, ok := byID[b.ID]
		if !ok {
			continue // the fixture does not carry this app
		}
		if got["transport"] != b.Transport || (b.URL != "" && got["url"] != b.URL) {
			t.Errorf("OSS id %s: transport %v url %v; want %s %s", b.ID, got["transport"], got["url"], b.Transport, b.URL)
		}
	}
	for _, id := range []string{"github", "github-remote", "linear", "stripe"} {
		if _, ok := byID[id]; !ok {
			t.Errorf("OSS id %s is missing", id)
		}
	}
	if got := byID["github"]["command"]; got != "docker" {
		t.Errorf("github is OSS's Docker stdio server; command %v", got)
	}
	// Ids the emitter derives without an ossId, as §2.4 states them.
	if _, ok := byID["close"]; !ok {
		t.Error("close: the first eligible provider takes the app id")
	}
	// K2 (rest) and partner providers never reach OSS.
	for id, s := range byID {
		if s["url"] == "https://{connectorsHost}/gmail/mcp" {
			t.Errorf("OSS server %s is a rest provider", id)
		}
	}

	// The emitter's own guard, with the loader bypassed: a built-in hosted
	// URL keeps its id, and a built-in id stays with its app and transport.
	sentry := &manifest.Catalog{Apps: []*manifest.App{appWith("sentry",
		oauthProvider("sentry", "sentry-mcp", manifest.KindVendorMCP, "https://mcp.sentry.dev/mcp"))}}
	_, err := Target(TargetOSSRegistry, sentry, testMeta)
	wantError(t, err, "set x-omega.ossId: sentry-remote")
	sentry.Apps[0].Providers[0].XOmega = &manifest.ProviderXOmega{OSSID: "sentry-remote"}
	proven(sentry.Apps[0].Providers[0])
	files, err := Target(TargetOSSRegistry, sentry, testMeta)
	if err != nil {
		t.Fatalf("with ossId sentry-remote: %v", err)
	}
	if got := member(list(t, parse(t, files["oss-registry.json"]), "servers")[0], "id"); got != "sentry-remote" {
		t.Fatalf("sentry's server id %v", got)
	}
	taken := &manifest.Catalog{Apps: []*manifest.App{appWith("notes",
		oauthProvider("notes", "notes-mcp", manifest.KindVendorMCP, "https://mcp.notes.example.com/mcp"))}}
	taken.Apps[0].Providers[0].XOmega = &manifest.ProviderXOmega{OSSID: "notion"}
	_, err = Target(TargetOSSRegistry, taken, testMeta)
	wantError(t, err, "OSS id notion is OSS's http server for app notion")
	setup := vendorApps(1)
	setup.Apps[0].Providers[0].Auth = manifest.Auth{Kind: manifest.AuthLocalSetup}
	_, err = Target(TargetOSSRegistry, setup, testMeta)
	wantError(t, err, "has none for local_setup")
	twice := vendorApps(2)
	twice.Apps[1].Providers[0].XOmega = &manifest.ProviderXOmega{OSSID: "app00"}
	_, err = Target(TargetOSSRegistry, twice, testMeta)
	wantError(t, err, "also emitted")
}

// registered is an OAuth vendor MCP app whose authorization server takes
// clients as registration says.
func registered(id string, registration ...string) *manifest.App {
	p := oauthProvider(id, "mcp", manifest.KindVendorMCP, "https://mcp."+id+".example.com/mcp")
	p.Auth.OAuth2.Registration = registration
	return appWith(id, p)
}

// publicClientFact is an x-omega.publicClient block for the provider's app.
func publicClientFact(p *manifest.Provider, supported bool) *manifest.PublicClient {
	return &manifest.PublicClient{Supported: supported, Evidence: []manifest.PublicClientEvidence{{
		URL: "https://" + p.App + ".example.com/docs/oauth", FetchedAt: "2026-10-22", SHA256: strings.Repeat("cd", 32), Quote: "q"}}}
}

// stating sets the provider's x-omega.publicClient, keeping its other keys.
func stating(p *manifest.Provider, pc *manifest.PublicClient) *manifest.Provider {
	if p.XOmega == nil {
		p.XOmega = &manifest.ProviderXOmega{}
	}
	p.XOmega.PublicClient = pc
	return p
}

// proven gives the provider a public client proven by its vendor's page.
func proven(p *manifest.Provider) *manifest.Provider { return stating(p, publicClientFact(p, true)) }

// provenApp is registered with a proven public client.
func provenApp(id string, registration ...string) *manifest.App {
	a := registered(id, registration...)
	proven(a.Providers[0])
	return a
}

func ossIDs(t *testing.T, files Files) []string {
	t.Helper()
	var ids []string
	for _, s := range list(t, parse(t, files["oss-registry.json"]), "servers") {
		ids = append(ids, member(s, "id").(string))
	}
	return ids
}

// OSS Omega's `omega mcp login` signs in with a client id registered
// beforehand, and neither registers a client (DCR) nor has a client ID
// metadata document (CIMD) (IR-CN19-8). A server whose OAuth sign-in takes
// only clients that register themselves is left out of oss-registry.json
// (AM-C25); one that also takes a pre-registered client, or declares no
// registration, or authenticates another way, stays.
func TestOSSRegistryLeavesOutSelfRegistrationOnlyOAuth(t *testing.T) {
	key := oauthProvider("keyed", "api-key", manifest.KindVendorMCP, "https://mcp.keyed.example.com/mcp")
	key.Auth = manifest.Auth{Kind: manifest.AuthAPIKey}
	// The pre-registered ones have a proven public client, so only the
	// self-registration rule decides here (OWNER-RULINGS 30's fact:
	// TestOSSRegistryListsOnlyServersWithAPublicClient). A self-registering
	// one is left out by this rule even with a public client stated, which
	// the loader refuses as a contradiction.
	cat := &manifest.Catalog{Apps: []*manifest.App{
		provenApp("dcr-only", "dcr"),
		registered("cimd-dcr", "cimd", "dcr"),
		registered("cimd-only", "cimd"),
		provenApp("pre-dcr", "preregistered", "dcr"),
		provenApp("pre-only", "preregistered"),
		provenApp("undeclared"),
		appWith("keyed", key),
	}}
	got := ossIDs(t, emitTarget(t, TargetOSSRegistry, cat, testMeta))
	want := []string{"pre-dcr", "pre-only", "undeclared", "keyed"}
	if !slices.Equal(got, want) {
		t.Fatalf("oss-registry.json lists %v, want %v", got, want)
	}
	left := OSSLeftOutServers(cat)
	wantLeft := []OSSLeftOut{
		{ID: "dcr-only", Ref: "dcr-only/mcp@1.0.0", Registration: []string{"dcr"}, Reason: manifest.ReasonOAuthSelfRegistrationOnly},
		{ID: "cimd-dcr", Ref: "cimd-dcr/mcp@1.0.0", Registration: []string{"cimd", "dcr"}, Reason: manifest.ReasonOAuthSelfRegistrationOnly},
		{ID: "cimd-only", Ref: "cimd-only/mcp@1.0.0", Registration: []string{"cimd"}, Reason: manifest.ReasonOAuthSelfRegistrationOnly},
	}
	if !slices.EqualFunc(left, wantLeft, func(a, b OSSLeftOut) bool {
		return a.ID == b.ID && a.Ref == b.Ref && slices.Equal(a.Registration, b.Registration) && a.Reason == b.Reason
	}) {
		t.Fatalf("OSSLeftOutServers = %+v, want %+v", left, wantLeft)
	}
	if manifest.OSSLeftOutReasons[manifest.ReasonOAuthSelfRegistrationOnly] == "" {
		t.Fatal("the rule has no explanation for the build's report")
	}

	// OSS's own linear id leaves too, and the emitter does not refuse it.
	linear := registered("linear", "cimd", "dcr")
	linear.Providers[0].XOmega = &manifest.ProviderXOmega{OSSID: "linear"}
	got = ossIDs(t, emitTarget(t, TargetOSSRegistry, &manifest.Catalog{Apps: []*manifest.App{linear, provenApp("pre-only", "preregistered")}}, testMeta))
	if !slices.Equal(got, []string{"pre-only"}) {
		t.Fatalf("with linear's server self-registration only, oss-registry.json lists %v", got)
	}

	// A server left out keeps its id: the id is still checked, and no other
	// server takes it, so the server comes back under it when the rule lifts.
	taken := registered("notes", "dcr")
	taken.Providers[0].XOmega = &manifest.ProviderXOmega{OSSID: "notion"}
	_, err := Target(TargetOSSRegistry, &manifest.Catalog{Apps: []*manifest.App{taken}}, testMeta)
	wantError(t, err, "OSS id notion is OSS's http server for app notion")
	held := registered("held", "dcr")
	clash := provenApp("clash", "preregistered")
	clash.Providers[0].XOmega = &manifest.ProviderXOmega{OSSID: "held"}
	_, err = Target(TargetOSSRegistry, &manifest.Catalog{Apps: []*manifest.App{held, clash}}, testMeta)
	wantError(t, err, "OSS id held is also emitted for app held")
}

// OSS Omega's `omega mcp login` sends a client id it is given and a PKCE
// verifier, and no client secret (OSS:packages/cli/src/chat/mcp-oauth.ts).
// So oss-registry.json lists an OAuth server only when its record proves a
// public client a person can register (x-omega.publicClient.supported:
// true, OWNER-RULINGS 30); one that states none, or states nothing, is left
// out by name, and the report says which. A server that signs in another
// way is not touched.
func TestOSSRegistryListsOnlyServersWithAPublicClient(t *testing.T) {
	key := oauthProvider("keyed", "api-key", manifest.KindVendorMCP, "https://mcp.keyed.example.com/mcp")
	key.Auth = manifest.Auth{Kind: manifest.AuthAPIKey}
	open := oauthProvider("open", "mcp", manifest.KindVendorMCP, "https://mcp.open.example.com/mcp")
	open.Auth = manifest.Auth{Kind: manifest.AuthNone}
	local := oauthProvider("local", "local", manifest.KindVendorLocal, "")
	local.Transport = manifest.Transport{Kind: "stdio", Command: "npx", Args: []string{"-y", "local-mcp"}}
	local.Hosted = false
	local.Auth = manifest.Auth{Kind: manifest.AuthLocalSetup}
	discover := oauthProvider("discover", "mcp", manifest.KindVendorMCP, "https://mcp.discover.example.com/mcp")
	discover.Auth = manifest.Auth{Kind: manifest.AuthMCPOAuth}
	refutedDiscover := oauthProvider("refuted-discover", "mcp", manifest.KindVendorMCP, "https://mcp.refuted-discover.example.com/mcp")
	refutedDiscover.Auth = manifest.Auth{Kind: manifest.AuthMCPOAuth}
	stating(refutedDiscover, publicClientFact(refutedDiscover, false))
	refuted := registered("refuted", "preregistered", "dcr")
	stating(refuted.Providers[0], publicClientFact(refuted.Providers[0], false))
	other := registered("other", "preregistered")
	other.Providers[0].XOmega = &manifest.ProviderXOmega{OSSID: "other-id"}
	cat := &manifest.Catalog{Apps: []*manifest.App{
		provenApp("proven", "preregistered", "dcr"),
		registered("unstated", "preregistered"),
		refuted,
		appWith("discover", discover),
		appWith("refuted-discover", refutedDiscover),
		appWith("keyed", key),
		appWith("open", open),
		appWith("local", local),
		other,
		registered("self", "dcr"),
	}}
	got := ossIDs(t, emitTarget(t, TargetOSSRegistry, cat, testMeta))
	if want := []string{"proven", "keyed", "open", "local"}; !slices.Equal(got, want) {
		t.Fatalf("oss-registry.json lists %v, want %v", got, want)
	}
	notProven := manifest.ReasonOAuthPublicClientNotProven
	wantLeft := []OSSLeftOut{
		{ID: "unstated", Ref: "unstated/mcp@1.0.0", Registration: []string{"preregistered"}, PublicClient: "not stated", Reason: notProven},
		{ID: "refuted", Ref: "refuted/mcp@1.0.0", Registration: []string{"preregistered", "dcr"}, PublicClient: "no", Reason: notProven},
		{ID: "discover", Ref: "discover/mcp@1.0.0", PublicClient: "not stated", Reason: notProven},
		{ID: "refuted-discover", Ref: "refuted-discover/mcp@1.0.0", PublicClient: "no", Reason: notProven},
		{ID: "other-id", Ref: "other/mcp@1.0.0", Registration: []string{"preregistered"}, PublicClient: "not stated", Reason: notProven},
		{ID: "self", Ref: "self/mcp@1.0.0", Registration: []string{"dcr"}, PublicClient: "not stated", Reason: manifest.ReasonOAuthSelfRegistrationOnly},
	}
	left := OSSLeftOutServers(cat)
	if !slices.EqualFunc(left, wantLeft, func(a, b OSSLeftOut) bool {
		return a.ID == b.ID && a.Ref == b.Ref && slices.Equal(a.Registration, b.Registration) && a.PublicClient == b.PublicClient && a.Reason == b.Reason
	}) {
		t.Fatalf("OSSLeftOutServers =\n%+v\nwant\n%+v", left, wantLeft)
	}
	if manifest.OSSLeftOutReasons[notProven] == "" {
		t.Fatal("the rule has no explanation for the build's report")
	}
	for _, s := range cat.Apps {
		for _, p := range s.Providers {
			wantListed := slices.Contains(got, s.ID)
			if listed := manifest.OSSLeftOutReason(p) == ""; listed != wantListed {
				t.Errorf("%s/%s: OSSLeftOutReason %q disagrees with the emitted registry", s.ID, p.ID, manifest.OSSLeftOutReason(p))
			}
		}
	}

	// The fact moves the server, and nothing else: proving a public client
	// lists it under the id it kept while left out.
	proven(cat.Apps[1].Providers[0])
	proven(other.Providers[0])
	got = ossIDs(t, emitTarget(t, TargetOSSRegistry, cat, testMeta))
	if want := []string{"proven", "unstated", "keyed", "open", "local", "other-id"}; !slices.Equal(got, want) {
		t.Fatalf("with unstated's and other's public clients proven, oss-registry.json lists %v, want %v", got, want)
	}
}
