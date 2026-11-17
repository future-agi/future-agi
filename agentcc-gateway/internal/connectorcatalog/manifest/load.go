package manifest

import (
	"errors"
	"fmt"
	"io/fs"
	"slices"
	"strings"
	"unicode/utf8"
)

// maxFileBytes bounds what LoadDir reads from one file.
const maxFileBytes = 16 << 20

// LoadDir reads the catalog in fsys: apps/<app-id>/app.yaml,
// apps/<app-id>/providers/<provider-id>.yaml, and beside each provider
// tools.generated.json, tools.yaml, fixtures/** and evals/**; and
// platforms/<platform-id>.yaml. apps/ and platforms/ are optional; other
// top-level entries (schema/, dist/, README.md) are not records.
//
// Every file is scanned for secret-shaped values, and a record that holds
// one is not decoded further. Records are decoded strictly and checked
// against the schema and L0. A returned *Catalog is L0-clean; on any problem
// LoadDir returns nil and every problem it found, each naming its file and
// none quoting a secret.
func LoadDir(fsys fs.FS) (*Catalog, error) {
	l := &loader{fsys: fsys, apps: map[string]*App{}, generated: map[string]*ToolsGenerated{},
		generatedSeen: map[string]bool{}, providerDirs: map[string]string{}}
	for _, root := range []string{"apps", "platforms"} {
		if _, err := fs.Stat(fsys, root); errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err := fs.WalkDir(fsys, root, l.visit); err != nil {
			l.errs = append(l.errs, err)
		}
	}
	cat := l.assemble()
	if len(l.errs) > 0 {
		return nil, redacted(errors.Join(l.errs...))
	}
	if errs := checkCatalog(cat); len(errs) > 0 {
		return nil, redacted(errors.Join(errs...))
	}
	return cat, nil
}

type loader struct {
	fsys          fs.FS
	errs          []error
	apps          map[string]*App
	appDirs       []string // directories under apps/ that hold any file
	providers     []*Provider
	platforms     []*Platform
	generated     map[string]*ToolsGenerated // by "<app>/<provider>"
	generatedSeen map[string]bool            // a tools.generated.json exists, parsed or not
	providerDirs  map[string]string          // "<app>/<provider>" -> a file inside it
}

func (l *loader) fail(format string, args ...any) {
	l.errs = append(l.errs, fmt.Errorf(format, args...))
}

func (l *loader) visit(path string, d fs.DirEntry, err error) error {
	if err != nil {
		l.fail("%s: %v", path, err)
		return nil
	}
	if d.IsDir() || d.Name() == ".DS_Store" {
		return nil
	}
	info, err := d.Info()
	if err != nil {
		l.fail("%s: %v", path, err)
		return nil
	}
	if info.Size() > maxFileBytes {
		l.fail("%s: is larger than %d bytes", path, maxFileBytes)
		return nil
	}
	data, err := fs.ReadFile(l.fsys, path)
	if err != nil {
		l.fail("%s: %v", path, err)
		return nil
	}
	secrets := scanFile(path, data)
	l.errs = append(l.errs, secrets...)

	parts := strings.Split(path, "/")
	if parts[0] == "apps" && len(parts) >= 3 && !slices.Contains(l.appDirs, parts[1]) {
		l.appDirs = append(l.appDirs, parts[1])
	}
	kind := classify(parts)
	if kind == "" {
		l.fail("%s: not a catalog file (see catalog/connectors/README.md for the layout)", path)
		return nil
	}
	if kind == "scan" {
		l.providerDirs[parts[1]+"/"+parts[3]] = path
		return nil
	}
	if !utf8.Valid(data) {
		l.fail("%s: is not UTF-8", path)
		return nil
	}
	if len(secrets) > 0 {
		// The record is not decoded: a check of the value would quote it.
		if kind == "generated" {
			key := parts[1] + "/" + parts[3]
			l.providerDirs[key], l.generatedSeen[key] = path, true
		}
		return nil
	}
	stem := strings.TrimSuffix(parts[len(parts)-1], ".yaml")
	switch kind {
	case "app":
		a, errs := parseApp(path, data)
		l.errs = append(l.errs, errs...)
		if a != nil {
			if a.ID != parts[1] {
				l.fail("%s: id %s must equal its directory name %s", path, a.ID, parts[1])
			}
			l.apps[parts[1]] = a
		}
	case "provider":
		p, errs := parseProvider(path, data)
		l.errs = append(l.errs, errs...)
		if p != nil {
			if p.ID != stem {
				l.fail("%s: id %s must equal its file name %s", path, p.ID, stem)
			}
			if p.App != parts[1] {
				l.fail("%s: app %s must equal its directory name %s", path, p.App, parts[1])
			}
			l.providers = append(l.providers, p)
		}
	case "generated":
		key := parts[1] + "/" + parts[3]
		l.providerDirs[key] = path
		l.generatedSeen[key] = true
		g, errs := parseGenerated(path, data)
		l.errs = append(l.errs, errs...)
		if g != nil && len(errs) == 0 {
			l.generated[key] = g
		}
	case "platform":
		p, errs := parsePlatform(path, data)
		l.errs = append(l.errs, errs...)
		if p != nil {
			if p.ID != stem {
				l.fail("%s: id %s must equal its file name %s", path, p.ID, stem)
			}
			l.platforms = append(l.platforms, p)
		}
	}
	return nil
}

