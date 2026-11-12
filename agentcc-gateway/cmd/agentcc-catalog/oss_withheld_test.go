package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/emit"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// repoCatalog is the repository's own catalog directory.
var repoCatalog = filepath.Join("..", "..", "catalog", "connectors")

// ossRegistryIDs reads oss-registry.json's server ids, each with its auth.
func ossRegistryIDs(t *testing.T, data []byte) map[string]string {
	t.Helper()
	var doc struct {
		Servers []struct{ ID, Auth string } `json:"servers"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for _, s := range doc.Servers {
		out[s.ID] = s.Auth
	}
	return out
}

// selfRegistrationOnly is the self-registration rule stated from the record alone, as
// the tests check it: an oauth2 provider whose authorization server takes
// only clients that register themselves (dcr or cimd), with no
// pre-registered client.
func selfRegistrationOnly(p *manifest.Provider) bool {
	o := p.Auth.OAuth2
	return p.Auth.Kind == manifest.AuthOAuth2 && o != nil && len(o.Registration) > 0 && !slices.Contains(o.Registration, "preregistered")
}

// A server whose OAuth sign-in needs a client that registers itself
// (dynamic client registration, or a client ID metadata document) is left
// out of oss-registry.json, because OSS Omega's `omega mcp login` signs in
// only with a client id registered beforehand; so is one
// whose record does not prove a public client, a client with no secret,
// because omega mcp login sends none. build
// names each one, with its rule and what its record says of a public
// client, on its output and in dist/README.md; the bundle and what EE
// receives are unchanged, and check passes on the result.
func TestBuildReportsServersLeftOutOfOSS(t *testing.T) {
	const linear = "apps/linear/providers/linear-mcp.yaml"
	const rule = "oauth_client_self_registration_only"
	const notProven = "oauth_public_client_not_proven"

	// The example catalog: Slack's server states no public client (its MCP
	// guide asks for a client secret); GitHub's remote and Linear's state
	// nothing. All three leave; the API-key and stdio servers stay.
	plain := newCopy(t)
	out := plain.build(t)
	for _, want := range []string{
		"oss-registry.json leaves out github-remote (github/remote@1.0.0, registration preregistered; public client not stated): " + notProven + ": ",
		"oss-registry.json leaves out linear (linear/linear-mcp@1.0.0, registration preregistered, dcr; public client not stated): " + notProven + ": ",
		"oss-registry.json leaves out slack (slack/slack-mcp@1.0.0, registration preregistered; public client no): " + notProven + ": ",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("build output does not name the server, the fact and the rule:\n%s\nwant a line containing %q", out, want)
		}
	}
	readme, err := os.ReadFile(filepath.Join(plain.catalog, distReadmePath))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"## Servers left out of `emit/oss-registry.json`",
		"| OSS id | Provider | Registration | Public client | Rule |",
		"| `slack` | `slack/slack-mcp@1.0.0` | preregistered | no | `" + notProven + "` |",
		"| `linear` | `linear/linear-mcp@1.0.0` | preregistered, dcr | not stated | `" + notProven + "` |",
		"- `" + notProven + "`: ",
		"- `" + rule + "`: ",
	} {
		if !strings.Contains(string(readme), want) {
			t.Errorf("dist/README.md does not hold %q:\n%s", want, readme)
		}
	}
	plainSnap := plain.snapshot(t)
	ids := ossRegistryIDs(t, []byte(plainSnap[emitDir+"/oss-registry.json"]))
	for _, id := range []string{"github-remote", "linear", "slack"} {
		if _, ok := ids[id]; ok {
			t.Errorf("oss-registry.json lists %s, whose record proves no public client", id)
		}
	}
	for _, id := range []string{"github", "stripe", "close"} {
		if _, ok := ids[id]; !ok {
			t.Errorf("oss-registry.json lost %s, which signs in without OAuth", id)
		}
	}

	// A self-registering sign-in is named by its own rule.
	c := newCopy(t)
	c.edit(t, linear, "registration: [preregistered, dcr]\n", "registration: [cimd, dcr]\n")
	out = c.build(t)
	if want := "oss-registry.json leaves out linear (linear/linear-mcp@1.0.0, registration cimd, dcr; public client not stated): " + rule; !strings.Contains(out, want) {
		t.Errorf("build output does not name linear's server and the rule:\n%s\nwant a line containing %q", out, want)
	}
	readme, err = os.ReadFile(filepath.Join(c.catalog, distReadmePath))
	if err != nil {
		t.Fatal(err)
	}
	if want := "| `linear` | `linear/linear-mcp@1.0.0` | cimd, dcr | not stated | `" + rule + "` |"; !strings.Contains(string(readme), want) {
		t.Errorf("dist/README.md does not list linear's server:\n%s\nwant a row %q", readme, want)
	}
	// The rules are the OSS emitter's alone: the bundle (and so Django's
	// copy) still holds the provider, and EE still receives the app.
	snap := c.snapshot(t)
	if !strings.Contains(snap[bundlePath], `"id": "linear-mcp"`) {
		t.Error("dist/catalog.json lost linear-mcp")
	}
	if !strings.Contains(snap[emitDir+"/ee-apps.json"], `"id": "linear"`) {
		t.Error("ee-apps.json lost linear")
	}
	if !strings.Contains(snap[emitDir+"/ee-settings.json"], `"id": "linear"`) {
		t.Error("ee-settings.json lost linear")
	}
	c.run(t, 0, "check", "--django-copy", c.django)
	c.run(t, 0, "check", "--sources-only")
}

// linearPublicClient is an x-omega.publicClient block for the example
// catalog's Linear provider (the example's facts are illustrative).
func linearPublicClient(supported string) string {
	return "x-omega:\n  publicClient:\n    supported: " + supported + "\n    evidence:\n" +
		"      - url: https://linear.app/developers/oauth-2-0-authentication\n        fetchedAt: 2026-10-22\n" +
		"        sha256: " + strings.Repeat("ab", 32) + "\n        quote: \"Illustrative: no client secret with PKCE.\"\n"
}

// A server is listed when, and only when, its record proves a public
// client: stating one lists it, and stating none leaves it out again, by
// the rule, with the fact in the report.
func TestBuildListsAServerOnlyWithAProvenPublicClient(t *testing.T) {
	const linear = "apps/linear/providers/linear-mcp.yaml"
	c := newCopy(t)
	c.edit(t, linear, "x-gateway: { timeoutMs: 30000 }\n", "x-gateway: { timeoutMs: 30000 }\n"+linearPublicClient("true"))
	out := c.build(t)
	if strings.Contains(out, "leaves out linear ") {
		t.Errorf("build leaves out linear, whose record proves a public client:\n%s", out)
	}
	ids := ossRegistryIDs(t, []byte(c.snapshot(t)[emitDir+"/oss-registry.json"]))
	if ids["linear"] != "oauth" {
		t.Errorf("oss-registry.json does not list linear as an OAuth server: %v", ids)
	}
	c.run(t, 0, "check", "--django-copy", c.django)

	// Withdrawing a listed server is a major change (L1): the build refuses
	// the record until its version says so.
	c.edit(t, linear, "    supported: true\n", "    supported: false\n")
	if out := c.run(t, 1, "build", "--date", "2026-10-21", "--source-commit", testCommit); !strings.Contains(out,
		"linear/linear-mcp: needs a major version bump (1.0.0 -> 1.0.0): OSS no longer receives server linear (provider linear-mcp): oauth_public_client_not_proven") {
		t.Errorf("build output:\n%s", out)
	}
	c.edit(t, linear, "\nversion: 1.0.0\n", "\nversion: 2.0.0\n")
	out = c.build(t)
	if want := "oss-registry.json leaves out linear (linear/linear-mcp@2.0.0, registration preregistered, dcr; public client no): oauth_public_client_not_proven: "; !strings.Contains(out, want) {
		t.Errorf("build output:\n%s\nwant a line containing %q", out, want)
	}
	if _, ok := ossRegistryIDs(t, []byte(c.snapshot(t)[emitDir+"/oss-registry.json"]))["linear"]; ok {
		t.Error("oss-registry.json still lists linear, whose record states no public client")
	}
	c.run(t, 0, "check", "--django-copy", c.django)
}

// ossSignInProven is the public-client rule stated from the record alone, as the
// tests check it: OSS can sign in to a server that is not OAuth, or to an
// OAuth server whose record proves a public client a person registers
// beforehand.
func ossSignInProven(p *manifest.Provider) bool {
	if p.Auth.Kind != manifest.AuthOAuth2 && p.Auth.Kind != manifest.AuthMCPOAuth {
		return true
	}
	return !selfRegistrationOnly(p) && p.XOmega != nil && p.XOmega.PublicClient != nil && p.XOmega.PublicClient.Supported
}

// The repository's OSS registry lists no OAuth server whose sign-in needs a
// self-registering client, nor one whose record does not prove a
// public client: each one the catalog would offer OSS is
// left out, by the build rule, not by hand; every other server is listed.
func TestRepoOSSRegistryListsNoSelfRegistrationOnlyServer(t *testing.T) {
	cat, err := sources(os.DirFS(repoCatalog))
	if err != nil {
		t.Fatal(err)
	}
	files, err := emit.Target(emit.TargetOSSRegistry, cat, sourcesOnlyMeta)
	if err != nil {
		t.Fatal(err)
	}
	listed := ossRegistryIDs(t, files["oss-registry.json"])
	var leftOut, kept []string
	for _, a := range cat.Apps {
		for _, s := range manifest.EligibleForOSS(a) {
			_, isListed := listed[s.ID]
			switch ok := ossSignInProven(s.Provider); {
			case !ok && isListed:
				t.Errorf("oss-registry.json lists %s (%s/%s, auth %s): its record proves no sign-in omega mcp login can do",
					s.ID, a.ID, s.Provider.ID, s.Provider.Auth.Kind)
			case !ok:
				leftOut = append(leftOut, s.ID)
			case !isListed:
				t.Errorf("oss-registry.json leaves out %s (%s/%s), which the rules do not cover", s.ID, a.ID, s.Provider.ID)
			default:
				kept = append(kept, s.ID)
			}
		}
	}
	t.Logf("listed in oss-registry.json: %s", strings.Join(kept, ", "))
	t.Logf("left out of oss-registry.json: %s", strings.Join(leftOut, ", "))
}

// Every public client the repository's records state as proven cites the
// vendor's own pages, each saved with its sha256 and the sentence that
// proves it, and every OAuth server OSS lists has one.
func TestRepoPublicClientsCiteTheVendor(t *testing.T) {
	cat, err := sources(os.DirFS(repoCatalog))
	if err != nil {
		t.Fatal(err)
	}
	stated := 0
	for _, a := range cat.Apps {
		for _, p := range a.Providers {
			if p.XOmega == nil || p.XOmega.PublicClient == nil {
				continue
			}
			stated++
			pc := p.XOmega.PublicClient
			for i, e := range pc.Evidence {
				if len(e.SHA256) != 64 || e.Quote == "" || e.FetchedAt == "" {
					t.Errorf("%s/%s: evidence %d is not a saved page with its sha256 and quote: %+v", a.ID, p.ID, i, e)
				}
				if pc.Supported && !manifest.VendorOwned(a, e.URL) {
					t.Errorf("%s/%s: a public client is proven by %s, which is not the vendor's", a.ID, p.ID, e.URL)
				}
			}
		}
	}
	if stated == 0 {
		t.Fatal("no record states whether a public client exists")
	}
}

// EE needs no such rule today: every provider EE receives has a
// pre-registered client, the client id and secret EE reads from its
// environment (OMEGA_INTEGRATION_<PREFIX>_CLIENT_ID and _SECRET). A record
// that breaks this needs x-omega.ee: exclude, or EE's own rule.
func TestRepoEEAppsHaveAPreregisteredClient(t *testing.T) {
	cat, err := sources(os.DirFS(repoCatalog))
	if err != nil {
		t.Fatal(err)
	}
	n := 0
	for _, a := range cat.Apps {
		p, ok := manifest.EligibleForEE(a)
		if !ok {
			continue
		}
		n++
		if selfRegistrationOnly(p) {
			t.Errorf("EE receives %s through %s, whose OAuth sign-in takes only self-registering clients (registration %v)",
				a.ID, p.ID, p.Auth.OAuth2.Registration)
		}
	}
	if n == 0 {
		t.Fatal("EE receives no app from the repository's catalog")
	}
}
