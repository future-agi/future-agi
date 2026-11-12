package bundle

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"testing/fstest"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// fixtureRoot is the complete valid example catalog.
const fixtureRoot = "../manifest/testdata/valid"

const testCommit = "0123456789abcdef0123456789abcdef01234567"

var testDate = time.Date(2026, 10, 20, 12, 0, 0, 0, time.UTC)

func fixture(t *testing.T) fstest.MapFS {
	t.Helper()
	fsys := fstest.MapFS{}
	err := filepath.WalkDir(fixtureRoot, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		data, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(fixtureRoot, p)
		fsys[filepath.ToSlash(rel)] = &fstest.MapFile{Data: data, Mode: 0o644}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return fsys
}

func edit(t *testing.T, fsys fstest.MapFS, path, old, new string) {
	t.Helper()
	f := fsys[path]
	if f == nil || !strings.Contains(string(f.Data), old) {
		t.Fatalf("%s does not hold %q", path, old)
	}
	f.Data = []byte(strings.Replace(string(f.Data), old, new, 1))
}

func load(t *testing.T, fsys fs.FS) *manifest.Catalog {
	t.Helper()
	cat, err := manifest.LoadDir(fsys)
	if err != nil {
		t.Fatal(err)
	}
	return cat
}

func build(t *testing.T, fsys fs.FS, prev *Bundle) *Bundle {
	t.Helper()
	b, err := Build(fsys, load(t, fsys), prev, testDate, func() (string, error) { return testCommit, nil })
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// asBundled is a source provider as the bundle states it: generated tools
// after the curated ones, no tools.generated.
func asBundled(p *manifest.Provider) manifest.Provider {
	c := *p
	c.Tools.Items = p.AllTools()
	c.Tools.Generated = ""
	c.GeneratedTools = nil
	return c
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// The bundle carries every record exactly: read back through the loader,
// each app, provider and platform equals its source, a rest provider's
// generated tools folded into tools.items.
func TestBundleRoundTrip(t *testing.T) {
	fsys := fixture(t)
	src := load(t, fsys)
	b := build(t, fsys, nil)
	data, err := b.Encode()
	if err != nil {
		t.Fatal(err)
	}
	back, err := Decode(data)
	if err != nil {
		t.Fatal(err)
	}
	if back.SHA256 != b.SHA256 || back.Version != "2026.10.20.0" || back.SourceCommit != testCommit {
		t.Fatalf("decoded %s %s %s", back.Version, back.SHA256, back.SourceCommit)
	}
	cat, err := back.Catalog()
	if err != nil {
		t.Fatal(err)
	}
	if len(cat.Apps) != len(src.Apps) || len(cat.Platforms) != len(src.Platforms) {
		t.Fatalf("%d apps, %d platforms; want %d, %d", len(cat.Apps), len(cat.Platforms), len(src.Apps), len(src.Platforms))
	}
	folded := 0
	for i, a := range src.Apps {
		got := cat.Apps[i]
		if mustJSON(t, got) != mustJSON(t, a) {
			t.Errorf("app %s:\n got %s\nwant %s", a.ID, mustJSON(t, got), mustJSON(t, a))
		}
		for j, p := range a.Providers {
			want := asBundled(p)
			if p.Tools.Generated != "" {
				folded++
			}
			if g := mustJSON(t, *got.Providers[j]); g != mustJSON(t, want) {
				t.Errorf("provider %s/%s:\n got %s\nwant %s", a.ID, p.ID, g, mustJSON(t, want))
			}
		}
	}
	for i, pl := range src.Platforms {
		if mustJSON(t, cat.Platforms[i]) != mustJSON(t, pl) {
			t.Errorf("platform %s differs", pl.ID)
		}
	}
	if folded == 0 {
		t.Fatal("the fixture has no rest provider with generated tools; the fold is untested")
	}
}

func TestDecodeIsStrict(t *testing.T) {
	data, err := build(t, fixture(t), nil).Encode()
	if err != nil {
		t.Fatal(err)
	}
	good := string(data)
	for name, mutate := range map[string]func(string) string{
		"an extra member": func(s string) string {
			return strings.Replace(s, `"schema": "connector-catalog-bundle/v1",`, `"schema": "connector-catalog-bundle/v1", "price": 1,`, 1)
		},
		"another schema": func(s string) string {
			return strings.Replace(s, `"connector-catalog-bundle/v1"`, `"connector-catalog-bundle/v2"`, 1)
		},
		"a record changed under the same sha256": func(s string) string {
			return strings.Replace(s, `"name": "Close"`, `"name": "Closed"`, 1)
		},
		"a date that does not exist": func(s string) string {
			return strings.Replace(s, `"version": "2026.10.20.0"`, `"version": "2026.02.30.0"`, 1)
		},
		"a short source commit": func(s string) string {
			return strings.Replace(s, testCommit, "0123456", 1)
		},
		"a missing member": func(s string) string {
			return strings.Replace(s, `"platforms": [`, `"platformz": [`, 1)
		},
		"a duplicate member": func(s string) string {
			return strings.Replace(s, `"schema": "connector-catalog-bundle/v1",`, `"schema": "connector-catalog-bundle/v1", "schema": "connector-catalog-bundle/v1",`, 1)
		},
	} {
		bad := mutate(good)
		if bad == good {
			t.Fatalf("%s: the mutation changed nothing", name)
		}
		if _, err := Decode([]byte(bad)); err == nil {
			t.Errorf("%s: Decode accepted it", name)
		}
	}
	if _, err := Decode(data); err != nil {
		t.Fatalf("the unmutated bundle: %v", err)
	}
}

func TestNextVersion(t *testing.T) {
	for _, c := range []struct{ last, want, err string }{
		{"", "2026.10.20.0", ""},
		{"2026.10.19.4", "2026.10.20.0", ""},
		{"2026.10.20.0", "2026.10.20.1", ""},
		{"2026.10.20.41", "2026.10.20.42", ""},
		{"2026.10.21.0", "", "never go backwards"},
		{"2026.10.20.9999", "", "last of its day"},
		{"2026-10-20", "", "not YYYY.MM.DD.N"},
	} {
		got, err := NextVersion(c.last, testDate)
		if c.err != "" {
			if err == nil || !strings.Contains(err.Error(), c.err) {
				t.Errorf("after %q: got %q, %v; want an error with %q", c.last, got, err, c.err)
			}
			continue
		}
		if err != nil || got != c.want {
			t.Errorf("after %q: got %q, %v; want %q", c.last, got, err, c.want)
		}
	}
}

// Unchanged records keep the version and source commit, and ask for no
// commit; changed ones take the next version.
func TestBuildKeepsVersionWhenUnchanged(t *testing.T) {
	fsys := fixture(t)
	first := build(t, fsys, nil)
	noGit := func() (string, error) { return "", errors.New("git must not be asked") }
	again, err := Build(fsys, load(t, fsys), first, testDate.AddDate(0, 0, 3), noGit)
	if err != nil {
		t.Fatal(err)
	}
	if again.Version != first.Version || again.SourceCommit != first.SourceCommit || again.SHA256 != first.SHA256 {
		t.Errorf("an unchanged rebuild moved: %s %s", again.Version, again.SourceCommit)
	}
	edit(t, fsys, "apps/close/app.yaml", "version: 1.0.0", "version: 1.0.1")
	edit(t, fsys, "apps/close/app.yaml", "name: Close\n", "name: Close CRM\n")
	next := build(t, fsys, first)
	if next.Version != "2026.10.20.1" || next.SHA256 == first.SHA256 {
		t.Errorf("a changed build: version %s, sha256 %s", next.Version, next.SHA256)
	}
}

// L1's semver rule against the last bundle: a change needs its bump.
func TestCheckBumps(t *testing.T) {
	fsys := fixture(t)
	prev, err := build(t, fsys, nil).Catalog()
	if err != nil {
		t.Fatal(err)
	}
	catalogOf := func(fsys fs.FS) *manifest.Catalog {
		b := build(t, fsys, nil)
		c, err := b.Catalog()
		if err != nil {
			t.Fatal(err)
		}
		return c
	}
	if err := CheckBumps(prev, catalogOf(fsys)); err != nil {
		t.Fatalf("unchanged records: %v", err)
	}
	edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "defaultPosture: ask", "defaultPosture: deny")
	err = CheckBumps(prev, catalogOf(fsys))
	if err == nil || !strings.Contains(err.Error(), "needs a minor version bump") {
		t.Fatalf("a posture change without a bump: %v", err)
	}
	edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "version: 1.0.0", "version: 1.1.0")
	if err := CheckBumps(prev, catalogOf(fsys)); err != nil {
		t.Fatalf("with the bump: %v", err)
	}
}
