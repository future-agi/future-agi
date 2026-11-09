package emit

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

func restApp(description string) *manifest.Catalog {
	a := appWith("mail", oauthProvider("mail", "futureagi-rest", manifest.KindREST, "https://{connectorsHost}/mail/mcp"))
	a.Description = description
	return &manifest.Catalog{Apps: []*manifest.App{a}}
}

// TestServerJSONLeavesOutWhatNoRegistryClientCanSignIn holds IR-CN14c-6:
// while agentcc-connectors serves no protected-resource metadata, the build
// writes no server.json for a rest provider, reports each one left out, and
// still checks it (a malformed record is refused, left out or not).
func TestServerJSONLeavesOutWhatNoRegistryClientCanSignIn(t *testing.T) {
	if RuntimeServesProtectedResourceMetadata {
		t.Skip("the runtime serves protected-resource metadata: every rest provider is written")
	}
	cat, meta := fixture(t)
	if files := emitTarget(t, TargetServerJSON, cat, meta); len(files) != 0 {
		t.Errorf("server.json files %v; want none while the runtime serves no protected-resource metadata", files.Paths())
	}
	left := ServerJSONLeftOutServers(cat)
	if len(left) != 1 || left[0].App != "gmail" || left[0].Name != "com.futureagi/gmail" ||
		!strings.HasPrefix(left[0].Ref, "gmail/futureagi-rest@") || left[0].Reason != ReasonNoProtectedResourceMetadata {
		t.Errorf("left out %+v; want gmail's rest provider, %s", left, ReasonNoProtectedResourceMetadata)
	}
	if ServerJSONLeftOutReasons[ReasonNoProtectedResourceMetadata] == "" {
		t.Error("the reason has no explanation for the build's report")
	}
	// Left out, but still checked.
	_, err := Target(TargetServerJSON, restApp(strings.Repeat("d", 101)), testMeta)
	wantError(t, err, "description of 1 to 100 characters")
	// The rule leaves out rest providers only: no other kind is ever written.
	for _, a := range cat.Apps {
		for _, p := range a.Providers {
			if got := serverJSONLeftOutReason(p, false); (got != "") != (p.Kind == manifest.KindREST) {
				t.Errorf("%s/%s (%s): reason %q", a.ID, p.ID, p.Kind, got)
			}
			if got := serverJSONLeftOutReason(p, true); got != "" {
				t.Errorf("%s/%s with metadata served: reason %q, want none", a.ID, p.ID, got)
			}
		}
	}
}

func TestServerJSONDescriptionMax100(t *testing.T) {
	cat, meta := fixture(t)
	// What the build writes once the runtime serves protected-resource
	// metadata (the rule above lifted).
	files, err := serverJSONFiles(cat, meta, true)
	if err != nil {
		t.Fatal(err)
	}
	// Only servers FutureAGI hosts: gmail's rest provider.
	if paths := files.Paths(); len(paths) != 1 || paths[0] != "server-json/gmail.json" {
		t.Fatalf("server.json files %v", paths)
	}
	if want, err := os.ReadFile(filepath.Join("testdata", "server-json-with-prm", "gmail.json")); err != nil ||
		string(want) != string(files["server-json/gmail.json"]) {
		t.Errorf("server-json/gmail.json differs from testdata/server-json-with-prm/gmail.json (%v)", err)
	}
	doc := parse(t, files["server-json/gmail.json"])
	for key, want := range map[string]any{
		"$schema": ServerJSONSchemaURL, "name": "com.futureagi/gmail", "title": "Gmail", "version": "1.0.0",
	} {
		if got := member(doc, key); got != want {
			t.Errorf("%s: %v, want %v", key, got, want)
		}
	}
	if d, _ := member(doc, "description").(string); d == "" || utf8.RuneCountInString(d) > 100 {
		t.Errorf("description %q", d)
	}
	remote := list(t, doc, "remotes")[0]
	if member(remote, "type") != "streamable-http" || member(remote, "url") != "https://{connectorsHost}/gmail/mcp" {
		t.Errorf("remote %v", remote)
	}
	host, _ := member(member(remote, "variables").(canonjson.Object), "connectorsHost").(canonjson.Object)
	if member(host, "isRequired") != true {
		t.Errorf("{connectorsHost} is not a required variable: %v", host)
	}
	// The _meta member is com.futureagi/connector, the namespace of
	// RegistryNamespace (AM-C22, for consistency with AM-C21).
	meta2, _ := member(member(doc, "_meta").(canonjson.Object), "com.futureagi/connector").(canonjson.Object)
	if !equalKeys(meta2, "kind", "provider", "tier") || member(meta2, "kind") != "rest" || member(meta2, "provider") != "futureagi-rest" {
		t.Errorf("_meta %v", meta2)
	}

	// The 100-character rule is the emitter's own, counted in characters.
	if _, err := Target(TargetServerJSON, restApp(strings.Repeat("d", 100)), testMeta); err != nil {
		t.Errorf("100 characters: %v", err)
	}
	if _, err := Target(TargetServerJSON, restApp(strings.Repeat("é", 100)), testMeta); err != nil {
		t.Errorf("100 two-byte characters: %v", err)
	}
	_, err = Target(TargetServerJSON, restApp(strings.Repeat("d", 101)), testMeta)
	wantError(t, err, "description of 1 to 100 characters")
	_, err = Target(TargetServerJSON, restApp(""), testMeta)
	wantError(t, err, "description of 1 to 100 characters")

	// One K2 server per app: two rest providers would serve one path.
	two := restApp("Mail.")
	second := oauthProvider("mail", "second-rest", manifest.KindREST, "https://{connectorsHost}/mail/mcp")
	two.Apps[0].Providers = append(two.Apps[0].Providers, second)
	_, err = Target(TargetServerJSON, two, testMeta)
	wantError(t, err, "serves one per app")
}

func equalKeys(o canonjson.Object, want ...string) bool {
	got := keys(o)
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}
