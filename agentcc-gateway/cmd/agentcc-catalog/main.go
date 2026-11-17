// Command agentcc-catalog builds and checks the connector catalog's
// generated files (CONNECTORS-PLAN §2.5): dist/catalog.json, the bundle;
// dist/emit/**, what each consumer receives; and the bundle's copy for
// Django. Run it from agentcc-gateway/.
//
//	agentcc-catalog build [--catalog DIR] [--django-copy PATH] [--date YYYY-MM-DD] [--source-commit SHA]
//	agentcc-catalog check [--catalog DIR] [--django-copy PATH] [--sources-only] [--overlay DIR]
//	agentcc-catalog emit  --target TARGET --out DIR [--catalog DIR] [--bundle PATH]
//
// build validates every record (the schema and L0, L1, the open-data rule,
// no price overlay, and L1's version bump against the last dist/catalog.json)
// and writes every output. It reports each server oss-registry.json and
// server-json leave out, with its rule, on its output and in dist/README.md. Unchanged records
// keep the bundle's version and source commit, so a rebuild changes no byte.
// Changed ones take the next YYYY.MM.DD.N for --date (default today, UTC)
// and the commit --source-commit names, else git's HEAD, which build takes
// only when apps/ and platforms/ have no uncommitted change.
//
// check runs the same validation, then fails when any generated file is
// missing, differs from what build would write, or is under dist/ without
// build writing it. It needs no clock and no git. --sources-only checks the
// records, their version bumps and every emitter's refusals, without
// comparing dist/**, as data lanes do, since dist/** is the integrator's. --overlay validates an operator's price overlay
// directory against the catalog; it must be outside the repository.
//
// emit writes one target's files from a bundle (default the catalog's
// dist/catalog.json) into --out: gateway, ee-apps, ee-settings,
// oss-registry, server-json or docs.
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/emit"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/overlay"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

const usage = `usage:
  agentcc-catalog build [--catalog DIR] [--django-copy PATH] [--date YYYY-MM-DD] [--source-commit SHA]
  agentcc-catalog check [--catalog DIR] [--django-copy PATH] [--sources-only] [--overlay DIR]
  agentcc-catalog emit  --target TARGET --out DIR [--catalog DIR] [--bundle PATH]
targets: ` + "gateway, ee-apps, ee-settings, oss-registry, server-json, docs" + "\n"

// run is the command, with its exit code: 0 done, 1 failed, 2 misused.
func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprint(stderr, usage)
		return 2
	}
	cmd, args := args[0], args[1:]
	fset := flag.NewFlagSet("agentcc-catalog "+cmd, flag.ContinueOnError)
	fset.SetOutput(stderr)
	catalogDir := fset.String("catalog", filepath.Join("catalog", "connectors"), "the catalog directory")
	var err error
	switch cmd {
	case "build":
		djangoCopy := fset.String("django-copy", "auto", "where to copy the bundle for Django (auto: the repository's futureagi/agentcc/connector_catalog/catalog.json; empty: no copy)")
		date := fset.String("date", "", "the build date for a new version, YYYY-MM-DD (default today, UTC)")
		commit := fset.String("source-commit", "", "the git commit the records come from (default git's HEAD)")
		if fset.Parse(args) != nil || fset.NArg() > 0 {
			return misused(stderr, fset)
		}
		err = runBuild(stdout, *catalogDir, *djangoCopy, *date, *commit)
	case "check":
		djangoCopy := fset.String("django-copy", "auto", "the Django copy to compare (auto, a path, or empty for none)")
		sourcesOnly := fset.Bool("sources-only", false, "check the records, their version bumps and the emitters' refusals, not dist/**")
		overlayDir := fset.String("overlay", "", "an operator's price overlay directory to validate against the catalog")
		if fset.Parse(args) != nil || fset.NArg() > 0 {
			return misused(stderr, fset)
		}
		err = runCheck(stdout, *catalogDir, *djangoCopy, *sourcesOnly, *overlayDir)
	case "emit":
		target := fset.String("target", "", "the target to write: "+strings.Join(emit.Targets, ", "))
		out := fset.String("out", "", "the directory to write into")
		bundleFile := fset.String("bundle", "", "the bundle to write from (default <catalog>/dist/catalog.json)")
		if fset.Parse(args) != nil || fset.NArg() > 0 || *target == "" || *out == "" {
			return misused(stderr, fset)
		}
		err = runEmit(stdout, *catalogDir, *bundleFile, *target, *out)
	default:
		fmt.Fprint(stderr, usage)
		return 2
	}
	if err != nil {
		fmt.Fprintf(stderr, "agentcc-catalog %s: %v\n", cmd, err)
		return 1
	}
	return 0
}

func misused(stderr io.Writer, fset *flag.FlagSet) int {
	fmt.Fprint(stderr, usage)
	fset.PrintDefaults()
	return 2
}

// catalogDirOK refuses a catalog directory that is not one, so a wrong
// --catalog never reads as an empty catalog.
func catalogDirOK(dir string) error {
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		return fmt.Errorf("--catalog %s is not a directory (run from agentcc-gateway/, or pass --catalog)", dir)
	}
	return nil
}

