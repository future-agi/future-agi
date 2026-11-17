package main

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/emit"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/overlay"
)

// Paths under the catalog directory.
const (
	distDir    = "dist"
	bundlePath = "dist/catalog.json"
	emitDir    = "dist/emit"
)

// djangoReadme is the hand-written file beside the Django copy.
const djangoReadme = "README.md"

// outputs is everything build writes under the catalog directory, keyed by
// slash path; the Django copy is a copy of files[bundlePath]. ossLeftOut and
// serverJSONLeftOut are what oss-registry.json and server-json leave out, for
// build's report.
type outputs struct {
	bundle     *bundle.Bundle
	files      map[string][]byte
	ossLeftOut []emit.OSSLeftOut
	// serverJSONLeftOut is what emit/server-json leaves out.
	serverJSONLeftOut []emit.ServerJSONLeftOut
}

// sources loads the catalog's records and runs every check a record must
// pass before anything is generated: the schema and L0 (manifest.LoadDir),
// L1, the open-data rule, and no price overlay anywhere in the catalog.
func sources(fsys fs.FS) (*manifest.Catalog, error) {
	cat, err := manifest.LoadDir(fsys)
	if err != nil {
		return nil, fmt.Errorf("the catalog's records are not valid:\n%w", err)
	}
	if err := manifest.CheckL1(cat); err != nil {
		return nil, fmt.Errorf("the catalog's records fail L1:\n%w", err)
	}
	if err := manifest.CheckPublic(cat); err != nil {
		return nil, err
	}
	if err := noOverlay(fsys, ""); err != nil {
		return nil, err
	}
	return cat, nil
}

// noOverlay refuses an overlay document anywhere in fsys: overlay data is
// private and never enters the catalog, a bundle or the repository.
func noOverlay(fsys fs.FS, where string) error {
	found, err := overlay.FindDocuments(fsys)
	if err != nil {
		return err
	}
	if len(found) > 0 {
		for i, p := range found {
			found[i] = filepath.Join(where, p)
		}
		return fmt.Errorf("%s holds price overlay data (%s), which never enters the catalog or the repository: move it to the operator's overlay directory",
			strings.Join(found, ", "), overlay.Schema)
	}
	return nil
}

// lastBundle reads the committed dist/catalog.json, which is the last
// published bundle, and its records as a catalog; both nil when there is
// none.
func lastBundle(fsys fs.FS) (*bundle.Bundle, *manifest.Catalog, error) {
	prev, err := bundle.ReadFile(fsys, bundlePath)
	if err != nil || prev == nil {
		return nil, nil, err
	}
	cat, err := prev.Catalog()
	if err != nil {
		return nil, nil, fmt.Errorf("%s, the last published bundle, is not valid: %w", bundlePath, err)
	}
	return prev, cat, nil
}

// generate builds every output in memory. The bundle keeps the last one's
// version and source commit when its records are unchanged; otherwise it
// takes the next version for date and the commit commit returns. A changed
// record must move its version as L1's semver rule requires.
func generate(fsys fs.FS, date time.Time, commit func() (string, error)) (*outputs, error) {
	src, err := sources(fsys)
	if err != nil {
		return nil, err
	}
	prev, prevCat, err := lastBundle(fsys)
	if err != nil {
		return nil, err
	}
	b, err := bundle.Build(fsys, src, prev, date, commit)
	if err != nil {
		return nil, err
	}
	cat, err := b.Catalog()
	if err != nil {
		return nil, err
	}
	if prevCat != nil {
		if err := bundle.CheckBumps(prevCat, cat); err != nil {
			return nil, fmt.Errorf("a record changed since %s without the version bump L1 requires:\n%w", bundlePath, err)
		}
	}
	emitted, err := emit.All(cat, emit.Meta{Version: b.Version, SHA256: b.SHA256})
	if err != nil {
		return nil, err
	}
	data, err := b.Encode()
	if err != nil {
		return nil, err
	}
	leftOut := emit.OSSLeftOutServers(cat)
	sjLeftOut := emit.ServerJSONLeftOutServers(cat)
	files := map[string][]byte{bundlePath: data, distReadmePath: distReadme(b, leftOut, sjLeftOut)}
	for p, data := range emitted {
		files[emitDir+"/"+p] = data
	}
	return &outputs{bundle: b, files: files, ossLeftOut: leftOut, serverJSONLeftOut: sjLeftOut}, nil
}

// sourcesOnlyMeta names no real bundle: checkSourcesOnly runs the emitters
// for their refusals only and discards what they write.
var sourcesOnlyMeta = emit.Meta{Version: "0000.00.00.0", SHA256: strings.Repeat("0", 64)}

// checkSourcesOnly is what a data lane runs (§2.5): the records' checks,
// their version bumps against the committed bundle (when there is one), and
// every emitter's refusals, without regenerating the files the integrator
// owns. The emitters are pure, so running them writes nothing; a record one
// of them refuses fails here as it would fail build at the wave merge.
func checkSourcesOnly(fsys fs.FS) error {
	src, err := sources(fsys)
	if err != nil {
		return err
	}
	_, prevCat, err := lastBundle(fsys)
	if err != nil {
		return err
	}
	apps, providers, platforms, err := bundle.Records(fsys, src)
	if err != nil {
		return err
	}
	cur, err := (&bundle.Bundle{Apps: apps, Providers: providers, Platforms: platforms}).Catalog()
	if err != nil {
		return err
	}
	if prevCat != nil {
		if err := bundle.CheckBumps(prevCat, cur); err != nil {
			return fmt.Errorf("a record changed since %s without the version bump L1 requires:\n%w", bundlePath, err)
		}
	}
	if _, err := emit.All(cur, sourcesOnlyMeta); err != nil {
		return fmt.Errorf("build would refuse the catalog's records:\n%w", err)
	}
	return nil
}

