package bundle

import (
	"errors"
	"fmt"
	"regexp"
	"testing/fstest"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Record ids, as the manifest schema's appId and providerId define them;
// they become path segments, so nothing else is accepted.
var (
	appIDPattern      = regexp.MustCompile(`^[a-z][a-z0-9-]{1,39}$`)
	providerIDPattern = regexp.MustCompile(`^[a-z][a-z0-9-]{1,23}$`)
)

// Catalog checks the bundle's records as the catalog's own files are
// checked, and returns them as a catalog: each record goes back into the
// catalog layout and through manifest.LoadDir (the schema and L0),
// manifest.CheckL1 and manifest.CheckPublic. Its providers have no
// tools.generated: their generated tools are in tools.items.
func (b *Bundle) Catalog() (*manifest.Catalog, error) {
	fsys := fstest.MapFS{}
	var errs []error
	put := func(path string, rec any) {
		if _, dup := fsys[path]; dup {
			errs = append(errs, fmt.Errorf("bundle: %s is in the bundle twice", path))
			return
		}
		data, err := canonjson.Compact(rec)
		if err != nil {
			errs = append(errs, fmt.Errorf("bundle: %s: %w", path, err))
			return
		}
		fsys[path] = &fstest.MapFile{Data: data, Mode: 0o644}
	}
	for i, rec := range b.Apps {
		id, err := recordID(rec, "id", appIDPattern)
		if err != nil {
			errs = append(errs, fmt.Errorf("bundle: apps[%d]: %w", i, err))
			continue
		}
		put("apps/"+id+"/app.yaml", rec)
	}
	for i, rec := range b.Providers {
		app, err1 := recordID(rec, "app", appIDPattern)
		id, err2 := recordID(rec, "id", providerIDPattern)
		if err := errors.Join(err1, err2); err != nil {
			errs = append(errs, fmt.Errorf("bundle: providers[%d]: %w", i, err))
			continue
		}
		put("apps/"+app+"/providers/"+id+".yaml", rec)
	}
	for i, rec := range b.Platforms {
		id, err := recordID(rec, "id", appIDPattern)
		if err != nil {
			errs = append(errs, fmt.Errorf("bundle: platforms[%d]: %w", i, err))
			continue
		}
		put("platforms/"+id+".yaml", rec)
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	cat, err := manifest.LoadDir(fsys)
	if err != nil {
		return nil, fmt.Errorf("bundle: its records are not a valid catalog:\n%w", err)
	}
	if err := manifest.CheckL1(cat); err != nil {
		return nil, fmt.Errorf("bundle: its records fail L1:\n%w", err)
	}
	if err := manifest.CheckPublic(cat); err != nil {
		return nil, fmt.Errorf("bundle: %w", err)
	}
	return cat, nil
}

func recordID(rec any, key string, pattern *regexp.Regexp) (string, error) {
	obj, ok := rec.(canonjson.Object)
	if !ok {
		return "", errors.New("must be an object")
	}
	id, _ := mustGet(obj, key).(string)
	if !pattern.MatchString(id) {
		return "", fmt.Errorf("%s %q does not match %s", key, id, pattern)
	}
	return id, nil
}

// CheckBumps applies L1's semver rule (CONNECTORS-PLAN §2.5) to each record
// next holds that prev, the last published bundle, also holds: a changed
// record moves its version by at least the bump manifest.SemverDiff
// requires, and never backwards. Both catalogs are in bundle form, so a
// rest provider compares like for like.
func CheckBumps(prev, next *manifest.Catalog) error {
	var errs []error
	for _, a := range next.Apps {
		old := prev.App(a.ID)
		if old == nil {
			continue
		}
		errs = append(errs, manifest.CheckVersionBump(old, a))
		for _, p := range a.Providers {
			if op := old.Provider(p.ID); op != nil {
				errs = append(errs, manifest.CheckVersionBump(op, p))
			}
		}
	}
	for _, pl := range next.Platforms {
		if old := prev.Platform(pl.ID); old != nil {
			errs = append(errs, manifest.CheckVersionBump(old, pl))
		}
	}
	return errors.Join(errs...)
}
