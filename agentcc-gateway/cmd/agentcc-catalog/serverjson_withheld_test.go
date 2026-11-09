package main

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/emit"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// emit/server-json is the set OA-DIR would publish to the MCP Registry. While
// agentcc-connectors serves no OAuth protected-resource metadata, a registry
// client cannot sign in to a rest provider, so build writes no server.json
// for one (IR-CN14c-6, IR-CN15c-5); it names each one left out, with the
// rule, on its output and in dist/README.md, and check passes on the result.
func TestBuildReportsServersLeftOutOfServerJSON(t *testing.T) {
	if emit.RuntimeServesProtectedResourceMetadata {
		t.Skip("the runtime serves protected-resource metadata: server-json leaves out no rest provider")
	}
	const rule = "runtime_serves_no_protected_resource_metadata"
	c := newCopy(t)
	out := c.build(t)
	if want := "server-json leaves out com.futureagi/gmail (gmail/futureagi-rest@"; !strings.Contains(out, want) ||
		!strings.Contains(out, "): "+rule+": ") {
		t.Errorf("build output does not name gmail's rest provider and the rule:\n%s", out)
	}
	if _, err := os.Stat(filepath.Join(c.catalog, emitDir, "server-json")); !os.IsNotExist(err) {
		t.Errorf("build wrote dist/emit/server-json (%v); want none while the runtime serves no protected-resource metadata", err)
	}
	readme, err := os.ReadFile(filepath.Join(c.catalog, distReadmePath))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"## Servers left out of `emit/server-json`",
		"- `" + rule + "`: ",
		"| `com.futureagi/gmail` | `gmail/futureagi-rest@",
	} {
		if !strings.Contains(string(readme), want) {
			t.Errorf("dist/README.md has no %q:\n%s", want, readme)
		}
	}
	// The rule is server-json's alone: the bundle still holds the provider,
	// and the gateway's own entries still offer it.
	snap := c.snapshot(t)
	if !strings.Contains(snap[bundlePath], `"kind": "rest"`) {
		t.Error("dist/catalog.json lost the rest provider")
	}
	c.run(t, 0, "check", "--django-copy", c.django)
	c.run(t, 0, "check", "--sources-only")
}

// The repository's catalog publishes nothing a registry client cannot sign
// in to: the server-json emitter writes no server.json for any of its rest
// providers, including catalog additions, and the build's report names each
// provider exactly once with the correct reason.
func TestRepoServerJSONPublishesNoRestProviderWithoutMetadata(t *testing.T) {
	if emit.RuntimeServesProtectedResourceMetadata {
		t.Skip("the runtime serves protected-resource metadata")
	}
	cat, err := sources(os.DirFS(repoCatalog))
	if err != nil {
		t.Fatal(err)
	}
	files, err := emit.Target(emit.TargetServerJSON, cat, sourcesOnlyMeta)
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 0 {
		t.Errorf("server-json writes %v; want none while the runtime serves no protected-resource metadata", files.Paths())
	}
	want := map[string]emit.ServerJSONLeftOut{}
	for _, app := range cat.Apps {
		for _, provider := range app.Providers {
			if provider.Kind != manifest.KindREST {
				continue
			}
			ref := app.ID + "/" + provider.ID + "@" + provider.Version
			want[ref] = emit.ServerJSONLeftOut{App: app.ID, Name: emit.RegistryNamespace + "/" + app.ID,
				Ref: ref, Reason: emit.ReasonNoProtectedResourceMetadata}
		}
	}
	if len(want) == 0 {
		t.Fatal("repository catalog has no REST providers to exercise the withholding rule")
	}
	left := map[string]emit.ServerJSONLeftOut{}
	for _, s := range emit.ServerJSONLeftOutServers(cat) {
		if _, duplicate := left[s.Ref]; duplicate {
			t.Errorf("provider %s is reported more than once", s.Ref)
		}
		left[s.Ref] = s
	}
	if !reflect.DeepEqual(left, want) {
		t.Errorf("left out of server-json: %v; want exactly the catalog's REST providers: %v", left, want)
	}
}
