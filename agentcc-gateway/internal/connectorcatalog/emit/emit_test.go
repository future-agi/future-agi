package emit

import (
	"errors"
	"flag"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

var update = flag.Bool("update", false, "rewrite testdata/golden from CN1's example catalog")

// fixtureRoot is CN1's complete valid example catalog; goldenRoot holds what
// every JSON target writes for it, the reference outputs that pin each
// format.
const (
	fixtureRoot = "../manifest/testdata/valid"
	goldenRoot  = "testdata/golden"
	testCommit  = "0123456789abcdef0123456789abcdef01234567"
)

// fixture is CN1's catalog in bundle form, with its bundle's version and
// sha256, as agentcc-catalog builds it on 2026-10-20.
func fixture(t *testing.T) (*manifest.Catalog, Meta) {
	t.Helper()
	cat, b := fixtureBundle(t)
	return cat, Meta{Version: b.Version, SHA256: b.SHA256}
}

func fixtureBundle(t *testing.T) (*manifest.Catalog, *bundle.Bundle) {
	t.Helper()
	fsys := os.DirFS(fixtureRoot)
	src, err := manifest.LoadDir(fsys)
	if err != nil {
		t.Fatal(err)
	}
	b, err := bundle.Build(fsys, src, nil, time.Date(2026, 10, 20, 0, 0, 0, 0, time.UTC), func() (string, error) { return testCommit, nil })
	if err != nil {
		t.Fatal(err)
	}
	cat, err := b.Catalog()
	if err != nil {
		t.Fatal(err)
	}
	return cat, b
}

func emitTarget(t *testing.T, name string, cat *manifest.Catalog, meta Meta) Files {
	t.Helper()
	files, err := Target(name, cat, meta)
	if err != nil {
		t.Fatal(err)
	}
	return files
}

func parse(t *testing.T, data []byte) canonjson.Object {
	t.Helper()
	v, err := canonjson.Parse(data)
	if err != nil {
		t.Fatal(err)
	}
	obj, ok := v.(canonjson.Object)
	if !ok {
		t.Fatalf("not an object: %s", data)
	}
	return obj
}

func member(o canonjson.Object, key string) any {
	v, _ := o.Get(key)
	return v
}

func list(t *testing.T, o canonjson.Object, key string) []canonjson.Object {
	t.Helper()
	raw, ok := member(o, key).([]any)
	if !ok {
		t.Fatalf("%s is not an array", key)
	}
	out := make([]canonjson.Object, len(raw))
	for i, e := range raw {
		if out[i], ok = e.(canonjson.Object); !ok {
			t.Fatalf("%s[%d] is not an object", key, i)
		}
	}
	return out
}

func keys(o canonjson.Object) []string {
	out := make([]string, len(o))
	for i, m := range o {
		out[i] = m.Key
	}
	return out
}

// TestGoldens pins the bundle (catalog.json) and every JSON output for
// CN1's example catalog: the reference outputs that show each format. The
// docs cards are Markdown, which the commit hook reformats, so
// TestDocsCards checks them instead.
func TestGoldens(t *testing.T) {
	cat, b := fixtureBundle(t)
	files, err := All(cat, Meta{Version: b.Version, SHA256: b.SHA256})
	if err != nil {
		t.Fatal(err)
	}
	if files["catalog.json"], err = b.Encode(); err != nil {
		t.Fatal(err)
	}
	var jsonPaths []string
	for _, p := range files.Paths() {
		if path.Ext(p) == ".json" {
			jsonPaths = append(jsonPaths, p)
		}
	}
	// catalog.json, gateway-servers, ee-apps, ee-settings and oss-registry;
	// no server-json while the runtime serves no protected-resource metadata
	// (IR-CN14c-6; TestServerJSONDescriptionMax100 holds its bytes).
	if len(jsonPaths) != 5 {
		t.Fatalf("only %d JSON outputs: %v", len(jsonPaths), jsonPaths)
	}
	for _, p := range jsonPaths {
		golden := filepath.Join(goldenRoot, filepath.FromSlash(p))
		if *update {
			if err := os.MkdirAll(filepath.Dir(golden), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(golden, files[p], 0o644); err != nil {
				t.Fatal(err)
			}
			continue
		}
		want, err := os.ReadFile(golden)
		if err != nil {
			t.Fatalf("%v (run go test ./internal/connectorcatalog/emit/ -run TestGoldens -update)", err)
		}
		if string(want) != string(files[p]) {
			t.Errorf("%s differs from %s", p, golden)
		}
	}
}

// Every output path is committable: never under a directory .gitignore
// hides (AM-C20), and, where git is present, not ignored inside dist/.
func TestOutputPathsAreCommittable(t *testing.T) {
	for _, bad := range []string{"build/x.json", "docs/media/a.md", "a/logs/b.json", "coverage/c.md", "../x.json", "/abs.json", "a//b.json"} {
		if checkPath(bad) == nil {
			t.Errorf("checkPath(%q) passed", bad)
		}
	}
	cat, meta := fixture(t)
	files, err := All(cat, meta)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	top, err := exec.Command("git", "rev-parse", "--show-toplevel").Output()
	if err != nil {
		t.Skip("not in a git work tree")
	}
	args := []string{"-C", strings.TrimSpace(string(top)), "check-ignore", "--no-index"}
	for _, p := range files.Paths() {
		args = append(args, "agentcc-gateway/catalog/connectors/dist/emit/"+p)
	}
	args = append(args, "agentcc-gateway/catalog/connectors/dist/catalog.json", "futureagi/agentcc/connector_catalog/catalog.json")
	out, err := exec.Command("git", args...).Output()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 1 { // 1: no path is ignored
		t.Fatalf("git check-ignore: %v\n%s", err, out)
	}
}
