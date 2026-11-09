package main

import (
	"bytes"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// fixtureRoot is CN1's complete valid example catalog.
const (
	fixtureRoot = "../../internal/connectorcatalog/manifest/testdata/valid"
	testCommit  = "0123456789abcdef0123456789abcdef01234567"
)

// catalogCopy copies CN1's example catalog into a new directory, and gives
// it a Django app beside it, as the repository has.
type catalogCopy struct {
	root    string // the copy's repository root
	catalog string // root/agentcc-gateway/catalog/connectors
	django  string // the Django copy's path
}

func newCopy(t *testing.T) *catalogCopy {
	t.Helper()
	root := t.TempDir()
	c := &catalogCopy{
		root:    root,
		catalog: filepath.Join(root, "agentcc-gateway", "catalog", "connectors"),
		django:  filepath.Join(root, "futureagi", "agentcc", "connector_catalog", "catalog.json"),
	}
	err := filepath.WalkDir(fixtureRoot, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(fixtureRoot, p)
		data, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		return writeFile(filepath.Join(c.catalog, rel), data)
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(c.django), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(filepath.Dir(c.django), djangoReadme), []byte("the Django copy\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return c
}

// cli runs the command and returns its exit code and output.
func cli(args ...string) (int, string) {
	var out, errb bytes.Buffer
	code := run(args, &out, &errb)
	return code, out.String() + errb.String()
}

func (c *catalogCopy) run(t *testing.T, want int, args ...string) string {
	t.Helper()
	args = append(args[:1:1], append([]string{"--catalog", c.catalog}, args[1:]...)...)
	code, out := cli(args...)
	if code != want {
		t.Fatalf("agentcc-catalog %s: exit %d, want %d:\n%s", strings.Join(args, " "), code, want, out)
	}
	return out
}

func (c *catalogCopy) build(t *testing.T, args ...string) string {
	t.Helper()
	return c.run(t, 0, append([]string{"build", "--date", "2026-10-20", "--source-commit", testCommit}, args...)...)
}

// snapshot reads every generated file: dist/** and the Django copy.
func (c *catalogCopy) snapshot(t *testing.T) map[string]string {
	t.Helper()
	paths, err := filesUnder(c.catalog, distDir)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for _, p := range paths {
		data, err := os.ReadFile(filepath.Join(c.catalog, p))
		if err != nil {
			t.Fatal(err)
		}
		out[p] = string(data)
	}
	data, err := os.ReadFile(c.django)
	if err != nil {
		t.Fatal(err)
	}
	out["django:catalog.json"] = string(data)
	return out
}

func (c *catalogCopy) edit(t *testing.T, rel, old, new string) {
	t.Helper()
	p := filepath.Join(c.catalog, rel)
	data, err := os.ReadFile(p)
	if err != nil || !strings.Contains(string(data), old) {
		t.Fatalf("%s does not hold %q (%v)", rel, old, err)
	}
	if err := os.WriteFile(p, []byte(strings.Replace(string(data), old, new, 1)), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestBuildDeterministic(t *testing.T) {
	a, b := newCopy(t), newCopy(t)
	a.build(t)
	b.build(t)
	first, second := a.snapshot(t), b.snapshot(t)
	if len(first) < 15 {
		t.Fatalf("only %d files: %v", len(first), first)
	}
	for p, data := range first {
		if second[p] != data {
			t.Errorf("%s differs between two builds of the same records", p)
		}
	}
	// The Django copy is the bundle, byte for byte.
	if first["django:catalog.json"] != first[bundlePath] {
		t.Error("the Django copy is not the bundle")
	}
	// The bundle and every JSON output equal the reference goldens.
	goldens := "../../internal/connectorcatalog/emit/testdata/golden"
	for p, data := range first {
		rel, ok := strings.CutPrefix(p, emitDir+"/")
		if p == bundlePath {
			rel, ok = "catalog.json", true
		}
		if !ok || filepath.Ext(rel) != ".json" {
			continue
		}
		want, err := os.ReadFile(filepath.Join(goldens, rel))
		if err != nil || string(want) != data {
			t.Errorf("%s differs from golden %s (%v)", p, rel, err)
		}
	}
	// A rebuild of unchanged records, with another date and no git (these
	// directories are not repositories), changes no byte.
	a.run(t, 0, "build", "--date", "2026-11-30")
	if again := a.snapshot(t); len(again) != len(first) {
		t.Fatalf("a rebuild wrote %d files, not %d", len(again), len(first))
	} else {
		for p, data := range first {
			if again[p] != data {
				t.Errorf("a rebuild changed %s", p)
			}
		}
	}
	// emit writes each target from the bundle, the same bytes build wrote.
	out := t.TempDir()
	for _, target := range []string{"gateway", "ee-apps", "ee-settings", "oss-registry", "server-json", "docs"} {
		a.run(t, 0, "emit", "--target", target, "--out", out)
	}
	for p, data := range first {
		rel, ok := strings.CutPrefix(p, emitDir+"/")
		if !ok {
			continue
		}
		got, err := os.ReadFile(filepath.Join(out, rel))
		if err != nil || string(got) != data {
			t.Errorf("emit wrote %s differently from build (%v)", rel, err)
		}
	}
	// --bundle: an output rebuilds from a published bundle alone, here one
	// that differs from a's own.
	b.edit(t, "apps/linear/app.yaml", "version: 1.0.0", "version: 1.0.1")
	b.edit(t, "apps/linear/app.yaml", "name: Linear\n", "name: Linear Issues\n")
	b.build(t)
	fromOther := t.TempDir()
	a.run(t, 0, "emit", "--target", "ee-apps", "--bundle", filepath.Join(b.catalog, "dist", "catalog.json"), "--out", fromOther)
	got, err := os.ReadFile(filepath.Join(fromOther, "ee-apps.json"))
	want, err2 := os.ReadFile(filepath.Join(b.catalog, "dist", "emit", "ee-apps.json"))
	if err != nil || err2 != nil || string(got) != string(want) || !strings.Contains(string(got), "Linear Issues") {
		t.Errorf("emit --bundle did not write from the bundle it was given (%v, %v)", err, err2)
	}
}

// --django-copy= writes and compares no Django copy; the default does.
func TestNoDjangoCopy(t *testing.T) {
	c := newCopy(t)
	c.build(t, "--django-copy=")
	if _, err := os.Stat(c.django); !os.IsNotExist(err) {
		t.Fatalf("build --django-copy= wrote %s (%v)", c.django, err)
	}
	c.run(t, 0, "check", "--django-copy=")
	if out := c.run(t, 1, "check"); !strings.Contains(out, "(the Django copy) is missing") {
		t.Errorf("check with the default Django copy: %s", out)
	}
	c.build(t)
	c.run(t, 0, "check")
}

func TestCheckFailsWhenStale(t *testing.T) {
	c := newCopy(t)
	c.build(t)
	c.run(t, 0, "check", "--django-copy", c.django)

	// A record changed, with its bump: the bundle is stale; the sources
	// alone are fine, as a data lane checks them.
	c.edit(t, "apps/close/app.yaml", "version: 1.0.0", "version: 1.0.1")
	c.edit(t, "apps/close/app.yaml", "name: Close\n", "name: Close CRM\n")
	if out := c.run(t, 1, "check", "--django-copy", c.django); !strings.Contains(out, "dist/catalog.json is stale") {
		t.Errorf("a changed record: %s", out)
	}
	c.run(t, 0, "check", "--sources-only")
	c.build(t)
	c.run(t, 0, "check", "--django-copy", c.django)
	if v := c.snapshot(t)[bundlePath]; !strings.Contains(v, `"version": "2026.10.20.1"`) {
		t.Errorf("the rebuilt bundle did not take the next version")
	}

	// A record changed without its bump: build and the sources check refuse.
	c.edit(t, "apps/close/app.yaml", "name: Close CRM\n", "name: Close\n")
	for _, cmd := range [][]string{{"check", "--sources-only"}, {"build", "--date", "2026-10-20", "--source-commit", testCommit}} {
		if out := c.run(t, 1, cmd...); !strings.Contains(out, "needs a patch version bump") {
			t.Errorf("%s without a bump: %s", cmd[0], out)
		}
	}
	c.edit(t, "apps/close/app.yaml", "name: Close\n", "name: Close CRM\n")
	c.run(t, 0, "check", "--django-copy", c.django)

	// Each generated file is compared, and nothing extra may sit in dist/.
	tamper := func(path, content string) string {
		t.Helper()
		old, err := os.ReadFile(path)
		if err != nil && !os.IsNotExist(err) {
			t.Fatal(err)
		}
		if content == "" {
			os.Remove(path)
		} else if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
		out := c.run(t, 1, "check", "--django-copy", c.django)
		if old == nil {
			os.Remove(path)
		} else if err := os.WriteFile(path, old, 0o644); err != nil {
			t.Fatal(err)
		}
		return out
	}
	emitted := filepath.Join(c.catalog, "dist", "emit", "ee-apps.json")
	for name, cs := range map[string]struct{ path, content, want string }{
		"an edited output":      {emitted, `{"apps": []}` + "\n", "dist/emit/ee-apps.json is stale"},
		"a deleted output":      {filepath.Join(c.catalog, "dist", "emit", "gateway-servers.json"), "", "dist/emit/gateway-servers.json is missing"},
		"a stray file in dist":  {filepath.Join(c.catalog, "dist", "emit", "old.json"), "{}\n", "dist/emit/old.json is not written by build"},
		"an edited Django copy": {c.django, "{}\n", "differs from dist/catalog.json"},
		"a deleted Django copy": {c.django, "", "(the Django copy) is missing"},
		"a stray Django file":   {filepath.Join(filepath.Dir(c.django), "reader.py"), "x = 1\n", "is not the Django copy or its README"},
	} {
		if out := tamper(cs.path, cs.content); !strings.Contains(out, cs.want) {
			t.Errorf("%s: %s", name, out)
		}
	}
	c.run(t, 0, "check", "--django-copy", c.django)

	// build removes what it no longer writes.
	stray := filepath.Join(c.catalog, "dist", "emit", "docs", "apps", "gone.md")
	if err := writeFile(stray, []byte("old\n")); err != nil {
		t.Fatal(err)
	}
	c.build(t)
	if _, err := os.Stat(stray); !os.IsNotExist(err) {
		t.Errorf("build left %s", stray)
	}
	c.run(t, 0, "check", "--django-copy", c.django)
}

// A record every record check accepts but an emitter refuses fails the
// sources check as it fails build, with a committed bundle and without one,
// so a data lane cannot pass its gate with a record the integrator's build
// then refuses at the wave merge (CW1 review of CN6, P2).
func TestSourcesOnlyRunsTheEmitters(t *testing.T) {
	const provider = "apps/close/providers/close-mcp.yaml"
	localSetup := func(c *catalogCopy) {
		c.edit(t, provider, "version: 1.0.0", "version: 2.0.0")
		c.edit(t, provider, "kind: api_key\n  credentialParts:\n", "kind: local_setup\n  x-parts:\n")
		c.edit(t, provider, "  x-parts:\n    - in: header\n      name: Close-API-Key\n      secret: true\n      label: API key\n      helpUrl: https://developer.close.com/mcp\n    - in: header\n      name: Close-Scope\n      secret: false\n      label: Scope\n      help: The scope your Close admin granted the key.\n", "")
		c.edit(t, provider, "  - fact: auth.credentialParts\n    url: https://developer.close.com/mcp\n    fetchedAt: 2026-10-19\n", "")
	}
	const want = "emit oss-registry: close/close-mcp: OSS describes an http server's auth"

	c := newCopy(t)
	c.build(t)
	c.run(t, 0, "check", "--sources-only")
	localSetup(c)
	if out := c.run(t, 1, "check", "--sources-only"); !strings.Contains(out, want) {
		t.Errorf("with a committed bundle: %s", out)
	}
	if out := c.run(t, 1, "build", "--date", "2026-10-20", "--source-commit", testCommit); !strings.Contains(out, want) {
		t.Errorf("build: %s", out)
	}

	bare := newCopy(t)
	bare.run(t, 0, "check", "--sources-only")
	localSetup(bare)
	if out := bare.run(t, 1, "check", "--sources-only"); !strings.Contains(out, want) {
		t.Errorf("with no committed bundle: %s", out)
	}
}

// The repository's records pass every check a lane's commit must pass,
// version bumps against the committed bundle included. Whether dist/** is
// fresh is the integrator's check (agentcc-catalog check), not every
// lane's: data lanes never regenerate it (§2.5).
func TestRepoCatalogSourcesCheck(t *testing.T) {
	if code, out := cli("check", "--sources-only", "--catalog", filepath.Join("..", "..", "catalog", "connectors")); code != 0 {
		t.Fatalf("check --sources-only on the repository: exit %d\n%s", code, out)
	}
}

func TestBuildTakesTheSourceCommitFromGit(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	c := newCopy(t)
	gitRun := func(args ...string) string {
		t.Helper()
		cmd := exec.Command("git", append([]string{"-C", c.root, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"}, args...)...)
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	gitRun("init", "-q")
	gitRun("add", ".")
	gitRun("commit", "-q", "-m", "records")
	head := gitRun("rev-parse", "HEAD")
	c.run(t, 0, "build", "--date", "2026-10-20", "--django-copy", c.django)
	if !strings.Contains(c.snapshot(t)[bundlePath], `"sourceCommit": "`+head+`"`) {
		t.Fatal("the bundle does not name HEAD")
	}
	c.edit(t, "apps/close/app.yaml", "version: 1.0.0", "version: 1.0.1")
	if out := c.run(t, 1, "build", "--date", "2026-10-20"); !strings.Contains(out, "uncommitted changes") {
		t.Errorf("uncommitted records: %s", out)
	}
	c.run(t, 0, "build", "--date", "2026-10-20", "--source-commit", testCommit)
}

func TestUsage(t *testing.T) {
	for _, args := range [][]string{{}, {"publish"}, {"emit", "--target", "ee-apps"}, {"build", "extra"}} {
		if code, _ := cli(args...); code != 2 {
			t.Errorf("%v: exit %d, want 2", args, code)
		}
	}
	c := newCopy(t)
	c.build(t)
	if out := c.run(t, 1, "emit", "--target", "pdf", "--out", t.TempDir()); !strings.Contains(out, `no target "pdf"`) {
		t.Errorf("an unknown target: %s", out)
	}
	if out := c.run(t, 1, "build", "--date", "20-10-2026"); !strings.Contains(out, "is not YYYY-MM-DD") {
		t.Errorf("a bad date: %s", out)
	}
	c.edit(t, "apps/close/app.yaml", "version: 1.0.0", "version: 1.0.1")
	if out := c.run(t, 1, "build", "--date", "2026-10-19", "--source-commit", testCommit); !strings.Contains(out, "never go backwards") {
		t.Errorf("an earlier date for changed records: %s", out)
	}
	if out := c.run(t, 1, "build", "--source-commit", "HEAD"); !strings.Contains(out, "not a 40-character git commit") {
		t.Errorf("a source commit that is not a commit: %s", out)
	}
	if out := c.run(t, 1, "build", "--django-copy", "auto", "--catalog", t.TempDir()); !strings.Contains(out, "no futureagi/agentcc beside agentcc-gateway") {
		t.Errorf("auto without a Django app: %s", out)
	}
	for _, cmd := range []string{"build", "check"} {
		if code, out := cli(cmd, "--catalog", filepath.Join(t.TempDir(), "absent")); code != 1 || !strings.Contains(out, "is not a directory") {
			t.Errorf("%s on a missing catalog: exit %d %s", cmd, code, out)
		}
	}
}
