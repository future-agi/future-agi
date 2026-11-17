package mcp

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
)

// These are documentation-backed projections, not captures from hosted servers.
// Composio's public provider emits only name (= slug), description, inputSchema:
// https://github.com/ComposioHQ/composio/blob/df8a7817bbf5ba1244155fb9ea3ae5a835a65a28/ts/packages/providers/claude-agent-sdk/src/index.ts#L157
// Retrieved 2026-10-27. Its fallback description is "Execute <slug>". Parameter
// schemas are reduced to an empty object here: they are not identity fields.
func documentedToolFixtures(t *testing.T) []Tool {
	t.Helper()
	var tools []Tool
	for _, name := range []string{
		// https://docs.composio.dev/docs/sessions-via-mcp.md (retrieved 2026-10-27)
		"GMAIL_FETCH_EMAILS",
		// https://docs.composio.dev/docs/composio-connect.md (retrieved 2026-10-27)
		"COMPOSIO_SEARCH_TOOLS",
		// https://docs.composio.dev/kb/guide/toolkits-sharepoint.md (retrieved 2026-10-27)
		// Toolkit share_point is not the first underscore-separated token.
		"SHARE_POINT_SEARCH_QUERY",
		// https://docs.composio.dev/reference/sdk-reference/python/mcp.md (retrieved 2026-10-27)
		// Toolkit composio_search overlaps the meta-tool prefix above.
		"COMPOSIO_SEARCH_DUCK_DUCK_GO_SEARCH",
		// https://docs.composio.dev/docs/extending-sessions/custom-tools-and-toolkits.md (retrieved 2026-10-27)
		"LOCAL_USER_MANAGEMENT_ASSIGN_ROLE",
	} {
		raw, err := json.Marshal(map[string]any{
			"name": name, "description": "Execute " + name,
			"inputSchema": map[string]any{"type": "object", "properties": map[string]any{}},
		})
		if err != nil {
			t.Fatal(err)
		}
		tools = append(tools, pinTool(t, string(raw)))
	}
	// Both examples from https://pipedream.com/docs/connect/mcp/tool-modes.md
	// (retrieved 2026-10-27). The uppercase example is historical sub-agent mode;
	// the lowercase example is tools-only, the documented /v3 mode since 2026-04-15.
	// The sub-agent schema is verbatim; tools-only properties are reduced below.
	tools = append(tools, pinTool(t, `{
		"name":"GOOGLE_SHEETS-ADD-SINGLE-ROW",
		"description":"Add a single row of data to Google Sheets. [See the documentation](https://developers.google.com/sheets/api/reference/rest/v4/spreadsheets.values/append)",
		"inputSchema":{"type":"object","properties":{"instruction":{"type":"string"}},"required":["instruction"],"additionalProperties":false,"$schema":"http://json-schema.org/draft-07/schema#"}
	}`), pinTool(t, `{
		"name":"google_sheets-add-single-row",
		"description":"Add a single row of data to Google Sheets. [See the documentation](https://developers.google.com/sheets/api/reference/rest/v4/spreadsheets.values/append)",
		"inputSchema":{"type":"object","properties":{"sheetId":{"type":"string"},"worksheetId":{"type":"string"},"hasHeaders":{"type":"boolean"}},"required":["sheetId","worksheetId","hasHeaders"],"additionalProperties":false,"$schema":"http://json-schema.org/draft-07/schema#"}
	}`))
	return tools
}

func TestServedReadDocumentedVendorToolsRemainOther(t *testing.T) {
	_, reader, ten, _ := servedFixture(t)
	for _, tool := range documentedToolFixtures(t) {
		t.Run(tool.Name, func(t *testing.T) {
			ten.pools["org-a"].servers[0].Tools = []Tool{tool}
			got, err := reader.AdminServedTools(context.Background(), "org-a", "vs_eng", "")
			if err != nil || len(got.Tools) != 1 {
				t.Fatal("missing documented served fixture")
			}
			row := got.Tools[0]
			if row.Identity == nil || *row.Identity != (gatewayadmin.MCPToolIdentity{Platform: "other"}) {
				t.Fatal("tool name or non-identity fields established a toolkit")
			}
			if row.UpstreamName != tool.Name || row.Name != "github__"+tool.Name {
				t.Fatal("display grouping changed runtime tool names")
			}
		})
	}
}

func TestToolIdentityRejectsUnprovenNamesAndMetadata(t *testing.T) {
	// These are name-only negative probes, NOT claims about MCP wire names.
	// https://docs.composio.dev/docs/extending-sessions/custom-mcp.md documents
	// CUSTOM_ACME as a toolkit, not a tool name (retrieved 2026-10-27).
	// https://pipedream.com/docs/components/contributing/guidelines.md documents
	// linear_app-create-issue as a registry key, not a stable MCP naming contract.
	// https://pipedream.com/apps/dreamhost/integrations/resend uses
	// resend-retrieve-email in a marketing example, not reference documentation.
	// https://pipedream.com/docs/connect/components/custom-tools.md documents the
	// ~/ form for Connect APIs, not MCP. All retrieved 2026-10-27.
	for _, name := range []string{"CUSTOM_ACME", "linear_app-create-issue", "resend-retrieve-email", "~/google_sheets-update-file"} {
		t.Run(name, func(t *testing.T) {
			if got := toolIdentity(Tool{Name: name}, nil); *got != (gatewayadmin.MCPToolIdentity{Platform: "other"}) {
				t.Fatal("unproven naming convention established identity")
			}
		})
	}
	for name, tool := range map[string]Tool{
		"detached-meta":  {Meta: json.RawMessage(`{"composio":{"toolkit":{"slug":"github","name":"GitHub"}}}`)},
		"invalid-raw":    {Raw: json.RawMessage(`{`)},
		"invalid-meta":   {Meta: json.RawMessage(`{`)},
		"oversized-meta": {Meta: json.RawMessage(strings.Repeat("x", 17<<10))},
		"oversized-raw":  {Raw: json.RawMessage(strings.Repeat("x", 65<<10))},
	} {
		t.Run(name, func(t *testing.T) {
			if got := toolIdentity(tool, nil); *got != (gatewayadmin.MCPToolIdentity{Platform: "other"}) {
				t.Fatal("untrusted metadata established identity")
			}
		})
	}
}