// errStale is what check reports when the records changed after the last
// build.
var errStale = errors.New("the records changed since dist/catalog.json was built")

// check regenerates every output and compares it, byte for byte, with what
// is on disk: dist/** and the Django copy. It needs no clock and no git: a
// fresh bundle keeps its version and source commit.
func check(catalogDir, djangoCopy string) error {
	fsys := os.DirFS(catalogDir)
	out, err := generate(fsys, time.Time{}, func() (string, error) { return "", errStale })
	if errors.Is(err, errStale) {
		if _, statErr := fs.Stat(fsys, bundlePath); errors.Is(statErr, fs.ErrNotExist) {
			return fmt.Errorf("%s is missing: run go run ./cmd/agentcc-catalog build", filepath.Join(catalogDir, bundlePath))
		}
		return fmt.Errorf("%s is stale: %w", filepath.Join(catalogDir, bundlePath), err)
	}
	if err != nil {
		return err
	}
	var problems []string
	for _, p := range sortedKeys(out.files) {
		disk, err := os.ReadFile(filepath.Join(catalogDir, filepath.FromSlash(p)))
		switch {
		case errors.Is(err, fs.ErrNotExist):
			problems = append(problems, p+" is missing")
		case err != nil:
			problems = append(problems, err.Error())
		case string(disk) != string(out.files[p]):
			problems = append(problems, p+" is stale")
		}
	}
	extra, err := filesUnder(catalogDir, distDir)
	if err != nil {
		return err
	}
	for _, p := range extra {
		if _, ok := out.files[p]; !ok {
			problems = append(problems, p+" is not written by build")
		}
	}
	if djangoCopy != "" {
		problems = append(problems, checkDjangoCopy(djangoCopy, out.files[bundlePath])...)
	}
	if len(problems) > 0 {
		return fmt.Errorf("the generated files are out of date (run: go run ./cmd/agentcc-catalog build):\n  %s", strings.Join(problems, "\n  "))
	}
	return nil
}

// checkDjangoCopy compares the Django copy with the bundle, and keeps its
// directory to the copy and its README.
func checkDjangoCopy(path string, want []byte) []string {
	var problems []string
	disk, err := os.ReadFile(path)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		problems = append(problems, path+" (the Django copy) is missing")
	case err != nil:
		problems = append(problems, err.Error())
	case string(disk) != string(want):
		problems = append(problems, path+" (the Django copy) differs from "+bundlePath)
	}
	dir := filepath.Dir(path)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return append(problems, err.Error())
	}
	for _, e := range entries {
		if e.Name() != filepath.Base(path) && e.Name() != djangoReadme {
			problems = append(problems, filepath.Join(dir, e.Name())+" is not the Django copy or its README")
		}
	}
	return problems
}

// write writes every output, removes any other file under dist/, and
// writes the Django copy.
func write(catalogDir, djangoCopy string, out *outputs) error {
	for _, p := range sortedKeys(out.files) {
		if err := writeFile(filepath.Join(catalogDir, filepath.FromSlash(p)), out.files[p]); err != nil {
			return err
		}
	}
	extra, err := filesUnder(catalogDir, distDir)
	if err != nil {
		return err
	}
	for _, p := range extra {
		if _, ok := out.files[p]; !ok {
			if err := os.Remove(filepath.Join(catalogDir, filepath.FromSlash(p))); err != nil {
				return err
			}
		}
	}
	if err := removeEmptyDirs(filepath.Join(catalogDir, distDir)); err != nil {
		return err
	}
	if djangoCopy != "" {
		return writeFile(djangoCopy, out.files[bundlePath])
	}
	return nil
}

func writeFile(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

// filesUnder lists the regular files under root/dir as slash paths
// relative to root.
func filesUnder(root, dir string) ([]string, error) {
	var out []string
	err := filepath.WalkDir(filepath.Join(root, dir), func(p string, d fs.DirEntry, err error) error {
		if errors.Is(err, fs.ErrNotExist) {
			return nil
		}
		if err != nil || d.IsDir() {
			return err
		}
		rel, err := filepath.Rel(root, p)
		if err != nil {
			return err
		}
		out = append(out, filepath.ToSlash(rel))
		return nil
	})
	slices.Sort(out)
	return out, err
}

// removeEmptyDirs removes the directories under root that hold nothing.
func removeEmptyDirs(root string) error {
	var dirs []string
	err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err == nil && d.IsDir() && p != root {
			dirs = append(dirs, p)
		}
		return err
	})
	if err != nil {
		return err
	}
	slices.Reverse(dirs) // children first
	for _, d := range dirs {
		if entries, err := os.ReadDir(d); err == nil && len(entries) == 0 {
			if err := os.Remove(d); err != nil {
				return err
			}
		}
	}
	return nil
}

func sortedKeys(m map[string][]byte) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	slices.Sort(out)
	return out
}
