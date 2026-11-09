package restmcp

import (
	"errors"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// attackerOrigin is outside every app's vendor.domains (CN4 review P2's
// repro).
const attackerOrigin = "https://attacker.example"

// TestCompileNamesEveryRefusedTool: CN4 review P2's repro. Every Gmail
// tool's x-origin is moved to a host outside the app's vendor.domains, in a
// catalog that also holds a good app (Slack). The catalog check does not
// look at x-origin, so the runtime is the last line: Compile refuses every
// such tool, each refusal naming its app, provider and tool, and serves
// nothing, the good app included.
func TestCompileNamesEveryRefusedTool(t *testing.T) {
	gmail, p := gmailCopy(t)
	var names []string
	for i := range p.Tools.Items {
		editSchema(t, &p.Tools.Items[i], func(m map[string]any) { m["x-origin"] = attackerOrigin })
		names = append(names, p.Tools.Items[i].Name)
	}
	slack := cn4Catalog(t).App("slack")
	if slack == nil {
		t.Fatal("no slack app in CN4's example catalog")
	}
	apps, err := Compile(&manifest.Catalog{Apps: []*manifest.App{gmail, slack}})
	if err == nil {
		t.Fatalf("Compile served %d apps with every Gmail tool sent to %s", len(apps), attackerOrigin)
	}
	if apps != nil {
		t.Errorf("Compile returned %d apps with an error; a catalog is served whole or not at all", len(apps))
	}
	var ce *CompileError
	if !errors.As(err, &ce) {
		t.Fatalf("Compile error is a %T, not a *CompileError listing each refusal: %v", err, err)
	}
	var refused []string
	for _, r := range ce.Refusals {
		if r.App != "gmail" || r.Provider != "futureagi-rest" {
			t.Errorf("refusal %+v names another app or provider; only Gmail's tools are outside its domains", r)
		}
		if !strings.Contains(r.Reason, "attacker.example is not under the app's vendor.domains") {
			t.Errorf("refusal of %s says %q, not that its host is outside vendor.domains", r.Tool, r.Reason)
		}
		refused = append(refused, r.Tool)
	}
	sort.Strings(names)
	sort.Strings(refused)
	if !reflect.DeepEqual(refused, names) {
		t.Errorf("refused tools %v, want every Gmail tool %v", refused, names)
	}
	lines := strings.Split(err.Error(), "\n")
	if len(lines) != len(names) {
		t.Errorf("the error has %d lines for %d refused tools:\n%s", len(lines), len(names), err)
	}
	for _, line := range lines {
		if !strings.HasPrefix(line, "gmail/futureagi-rest: tool ") {
			t.Errorf("an error line does not name the app, the provider and the tool: %q", line)
		}
	}
}

// TestCompileRefusesAPathThatLeavesTheOrigin: the request URL is x-origin
// followed by operation.path, so a path that does not start with "/" would
// move the request (and the user's token) to another host: with
// "@attacker.example/x" the URL's host is attacker.example. The manifest
// schema requires the "/", and the runtime holds the rule itself.
func TestCompileRefusesAPathThatLeavesTheOrigin(t *testing.T) {
	for _, path := range []string{"@attacker.example/gmail/v1/users/me/profile", ".attacker.example/x", ":8443/x", ""} {
		app, p := gmailCopy(t)
		toolNamed(t, p, "get_profile").Operation.Path = path
		_, err := Compile(&manifest.Catalog{Apps: []*manifest.App{app}})
		if err == nil || !strings.Contains(err.Error(), "does not start with /") {
			t.Errorf("operation.path %q: Compile error = %v, want a refusal", path, err)
		}
	}
}

// TestRefusedOriginNeverLogsUserInfo: a refused x-origin is described by
// what is wrong with it, never echoed whole, so user info a spec's server
// carried (CN4 review P3: https://u:p@host) does not reach the log.
func TestRefusedOriginNeverLogsUserInfo(t *testing.T) {
	const secret = "cn10-fake-userinfo-s3cret"
	for _, origin := range []string{
		"https://user:" + secret + "@gmail.googleapis.com",
		"http://user:" + secret + "@gmail.googleapis.com",
		"https://user:" + secret + "@attacker.example",
		"https://gmail.googleapis.com/?k=" + secret,
		"https://gmail.googleapis.com/#" + secret,
		"https://gmail.googleapis.com/" + secret,
		"ht\ttps://" + secret,
	} {
		app, p := gmailCopy(t)
		editSchema(t, toolNamed(t, p, "get_profile"), func(m map[string]any) { m["x-origin"] = origin })
		_, err := Compile(&manifest.Catalog{Apps: []*manifest.App{app}})
		if err == nil {
			t.Errorf("x-origin %q was accepted", origin)
			continue
		}
		if strings.Contains(err.Error(), secret) {
			t.Errorf("the refusal of x-origin %q echoes it: %v", origin, err)
		}
	}
}

// TestUpstreamHasNoOriginFieldYet is a tripwire for IR-CN4-1. The frozen
// manifest has no field for a rest provider's API origin, so the runtime
// reads the generator's x-origin and checks it against vendor.domains
// (vendorOrigin). When the schema gains one (upstream.server, say), this
// test fails until compileTool checks that field against vendor.domains the
// same way and refuses a tool whose origin is outside them.
func TestUpstreamHasNoOriginFieldYet(t *testing.T) {
	known := map[reflect.Type][]string{
		reflect.TypeOf(manifest.Upstream{}):  {"Registry", "Spec"},
		reflect.TypeOf(manifest.Operation{}): {"Method", "Path"},
	}
	for typ, want := range known {
		var got []string
		for i := 0; i < typ.NumField(); i++ {
			got = append(got, typ.Field(i).Name)
		}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("manifest.%s has fields %v, not %v: if one names where a rest tool's request goes, make "+
				"compileTool check it against vendor.domains (as vendorOrigin checks x-origin), then update this list",
				typ.Name(), got, want)
		}
	}
}