func runBuild(stdout io.Writer, catalogDir, djangoCopy, date, commit string) error {
	if err := catalogDirOK(catalogDir); err != nil {
		return err
	}
	when := time.Now().UTC()
	if date != "" {
		var err error
		if when, err = time.Parse("2006-01-02", date); err != nil {
			return fmt.Errorf("--date %q is not YYYY-MM-DD", date)
		}
	}
	copyPath, err := resolveDjangoCopy(catalogDir, djangoCopy)
	if err != nil {
		return err
	}
	source := func() (string, error) {
		if commit != "" {
			return commit, nil
		}
		return gitHead(catalogDir)
	}
	out, err := generate(os.DirFS(catalogDir), when, source)
	if err != nil {
		return err
	}
	if err := write(catalogDir, copyPath, out); err != nil {
		return err
	}
	fmt.Fprintf(stdout, "bundle %s, sha256 %s, from %s: %d files written\n",
		out.bundle.Version, out.bundle.SHA256, out.bundle.SourceCommit, len(out.files)+boolInt(copyPath != ""))
	reportOSSLeftOut(stdout, out.ossLeftOut)
	reportServerJSONLeftOut(stdout, out.serverJSONLeftOut)
	return nil
}

// reportServerJSONLeftOut names each server emit/server-json leaves out,
// with its rule, one line each (dist/README.md lists the same).
func reportServerJSONLeftOut(w io.Writer, left []emit.ServerJSONLeftOut) {
	if len(left) == 0 {
		fmt.Fprintln(w, "server-json leaves out no server")
		return
	}
	for _, s := range left {
		fmt.Fprintf(w, "server-json leaves out %s (%s): %s: %s\n", s.Name, s.Ref, s.Reason, emit.ServerJSONLeftOutReasons[s.Reason])
	}
}

// reportOSSLeftOut names each server oss-registry.json leaves out, with its
// rule, one line each (dist/README.md lists the same).
func reportOSSLeftOut(w io.Writer, left []emit.OSSLeftOut) {
	if len(left) == 0 {
		fmt.Fprintln(w, "oss-registry.json leaves out no server")
		return
	}
	for _, s := range left {
		reg := ""
		if len(s.Registration) > 0 {
			reg = ", registration " + strings.Join(s.Registration, ", ")
		}
		fmt.Fprintf(w, "oss-registry.json leaves out %s (%s%s; public client %s): %s: %s\n",
			s.ID, s.Ref, reg, s.PublicClient, s.Reason, manifest.OSSLeftOutReasons[s.Reason])
	}
}

func runCheck(stdout io.Writer, catalogDir, djangoCopy string, sourcesOnly bool, overlayDir string) error {
	if err := catalogDirOK(catalogDir); err != nil {
		return err
	}
	fsys := os.DirFS(catalogDir)
	if sourcesOnly {
		if err := checkSourcesOnly(fsys); err != nil {
			return err
		}
	} else {
		copyPath, err := resolveDjangoCopy(catalogDir, djangoCopy)
		if err != nil {
			return err
		}
		if err := check(catalogDir, copyPath); err != nil {
			return err
		}
	}
	if overlayDir != "" {
		if err := checkOverlay(catalogDir, overlayDir); err != nil {
			return err
		}
	}
	fmt.Fprintln(stdout, "ok")
	return nil
}

// checkOverlay validates an operator's overlay directory against the
// catalog. The directory must be outside the repository the catalog is in:
// overlay data never enters the repository.
func checkOverlay(catalogDir, overlayDir string) error {
	if top, err := gitTopLevel(catalogDir); err == nil {
		inside, err := within(overlayDir, top)
		if err != nil {
			return err
		}
		if inside {
			return fmt.Errorf("--overlay %s is inside the repository at %s: overlay data never enters the repository", overlayDir, top)
		}
	}
	entries, err := overlay.LoadDir(os.DirFS(overlayDir))
	if err != nil {
		return fmt.Errorf("--overlay %s: %w", overlayDir, err)
	}
	cat, err := sources(os.DirFS(catalogDir))
	if err != nil {
		return err
	}
	return overlay.Check(entries, cat)
}

func runEmit(stdout io.Writer, catalogDir, bundleFile, target, outDir string) error {
	if bundleFile == "" {
		bundleFile = filepath.Join(catalogDir, filepath.FromSlash(bundlePath))
	}
	data, err := os.ReadFile(bundleFile)
	if err != nil {
		return err
	}
	b, err := bundle.Decode(data)
	if err != nil {
		return fmt.Errorf("%s: %w", bundleFile, err)
	}
	cat, err := b.Catalog()
	if err != nil {
		return fmt.Errorf("%s: %w", bundleFile, err)
	}
	files, err := emit.Target(target, cat, emit.Meta{Version: b.Version, SHA256: b.SHA256})
	if err != nil {
		return err
	}
	for _, p := range files.Paths() {
		if err := writeFile(filepath.Join(outDir, filepath.FromSlash(p)), files[p]); err != nil {
			return err
		}
		fmt.Fprintln(stdout, filepath.Join(outDir, filepath.FromSlash(p)))
	}
	return nil
}

// resolveDjangoCopy turns --django-copy into a path, or "" for none. auto
// is the repository's Django app: <catalog>/../../../futureagi/agentcc.
func resolveDjangoCopy(catalogDir, flagValue string) (string, error) {
	if flagValue != "auto" {
		return flagValue, nil
	}
	app := filepath.Join(catalogDir, "..", "..", "..", "futureagi", "agentcc")
	if info, err := os.Stat(app); err != nil || !info.IsDir() {
		return "", errors.New("--django-copy auto: no futureagi/agentcc beside agentcc-gateway; pass --django-copy PATH, or --django-copy= for no copy")
	}
	return filepath.Join(app, "connector_catalog", "catalog.json"), nil
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
