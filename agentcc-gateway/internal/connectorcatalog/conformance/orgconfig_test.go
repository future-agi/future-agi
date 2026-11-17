package conformance

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate/catalogparts"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// orgFixture is the run's mocks and keys for every running case.
func orgFixture(plan *Plan) (map[string]string, map[string]Keys) {
	mocks, keys := map[string]string{}, map[string]Keys{}
	for _, cs := range plan.Cases {
		if cs.Skip != "" {
			continue
		}
		if cs.Transport != "stdio" {
			mocks[cs.ServerID] = "http://127.0.0.1:40000"
		}
		keys[cs.ID] = Keys{Posture: "key_p_" + cs.ServerID, Audit: "key_a_" + cs.ServerID}
	}
	return mocks, keys
}

func mustOrgs(t *testing.T, plan *Plan) map[string]*tenant.OrgConfig {
	t.Helper()
	mocks, keys := orgFixture(plan)
	raw, err := OrgConfigs(plan, mocks, keys)
	if err != nil {
		t.Fatalf("OrgConfigs: %v", err)
	}
	// The gateway checks each catalog_ref against the run's catalogs, which
	// it loads as connectors.catalog_dir (CatalogDir).
	dir := CatalogDir(t.TempDir())
	if err := WriteCatalog(testdataCatalogs(), dir); err != nil {
		t.Fatal(err)
	}
	loaded, err := connectors.LoadDir(dir)
	if err != nil {
		t.Fatalf("the gateway would not load the run's catalogs: %v", err)
	}
	cat := catalogparts.FromManifest(loaded.Records)
	out := map[string]*tenant.OrgConfig{}
	for org, body := range raw {
		// The admin PUT's own decode: the generated contract, strictly.
		var contract gatewayadmin.OrgConfig
		dec := json.NewDecoder(bytes.NewReader(body))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&contract); err != nil {
			t.Fatalf("org %s: the contract refuses the config: %v\n%s", org, err, body)
		}
		var cfg tenant.OrgConfig
		if err := json.Unmarshal(body, &cfg); err != nil {
			t.Fatalf("org %s: %v", org, err)
		}
		if perr := cfg.UnappliedMCPCredentialPlacement(cat); perr != nil {
			t.Fatalf("org %s: the gateway would refuse the placement: %v", org, perr)
		}
		out[org] = &cfg
	}
	return out
}

func rule(t *testing.T, cfg *tenant.OrgConfig, slug string) *tenant.MCPAccessRule {
	t.Helper()
	vs := cfg.MCP.VirtualServers[slug]
	if vs == nil || vs.Access == nil || len(vs.Access.Rules) != 1 {
		t.Fatalf("virtual server %s has no single access rule: %+v", slug, vs)
	}
	return vs.Access.Rules[0]
}

func TestOrgConfigsPushWhatAddFromCatalogWould(t *testing.T) {
	plan := mustPlan(t)
	orgs := mustOrgs(t, plan)
	cs := caseByID(t, plan, remoteCase)
	cfg := orgs[cs.Org]
	if cfg == nil || cfg.MCP == nil || !cfg.MCP.Enabled || cfg.MCP.Access == nil || cfg.MCP.Access.Default != "deny" {
		t.Fatalf("org %s: %+v", cs.Org, cfg)
	}
	s := cfg.MCP.Servers[cs.ServerID]
	if s.URL != "http://127.0.0.1:40000/aaa/mcp" || s.Transport != "http" || s.CatalogRef != "acme/remote@1.2.0" ||
		s.URLVariables["workspace"] != "aaa" || s.Era != "" {
		t.Fatalf("server %+v", s)
	}
	if s.Credential == nil || s.Credential.Mode != "shared" || s.Credential.Secret == nil || s.Credential.Secret.Name != "X-Acme-Key" {
		t.Fatalf("credential %+v", s.Credential)
	}
	pinned := orgs[caseByID(t, plan, legacyCase).Org].MCP.Servers["acme_legacy"]
	if pinned.Era != "2024-11-05" || pinned.Transport != "sse" || pinned.Credential != nil {
		t.Fatalf("legacy server %+v", pinned)
	}
	audit := cfg.MCP.VirtualServers[cs.Audit.Slug]
	if audit.ID != cs.Audit.ID || len(audit.Access.Rules) != 0 || audit.Access.Keys[0] != "key_a_acme_remote" {
		t.Fatalf("the audit virtual server narrows nothing and admits only the audit key: %+v", audit)
	}
}

