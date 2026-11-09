package conformance

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

func TestRenderMCPServesStdioCasesAsYAMLServers(t *testing.T) {
	plan := mustPlan(t)
	mocks, _ := orgFixture(plan)
	port := 41000
	for _, sid := range sortedKeys(mocks) {
		port++
		mocks[sid] = "http://127.0.0.1:" + strconv.Itoa(port)
	}
	work := t.TempDir()
	block, err := RenderMCP(plan, mocks, work)
	if err != nil {
		t.Fatalf("RenderMCP: %v", err)
	}
	var doc struct {
		MCP        config.MCPConfig `yaml:"mcp"`
		Connectors struct {
			CatalogDir string `yaml:"catalog_dir"`
		} `yaml:"connectors"`
	}
	if err := yaml.Unmarshal([]byte(block), &doc); err != nil {
		t.Fatalf("the block is not YAML the gateway reads: %v\n%s", err, block)
	}
	// The gateway loads the run's catalogs (connectors.catalog_dir), so a
	// case's catalog_ref is placed by the parts its provider declares.
	if doc.Connectors.CatalogDir != CatalogDir(work) {
		t.Fatalf("connectors.catalog_dir %q, want %q", doc.Connectors.CatalogDir, CatalogDir(work))
	}
	m := doc.MCP
	if !m.Enabled || !m.Stdio.Enabled || m.Static.OrgID != StaticOrg {
		t.Fatalf("mcp %+v", m)
	}
	if len(m.Egress.PrivateAllow) == 0 || !slices.IsSorted(m.Egress.PrivateAllow) {
		t.Fatalf("egress %v", m.Egress.PrivateAllow)
	}
	if len(m.Servers) != 2 {
		t.Fatalf("servers %v: want the two stdio cases only", m.Servers)
	}
	s := m.Servers["acme_local"]
	addr, marker := StdioFiles(work, "acme_local")
	if s.Transport != "stdio" || s.Command != MockBinPlaceholder || s.Env["ACME_TOKEN"] == "" {
		t.Fatalf("acme_local %+v", s)
	}
	for _, want := range []string{"--mode", "stdio", "--fixtures", FixturesDir(work, "acme_local"), "--addr-file", addr, "--marker", marker} {
		if !slices.Contains(s.Args, want) {
			t.Errorf("acme_local args %v lack %q", s.Args, want)
		}
	}
	if s.Env["ACME_TOKEN"] != caseByID(t, plan, localCase).Credential.Env["ACME_TOKEN"] {
		t.Error("the child gets the case's test value")
	}
}

func TestRenderMCPRefusesWhatTheGatewayWouldMisread(t *testing.T) {
	plan := mustPlan(t)
	mocks, _ := orgFixture(plan)
	if _, err := RenderMCP(plan, mocks, "/work/$HOME"); err == nil || !strings.Contains(err.Error(), "$") {
		t.Fatalf("a $ in the block: %v", err)
	}
	delete(mocks, "acme_remote")
	if _, err := RenderMCP(plan, mocks, t.TempDir()); err == nil {
		t.Fatal("a case without its mock rendered")
	}
}

