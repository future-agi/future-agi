package server

import (
	"encoding/json"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// GET /-/mcp/tools?org_id= sends each tool's description and input schema as
// the org's upstream listed them, and its version and deprecation from the
// gateway's tool_versions config; a tool that has none sends none of the
// keys, and one org's details never reach another org's list.
func TestAdminToolsRouteCarriesToolDetails(t *testing.T) {
	a := &adminUpstream{listed: []string{
		`{"name":"search","description":"Search code.","inputSchema":{"type":"object","properties":{"q":{"type":"string"}},"required":["q"]}}`,
		`{"name":"plain","inputSchema":{}}`,
	}}
	b := &adminUpstream{listed: []string{`{"name":"search","inputSchema":{"type":"object"}}`, `{"name":"only_b","inputSchema":{"type":"object"}}`}}
	srv := newTenancyServer(t, map[string]*adminUpstream{"http://a.example": a, "http://b.example": b})
	putOrg(t, srv, "org-a", orgWithGithub("http://a.example", ""))
	putOrg(t, srv, "org-b", orgWithGithub("http://b.example", ""))
	srv.MCP().Registry().SetToolVersions(map[string]mcp.ToolVersionInfo{
		"github__search": {Version: "1.2", Deprecated: true, DeprecationMessage: "Use search_v2", ReplacedBy: "github__search_v2"},
		// Only org-b has this tool; only org-b's list may show this entry.
		"github__only_b": {Version: "3.0", Deprecated: true, DeprecationMessage: "Org-b only"},
	})

	rows := func(org string) map[string]map[string]any {
		t.Helper()
		w := serve(srv, "GET", "/-/mcp/tools?org_id="+org, "", "Bearer mcp-admin")
		if w.Code != http.StatusOK {
			t.Fatalf("GET tools for %s = %d %s", org, w.Code, w.Body.String())
		}
		var body struct {
			Tools []map[string]any `json:"tools"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatalf("tools for %s: %s", org, w.Body.String())
		}
		var whole any
		_ = json.Unmarshal(w.Body.Bytes(), &whole)
		if errs := schematest.ValidateDef(t, "../agentcc-admin.schema.json", "MCPAdminTools", whole); len(errs) > 0 {
			t.Fatalf("tools for %s fail MCPAdminTools: %v", org, errs)
		}
		out := map[string]map[string]any{}
		for _, row := range body.Tools {
			out[row["name"].(string)] = row
		}
		return out
	}

	got := rows("org-a")
	search := got["github__search"]
	wantSchema := map[string]any{"type": "object", "properties": map[string]any{"q": map[string]any{"type": "string"}}, "required": []any{"q"}}
	if search["description"] != "Search code." || !reflect.DeepEqual(search["input_schema"], wantSchema) {
		t.Fatalf("org-a search = %v", search)
	}
	if search["version"] != "1.2" || search["deprecated"] != true ||
		search["deprecation_message"] != "Use search_v2" || search["replaced_by"] != "github__search_v2" {
		t.Fatalf("org-a search version/deprecation = %v", search)
	}
	plain, ok := got["github__plain"]
	if !ok {
		t.Fatalf("org-a tools = %v", got)
	}
	for _, k := range []string{"description", "input_schema", "version", "deprecated", "deprecation_message", "replaced_by"} {
		if v, ok := plain[k]; ok {
			t.Errorf("plain carries %s=%v; a tool without it omits the key", k, v)
		}
	}

	// org-b's own search has its own schema and no description; org-a's
	// plain tool is not in its list.
	for name, row := range got {
		if name != "github__search" && name != "github__plain" {
			t.Errorf("org-a lists %s, which is not its own tool", name)
		}
		if row["version"] == "3.0" || row["deprecation_message"] == "Org-b only" {
			t.Errorf("org-a's %s carries org-b's tool_versions entry: %v", name, row)
		}
	}

	other := rows("org-b")
	if len(other) != 2 || other["github__only_b"]["version"] != "3.0" || other["github__only_b"]["deprecated"] != true {
		t.Fatalf("org-b tools = %v", other)
	}
	if _, ok := other["github__search"]["description"]; ok {
		t.Fatalf("org-b's search carries org-a's description: %v", other["github__search"])
	}
	if !reflect.DeepEqual(other["github__search"]["input_schema"], map[string]any{"type": "object"}) {
		t.Fatalf("org-b's search input_schema = %v", other["github__search"]["input_schema"])
	}
}
