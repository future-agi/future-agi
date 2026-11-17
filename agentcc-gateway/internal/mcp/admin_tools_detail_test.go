package mcp

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// adminToolRows is GET /-/mcp/tools's answer as the console reads it: each
// tool's JSON object by its listed name.
func adminToolRows(t *testing.T, s *Server, org string) map[string]map[string]any {
	t.Helper()
	tools, err := s.AdminTools(org, "")
	if err != nil {
		t.Fatalf("AdminTools(%s): %v", org, err)
	}
	if errs := schematest.ValidateDef(t, "../agentcc-admin.schema.json", "MCPAdminTools", tools); len(errs) > 0 {
		t.Fatalf("%s tools fail MCPAdminTools: %v", org, errs)
	}
	b, err := json.Marshal(tools)
	if err != nil {
		t.Fatal(err)
	}
	var body struct {
		Tools []map[string]any `json:"tools"`
	}
	if err := json.Unmarshal(b, &body); err != nil {
		t.Fatal(err)
	}
	rows := map[string]map[string]any{}
	for _, row := range body.Tools {
		rows[row["name"].(string)] = row
	}
	return rows
}

var adminToolDetailKeys = []string{"description", "input_schema", "version", "deprecated", "deprecation_message", "replaced_by"}

// The admin tool list carries each tool's description and input schema as
// its upstream listed them, and its version and deprecation from the
// gateway's tool_versions config; a tool with none of them carries none of
// the keys, and another org's tool never shows.
func TestAdminToolsCarryDescriptionSchemaVersionDeprecation(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	ten, _, _ := twoOrgs()
	schema := `{"type":"object","properties":{"q":{"type":"string"}},"required":["q"]}`
	ten.pools["org-a"].servers[0].Tools = []Tool{
		{Name: "search", Description: "Search code.", InputSchema: json.RawMessage(schema),
			Raw: json.RawMessage(`{"name":"search","description":"Search code.","inputSchema":` + schema + `}`)},
		{Name: "plain", InputSchema: json.RawMessage(`{}`), Raw: json.RawMessage(`{"name":"plain","inputSchema":{}}`)},
		{Name: "legacy", Raw: json.RawMessage(`{"name":"legacy"}`)},
	}
	s.SetTenancy(ten)
	s.Registry().SetToolVersions(map[string]ToolVersionInfo{
		// Keyed by the listed name.
		"github__search": {Version: "1.2", Deprecated: true, DeprecationMessage: "Use search_v2", ReplacedBy: "github__search_v2"},
		// Keyed the way earlier configs name a tool: <server><separator><tool>.
		"github_legacy": {Version: "0.9"},
		// Only org-b has this tool; only org-b's list may show this entry.
		"github__delete_repo": {Version: "3.0", Deprecated: true, DeprecationMessage: "Archive instead", ReplacedBy: "github__archive_repo"},
		// No org has this server's tool.
		"gitlab__search": {Version: "9.9", Deprecated: true},
	})

	rows := adminToolRows(t, s, "org-a")
	search, ok := rows["github__search"]
	if !ok {
		t.Fatalf("org-a tools %v lack github__search", rows)
	}
	var wantSchema map[string]any
	_ = json.Unmarshal([]byte(schema), &wantSchema)
	if search["description"] != "Search code." || !reflect.DeepEqual(search["input_schema"], wantSchema) {
		t.Fatalf("search description/input_schema = %v / %v", search["description"], search["input_schema"])
	}
	if search["version"] != "1.2" || search["deprecated"] != true ||
		search["deprecation_message"] != "Use search_v2" || search["replaced_by"] != "github__search_v2" {
		t.Fatalf("search version/deprecation = %v", search)
	}
	for _, k := range adminToolDetailKeys {
		if v, ok := rows["github__plain"][k]; ok {
			t.Errorf("plain carries %s=%v; a tool without it omits the key", k, v)
		}
	}
	legacy := rows["github__legacy"]
	if legacy["version"] != "0.9" {
		t.Fatalf("legacy-keyed version = %v, want 0.9", legacy)
	}
	if _, ok := legacy["deprecated"]; ok {
		t.Fatalf("a tool that is not deprecated carries deprecated: %v", legacy)
	}

	// org-b lists only its own tool, without org-a's details.
	other := adminToolRows(t, s, "org-b")
	if len(other) != 1 || other["github__delete_repo"] == nil {
		t.Fatalf("org-b tools = %v", other)
	}
	if _, ok := other["github__search"]; ok {
		t.Fatal("org-b lists org-a's tool")
	}
	if _, ok := other["github__delete_repo"]["description"]; ok {
		t.Fatalf("org-b's tool carries a description it never listed: %v", other["github__delete_repo"])
	}
	if _, ok := rows["github__delete_repo"]; ok {
		t.Fatal("org-a lists org-b's tool")
	}
	if v := other["github__delete_repo"]; v["version"] != "3.0" || v["deprecated"] != true || v["replaced_by"] != "github__archive_repo" {
		t.Fatalf("org-b's own tool lacks its tool_versions entry: %v", v)
	}

	// tool_versions is gateway-wide; an org's list applies an entry only to
	// that org's own tool of that server and name, so no entry for another
	// org's tool, or for a tool no org has, shows on org-a's list.
	own := map[string]bool{"github__search": true, "github__plain": true, "github__legacy": true}
	for name, row := range rows {
		if !own[name] {
			t.Errorf("org-a lists %s, which is not its own tool", name)
		}
		if row["version"] == "3.0" || row["version"] == "9.9" || row["deprecation_message"] == "Archive instead" {
			t.Errorf("org-a's %s carries another tool's tool_versions entry: %v", name, row)
		}
	}
}