func TestLoadEvidenceReadsARunAndNamesWhatIsMissing(t *testing.T) {
	plan := mustPlan(t)
	dir := t.TempDir()
	cs := caseByID(t, plan, remoteCase)
	write := func(rel, body string) {
		p := filepath.Join(dir, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(evLogs, `{"logs":[{"kind":"request"},{"kind":"tool_call","tool":"acme_remote__search_docs","era":"2025-06-18"}]}`)
	write(evGatewayLog, "ok\n")
	write(filepath.Join(cs.ServerID, evListAudit), `{"tools":[{"name":"acme_remote__search_docs"}]}`)
	write(filepath.Join(cs.ServerID, evReceived), `{"received":[{"method":"initialize","era":"2025-06-18","auth":{"present":true,"sha256":"ab"}}]}`)
	write(filepath.Join(cs.ServerID, evCalls, "acme_remote__search_docs.json"), `{"result":{}}`)
	ev, err := LoadEvidence(dir, plan)
	if err != nil {
		t.Fatalf("LoadEvidence: %v", err)
	}
	if len(ev.Records) != 1 || ev.Records[0].Tool != "acme_remote__search_docs" {
		t.Fatalf("records %+v: only tool_call records are kept", ev.Records)
	}
	ce := ev.Cases[remoteCase]
	if len(ce.ListAudit) != 1 || len(ce.Received) != 1 || !ce.Received[0].Auth.Present || len(ce.Answers) != 1 {
		t.Fatalf("case evidence %+v", ce)
	}
	if !ce.missing[evListPosture] || !ce.missing[evKeys] || ce.missing[evListAudit] {
		t.Fatalf("missing %v", ce.missing)
	}
	if !slices.Contains(ev.Missing, filepath.Join(cs.ServerID, evKeys)) || !slices.Contains(ev.Missing, filepath.Join("acme_local", evEnv)) {
		t.Fatalf("Missing %v", ev.Missing)
	}
	if _, ok := ev.Artifacts[filepath.Join(dir, evGatewayLog)]; !ok {
		t.Fatal("every file is kept for the never-logged row")
	}
	write(filepath.Join(cs.ServerID, evKeys), `{not json`)
	if _, err := LoadEvidence(dir, plan); err == nil {
		t.Fatal("unreadable evidence loaded")
	}
}

func TestReportWriteTextGroupsSkipsAndCounts(t *testing.T) {
	plan := mustPlan(t)
	rep := Check(plan, goodEvidence(t, plan), "2026-10-20")
	var b bytes.Buffer
	rep.WriteText(&b)
	out := b.String()
	for _, want := range []string{"L3 PASS: 13 cases (12 pass, 0 fail, 1 skipped whole)", "Skipped rows, by what each waits on:",
		"M26 (approvals, R8)", "case l3-fixtures/acme/remote (acme/remote@1.2.0): PASS"} {
		if !strings.Contains(out, want) {
			t.Errorf("the report lacks %q", want)
		}
	}
	ev := goodEvidence(t, plan)
	ev.Cases[remoteCase].ListAudit = nil
	b.Reset()
	Check(plan, ev, "2026-10-20").WriteText(&b)
	if !strings.Contains(b.String(), "L3 FAIL:") || !strings.Contains(b.String(), "FAIL  list.through_virtual_server") {
		t.Errorf("a failing report:\n%s", b.String())
	}
	raw, err := json.Marshal(rep)
	if err != nil || !strings.Contains(string(raw), `"fixtures_sha256"`) {
		t.Fatalf("the JSON report carries quality.conformance's fields: %v", err)
	}
}

func TestRehydrateRestoresDefinitionsAndResults(t *testing.T) {
	plan := mustPlan(t)
	raw, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	var back Plan
	if err := json.Unmarshal(raw, &back); err != nil {
		t.Fatal(err)
	}
	if _, err := Rehydrate(&back); err != nil {
		t.Fatalf("Rehydrate: %v", err)
	}
	orig := toolByName(t, caseByID(t, plan, remoteCase), "search_docs")
	got := toolByName(t, caseByID(t, &back, remoteCase), "search_docs")
	if !sameJSON(orig.result, got.result) || !sameJSON(orig.def, got.def) {
		t.Fatalf("rehydrated %s / %s, want %s / %s", got.def, got.result, orig.def, orig.result)
	}
	if rep := Check(&back, goodEvidence(t, &back), "2026-10-20"); rep.Failed() {
		t.Fatalf("a rehydrated plan judges differently:\n%s", joined(failedRows(rep)))
	}
}

// The gateway loads one catalog directory: WriteCatalog copies every catalog
// a run plans into it, and refuses two catalogs that hold the same file.
func TestWriteCatalogMergesTheRunsCatalogs(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "catalog")
	if err := WriteCatalog(testdataCatalogs(), dir); err != nil {
		t.Fatalf("WriteCatalog: %v", err)
	}
	cat, err := manifest.LoadDir(os.DirFS(dir))
	if err != nil {
		t.Fatalf("the merged catalog does not load: %v", err)
	}
	for _, app := range []string{"close", "stripe", "acme"} {
		if cat.App(app) == nil {
			t.Errorf("the merged catalog lacks %s", app)
		}
	}
	twice := filepath.Join(t.TempDir(), "catalog")
	if err := WriteCatalog(append(testdataCatalogs(), testdataCatalogs()[0]), twice); err == nil || !strings.Contains(err.Error(), "both hold") {
		t.Fatalf("two catalogs with the same app: %v", err)
	}
}