func TestOrgConfigsTranslatePosturesToAccessRules(t *testing.T) {
	plan := mustPlan(t)
	orgs := mustOrgs(t, plan)
	for _, tc := range []struct {
		caseID       string
		allow, deny  string
		matchKeyPart string
	}{
		// default auto: only the refused tools are named.
		{remoteCase, "", "acme_remote/purge_docs,acme_remote/update_doc", "key_p_acme_remote"},
		// default deny with one tool at auto: an allow list, and the refused undeclared tool.
		{"l3-fixtures/acme/search", "acme_search/search_all", "acme_search/cn8_undeclared", "key_p_acme_search"},
		// default ask and nothing allowed: every tool of the server is refused.
		{"cn1-example/close/close-mcp", "", "close_close_mcp/*", "key_p_close_close_mcp"},
		// default ask with an auto tool.
		{linearCase, "linear_linear_mcp/list_issues", "linear_linear_mcp/cn8_undeclared,linear_linear_mcp/create_issue", "key_p_linear_linear_mcp"},
	} {
		cs := caseByID(t, plan, tc.caseID)
		r := rule(t, orgs[cs.Org], cs.Posture.Slug)
		if got := strings.Join(r.Allow, ","); got != tc.allow {
			t.Errorf("%s: allow %q, want %q", tc.caseID, got, tc.allow)
		}
		if got := strings.Join(r.Deny, ","); got != tc.deny {
			t.Errorf("%s: deny %q, want %q", tc.caseID, got, tc.deny)
		}
		if r.Match == nil || len(r.Match.KeyIDs) != 1 || r.Match.KeyIDs[0] != tc.matchKeyPart {
			t.Errorf("%s: the rule matches %+v, want the posture key", tc.caseID, r.Match)
		}
	}
}

func TestOrgConfigsPutStdioCasesInTheStaticOrg(t *testing.T) {
	plan := mustPlan(t)
	static := mustOrgs(t, plan)[StaticOrg]
	if static == nil || len(static.MCP.Servers) != 0 {
		t.Fatalf("the static org pushes no server (its servers are YAML): %+v", static)
	}
	cs := caseByID(t, plan, localCase)
	posture := static.MCP.VirtualServers[cs.Posture.Slug]
	if posture.Tools == nil || strings.Join(posture.Tools.Exclude, ",") != "acme_local/env_names" {
		t.Fatalf("the posture view hides the mock's env probe: %+v", posture.Tools)
	}
	if r := rule(t, static, cs.Posture.Slug); strings.Join(r.Deny, ",") != "acme_local/delete_note" || len(r.Allow) != 0 {
		t.Fatalf("rule %+v", r)
	}
	if len(static.MCP.VirtualServers) != 4 {
		t.Fatalf("two stdio cases, two virtual servers each: %d", len(static.MCP.VirtualServers))
	}
}

func TestOrgConfigsRefuseMissingInputs(t *testing.T) {
	plan := mustPlan(t)
	mocks, keys := orgFixture(plan)
	delete(keys, remoteCase)
	if _, err := OrgConfigs(plan, mocks, keys); err == nil || !strings.Contains(err.Error(), remoteCase) {
		t.Fatalf("missing keys: %v", err)
	}
	mocks, keys = orgFixture(plan)
	delete(mocks, "acme_remote")
	if _, err := OrgConfigs(plan, mocks, keys); err == nil || !strings.Contains(err.Error(), "acme_remote") {
		t.Fatalf("missing mock: %v", err)
	}
}
