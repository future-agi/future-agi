// Command l3conformance is the Go half of the connector L3 conformance
// harness; scripts/scenarios/connectors/93_cn_conformance.sh drives it:
//
//	l3conformance plan   --catalog testdata|<dir> --gateway-dir <agentcc-gateway> --work <dir>
//	l3conformance render --work <dir> --mocks <mocks.json> --base <gateway.yaml> --out <file>
//	l3conformance orgs   --work <dir> --mocks <mocks.json> --keys <keys.json> --out <dir>
//	l3conformance check  --work <dir> --evidence <dir> --report <file> [--run-at YYYY-MM-DD]
//
// plan writes <work>/plan.json, each case's tools.json and the planned
// catalogs merged into <work>/catalog; render writes the gateway config (the
// base plus the run's connectors and mcp blocks); orgs writes one
// org config per org (<org>.json) and prints the org ids; check judges the
// evidence, writes the report and exits 1 when any row fails. mocks.json
// maps a case's server id to its mock's base URL; keys.json maps a case id
// to its {posture, audit} key ids.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/conformance"
)

// testdataCatalogs are what --catalog testdata runs: the example catalog
// and the harness's own, which adds a recording, a stdio provider and the
// placements the example lacks. Paths are relative to agentcc-gateway.
var testdataCatalogs = []struct{ label, dir string }{
	{"cn1-example", "internal/connectorcatalog/manifest/testdata/valid"},
	{"l3-fixtures", "internal/connectorcatalog/conformance/testdata/catalog"},
}

func main() {
	if len(os.Args) < 2 {
		fail(errors.New("usage: l3conformance plan|render|orgs|check [flags]"))
	}
	var err error
	switch os.Args[1] {
	case "plan":
		err = cmdPlan(os.Args[2:])
	case "render":
		err = cmdRender(os.Args[2:])
	case "orgs":
		err = cmdOrgs(os.Args[2:])
	case "check":
		err = cmdCheck(os.Args[2:])
	default:
		err = fmt.Errorf("unknown command %q (want plan, render, orgs or check)", os.Args[1])
	}
	if err != nil {
		fail(err)
	}
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "l3conformance:", err)
	os.Exit(2)
}

func cmdPlan(args []string) error {
	fs := flag.NewFlagSet("plan", flag.ContinueOnError)
	catalog := fs.String("catalog", "", "testdata, or a catalog directory")
	gwDir := fs.String("gateway-dir", ".", "the agentcc-gateway directory relative paths start from")
	work := fs.String("work", "", "the run's work directory")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *catalog == "" || *work == "" {
		return errors.New("plan needs --catalog and --work")
	}
	var cats []conformance.Catalog
	if *catalog == "testdata" {
		for _, c := range testdataCatalogs {
			cats = append(cats, conformance.Catalog{Label: c.label, FS: os.DirFS(filepath.Join(*gwDir, c.dir))})
		}
	} else {
		dir := *catalog
		if !filepath.IsAbs(dir) {
			dir = filepath.Join(*gwDir, dir)
		}
		if st, err := os.Stat(dir); err != nil || !st.IsDir() {
			return fmt.Errorf("--catalog %s is not a directory", *catalog)
		}
		cats = append(cats, conformance.Catalog{Label: filepath.Base(dir), FS: os.DirFS(dir)})
	}
	plan, err := conformance.Build(cats)
	if err != nil {
		return err
	}
	// The gateway loads the planned catalogs as one directory
	// (connectors.catalog_dir, written into the config by render).
	if err := conformance.WriteCatalog(cats, conformance.CatalogDir(*work)); err != nil {
		return err
	}
	for _, cs := range plan.Cases {
		if cs.Skip != "" {
			fmt.Printf("case %s: skipped whole (%s)\n", cs.ID, cs.Skip)
			continue
		}
		dir := conformance.FixturesDir(*work, cs.ServerID)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(dir, "tools.json"), cs.Fixture, 0o644); err != nil {
			return err
		}
		fmt.Printf("case %s: server %s, %s, %d tools, org %s\n", cs.ID, cs.ServerID, cs.Transport, len(cs.Tools), cs.Org)
	}
	return writeJSON(filepath.Join(*work, "plan.json"), plan)
}

func readPlan(work string) (*conformance.Plan, error) {
	var plan conformance.Plan
	if err := readJSON(filepath.Join(work, "plan.json"), &plan); err != nil {
		return nil, err
	}
	return conformance.Rehydrate(&plan)
}

func cmdRender(args []string) error {
	fs := flag.NewFlagSet("render", flag.ContinueOnError)
	work := fs.String("work", "", "the run's work directory")
	mocksFile := fs.String("mocks", "", "mocks.json")
	base := fs.String("base", "", "the gateway config template without an mcp block")
	out := fs.String("out", "", "where to write the rendered template")
	if err := fs.Parse(args); err != nil {
		return err
	}
	plan, err := readPlan(*work)
	if err != nil {
		return err
	}
	mocks := map[string]string{}
	if err := readJSON(*mocksFile, &mocks); err != nil {
		return err
	}
	block, err := conformance.RenderMCP(plan, mocks, *work)
	if err != nil {
		return err
	}
	tmpl, err := os.ReadFile(*base)
	if err != nil {
		return err
	}
	return os.WriteFile(*out, append(append(tmpl, '\n'), block...), 0o644)
}

func cmdOrgs(args []string) error {
	fs := flag.NewFlagSet("orgs", flag.ContinueOnError)
	work := fs.String("work", "", "the run's work directory")
	mocksFile := fs.String("mocks", "", "mocks.json")
	keysFile := fs.String("keys", "", "keys.json")
	out := fs.String("out", "", "the directory for <org>.json")
	if err := fs.Parse(args); err != nil {
		return err
	}
	plan, err := readPlan(*work)
	if err != nil {
		return err
	}
	mocks := map[string]string{}
	keys := map[string]conformance.Keys{}
	if err := readJSON(*mocksFile, &mocks); err != nil {
		return err
	}
	if err := readJSON(*keysFile, &keys); err != nil {
		return err
	}
	orgs, err := conformance.OrgConfigs(plan, mocks, keys)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(*out, 0o755); err != nil {
		return err
	}
	ids := make([]string, 0, len(orgs))
	for org, raw := range orgs {
		if err := os.WriteFile(filepath.Join(*out, org+".json"), raw, 0o644); err != nil {
			return err
		}
		ids = append(ids, org)
	}
	sort.Strings(ids)
	for _, id := range ids {
		fmt.Println(id)
	}
	return nil
}

func cmdCheck(args []string) error {
	fs := flag.NewFlagSet("check", flag.ContinueOnError)
	work := fs.String("work", "", "the run's work directory")
	evDir := fs.String("evidence", "", "the evidence directory")
	reportFile := fs.String("report", "", "where to write report.json")
	runAt := fs.String("run-at", time.Now().UTC().Format("2006-01-02"), "the run's date")
	if err := fs.Parse(args); err != nil {
		return err
	}
	plan, err := readPlan(*work)
	if err != nil {
		return err
	}
	ev, err := conformance.LoadEvidence(*evDir, plan)
	if err != nil {
		return err
	}
	rep := conformance.Check(plan, ev, *runAt)
	if err := writeJSON(*reportFile, rep); err != nil {
		return err
	}
	rep.WriteText(os.Stdout)
	if rep.Failed() {
		os.Exit(1)
	}
	return nil
}

func readJSON(path string, v any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, v); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return nil
}

func writeJSON(path string, v any) error {
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}
