package bundle

import (
	"errors"
	"fmt"
	"io/fs"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Records reads every record of cat, a catalog manifest.LoadDir accepted
// from fsys, in bundle form: each file as it is written, keys sorted, and a
// rest provider's generated tools after its curated ones in tools.items.
func Records(fsys fs.FS, cat *manifest.Catalog) (apps, providers, platforms []any, err error) {
	apps, providers, platforms = []any{}, []any{}, []any{}
	var errs []error
	for _, a := range cat.Apps {
		rec, err := readRecord(fsys, "apps/"+a.ID+"/app.yaml")
		if err != nil {
			errs = append(errs, err)
			continue
		}
		apps = append(apps, rec)
	}
	for _, a := range cat.Apps {
		for _, p := range a.Providers {
			rec, err := providerRecord(fsys, p)
			if err != nil {
				errs = append(errs, err)
				continue
			}
			providers = append(providers, rec)
		}
	}
	for _, pl := range cat.Platforms {
		rec, err := readRecord(fsys, "platforms/"+pl.ID+".yaml")
		if err != nil {
			errs = append(errs, err)
			continue
		}
		platforms = append(platforms, rec)
	}
	if err := errors.Join(errs...); err != nil {
		return nil, nil, nil, err
	}
	return apps, providers, platforms, nil
}

func readRecord(fsys fs.FS, path string) (any, error) {
	data, err := fs.ReadFile(fsys, path)
	if err != nil {
		return nil, err
	}
	v, err := canonjson.ParseYAML(data)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return canonjson.Sorted(v), nil
}

// providerRecord reads a provider's file and folds its tools.generated.json,
// when it names one, into tools.items.
func providerRecord(fsys fs.FS, p *manifest.Provider) (any, error) {
	path := "apps/" + p.App + "/providers/" + p.ID + ".yaml"
	data, err := fs.ReadFile(fsys, path)
	if err != nil {
		return nil, err
	}
	v, err := canonjson.ParseYAML(data)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	rec, ok := v.(canonjson.Object)
	if !ok {
		return nil, fmt.Errorf("%s: must be an object", path)
	}
	if p.Tools.Generated != "" {
		gpath := "apps/" + p.App + "/providers/" + p.ID + "/" + p.Tools.Generated
		gdata, err := fs.ReadFile(fsys, gpath)
		if err != nil {
			return nil, err
		}
		gv, err := canonjson.Parse(gdata)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", gpath, err)
		}
		generated, _ := mustGet(asObject(gv), "tools").([]any)
		tools := asObject(mustGet(rec, "tools"))
		if tools == nil {
			return nil, fmt.Errorf("%s: tools must be an object", path)
		}
		items, _ := mustGet(tools, "items").([]any)
		tools.Set("items", append(append([]any{}, items...), generated...))
		tools.Delete("generated")
		rec.Set("tools", tools)
	}
	return canonjson.Sorted(rec), nil
}

func asObject(v any) canonjson.Object {
	o, _ := v.(canonjson.Object)
	return o
}

// Build compiles cat, read from fsys, into a bundle. When prev, the last
// bundle, holds the same records, Build keeps its version and source
// commit, so rebuilding unchanged records changes no byte and needs no
// clock or git. Otherwise it asks commit for the source commit, then takes
// the next version for date.
func Build(fsys fs.FS, cat *manifest.Catalog, prev *Bundle, date time.Time, commit func() (string, error)) (*Bundle, error) {
	apps, providers, platforms, err := Records(fsys, cat)
	if err != nil {
		return nil, err
	}
	digest, err := Digest(apps, providers, platforms)
	if err != nil {
		return nil, err
	}
	b := &Bundle{SHA256: digest, Apps: apps, Providers: providers, Platforms: platforms}
	if prev != nil && prev.SHA256 == digest {
		b.Version, b.SourceCommit = prev.Version, prev.SourceCommit
		return b, nil
	}
	if b.SourceCommit, err = commit(); err != nil {
		return nil, err
	}
	if !commitPattern.MatchString(b.SourceCommit) {
		return nil, fmt.Errorf("source commit %q is not a 40-character git commit", b.SourceCommit)
	}
	last := ""
	if prev != nil {
		last = prev.Version
	}
	if b.Version, err = NextVersion(last, date); err != nil {
		return nil, err
	}
	return b, nil
}

// NextVersion is the version after last, a bundle version or "", for a
// build on date: YYYY.MM.DD.0 on a new day, and N+1 on the day of last.
func NextVersion(last string, date time.Time) (string, error) {
	day := date.UTC().Format("2006.01.02")
	if last == "" {
		return day + ".0", nil
	}
	m := versionPattern.FindStringSubmatch(last)
	if m == nil {
		return "", fmt.Errorf("the last bundle's version %q is not YYYY.MM.DD.N", last)
	}
	switch {
	case m[1] > day:
		return "", fmt.Errorf("the last bundle is version %s, after %s: versions never go backwards; build with a later date", last, day)
	case m[1] < day:
		return day + ".0", nil
	}
	var n int
	fmt.Sscanf(m[4], "%d", &n)
	if n >= 9999 {
		return "", fmt.Errorf("version %s is the last of its day", last)
	}
	return fmt.Sprintf("%s.%d", day, n+1), nil
}