// classify names what a path under apps/ or platforms/ holds, or "" when it
// is not part of the layout. "scan" files are read only by the secret scan.
func classify(parts []string) string {
	n := len(parts)
	switch {
	case parts[0] == "platforms":
		if n == 2 && strings.HasSuffix(parts[1], ".yaml") {
			return "platform"
		}
	case n == 3 && parts[2] == "app.yaml":
		return "app"
	case n == 4 && parts[2] == "providers" && strings.HasSuffix(parts[3], ".yaml"):
		return "provider"
	case n == 5 && parts[2] == "providers" && parts[4] == GeneratedToolsFile:
		return "generated"
	case n == 5 && parts[2] == "providers" && parts[4] == "tools.yaml":
		return "scan"
	case n >= 6 && parts[2] == "providers" && (parts[4] == "fixtures" || parts[4] == "evals"):
		return "scan"
	}
	return ""
}

// assemble attaches providers and generated tools to their apps and
// reports what the layout leaves dangling.
func (l *loader) assemble() *Catalog {
	cat := &Catalog{Platforms: l.platforms}
	for _, dir := range l.appDirs {
		if l.apps[dir] == nil && !l.failedApp(dir) {
			l.fail("apps/%s: has no app.yaml", dir)
		}
	}
	for _, p := range l.providers {
		if a := l.apps[p.App]; a != nil {
			a.Providers = append(a.Providers, p)
			p.app = a
		}
	}
	for _, key := range sortedNames(l.providerDirs) {
		app, provider, _ := strings.Cut(key, "/")
		if a := l.apps[app]; a == nil || a.Provider(provider) == nil {
			if !l.failedProvider(app, provider) {
				l.fail("apps/%s/providers/%s: has no %s.yaml", app, provider, provider)
			}
		}
	}
	for _, p := range l.providers {
		g := l.generated[p.App+"/"+p.ID]
		gpath := "apps/" + p.App + "/providers/" + p.ID + "/" + GeneratedToolsFile
		switch {
		case p.Tools.Generated != "" && g == nil:
			if !l.generatedSeen[p.App+"/"+p.ID] { // a file that failed to parse is reported already
				l.fail("%s: tools.generated names %s, which does not exist", p.path, gpath)
			}
		case p.Tools.Generated == "" && l.generatedSeen[p.App+"/"+p.ID]:
			l.fail("%s: exists, but %s's tools.generated does not name it", gpath, p.path)
		case g != nil:
			if spec := p.Upstream; spec == nil || spec.Spec == nil || spec.Spec.URL != g.Spec.URL || spec.Spec.SHA256 != g.Spec.SHA256 {
				l.fail("%s: spec sha256 %s (%s) does not equal %s's upstream.spec", gpath, g.Spec.SHA256, g.Spec.URL, p.path)
			}
			p.GeneratedTools, p.generatedPath = g.Tools, gpath
		}
	}
	for _, id := range sortedNames(l.apps) {
		a := l.apps[id]
		slices.SortFunc(a.Providers, func(x, y *Provider) int { return strings.Compare(x.ID, y.ID) })
		cat.Apps = append(cat.Apps, a)
	}
	slices.SortFunc(cat.Platforms, func(x, y *Platform) int { return strings.Compare(x.ID, y.ID) })
	return cat
}

// failedApp reports whether dir's app.yaml exists but failed to parse, so
// the problem is already reported.
func (l *loader) failedApp(dir string) bool {
	_, err := fs.Stat(l.fsys, "apps/"+dir+"/app.yaml")
	return err == nil
}

func (l *loader) failedProvider(app, provider string) bool {
	_, err := fs.Stat(l.fsys, "apps/"+app+"/providers/"+provider+".yaml")
	return err == nil
}
