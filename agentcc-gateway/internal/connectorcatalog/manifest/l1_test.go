package manifest

import (
	"fmt"
	"strings"
	"testing"
)

func TestScopeUnionEqualsRequested(t *testing.T) {
	const linear = "apps/linear/providers/linear-mcp.yaml"
	if err := CheckL1(mustLoad(t, fixture(t))); err != nil {
		t.Fatalf("CheckL1(fixture) = %v", err)
	}
	cases := []struct {
		name, path, old, new, want string
	}{
		{"a requested scope no tool needs", linear, "scopes: [read, write]\n", "scopes: [read, write, admin]\n",
			"apps/linear/providers/linear-mcp.yaml: auth.oauth2.scopes requests admin, which no tool needs"},
		{"a tool scope not requested", linear, "      scopes: [write]\n", "      scopes: [write, admin]\n",
			"apps/linear/providers/linear-mcp.yaml: tool create_issue needs admin, which auth.oauth2.scopes does not request"},
		{"every tool declares its scopes", linear, "      scopes: [read]\n", "",
			"apps/linear/providers/linear-mcp.yaml: tool list_issues declares no scopes; an oauth2 provider's tools each list the scopes they need"},
		{"generated tools count", "apps/gmail/providers/futureagi-rest.yaml", "      - https://www.googleapis.com/auth/gmail.compose\n", "",
			"tool send_email needs https://www.googleapis.com/auth/gmail.compose, which auth.oauth2.scopes does not request"},
		{"scopes only on oauth2 providers", "apps/stripe/providers/stripe-mcp.yaml", "      posture: deny\n", "      posture: deny\n      scopes: [write]\n",
			"tool stripe_api_write lists scopes, but the provider does not use oauth2"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantError(t, CheckL1(mustLoad(t, fsys)), tc.want)
		})
	}
	t.Run("protocol scopes need no tool", func(t *testing.T) {
		fsys := fixture(t)
		edit(t, fsys, linear, "scopes: [read, write]\n", "scopes: [read, write, offline_access, openid]\n")
		if err := CheckL1(mustLoad(t, fsys)); err != nil {
			t.Fatalf("CheckL1 = %v", err)
		}
	})
	t.Run("a provider that lists no tools is not checked", func(t *testing.T) {
		cat := mustLoad(t, fixture(t))
		if p := cat.App("github").Provider("remote"); len(p.AllTools()) != 0 {
			t.Fatalf("the fixture's GitHub remote lists tools")
		}
	})
}

func TestL1ToolContract(t *testing.T) {
	const linear = "apps/linear/providers/linear-mcp.yaml"
	const gen = "apps/gmail/providers/futureagi-rest/tools.generated.json"
	cases := []struct {
		name, path, old, new, want string
	}{
		{"read-only cannot be destructive", linear, "        readOnlyHint: true\n        destructiveHint: false\n", "        readOnlyHint: true\n        destructiveHint: true\n",
			"tool list_issues is readOnlyHint and destructiveHint at once"},
		{"a write tool is not auto", linear, "      posture: ask\n      scopes: [write]\n", "      posture: auto\n      scopes: [write]\n",
			"tool create_issue writes, so its posture is ask or deny, not auto"},
		{"a destructive tool is denied by default", "apps/stripe/providers/stripe-mcp.yaml", "      posture: deny\n", "      posture: ask\n",
			"tool stripe_api_write is destructive, so its posture is deny"},
		{"input schema root is an object", gen, `"inputSchema": {
        "type": "object",
        "properties": {
          "to"`, `"inputSchema": {
        "type": "array",
        "properties": {
          "to"`, "tool send_email: inputSchema's root must have type object"},
		{"2020-12 only", gen, `"additionalProperties": false
      },
      "outputSchema"`, `"additionalProperties": false, "definitions": {}
      },
      "outputSchema"`, "tool search_threads: inputSchema uses definitions, which JSON Schema 2020-12 replaced with $defs"},
		{"declared dialect", gen, `"inputSchema": {
        "type": "object",
        "properties": {
          "q"`, `"inputSchema": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
          "q"`, "tool search_threads: inputSchema declares http://json-schema.org/draft-07/schema#; only https://json-schema.org/draft/2020-12/schema is accepted"},
		{"no remote references", gen, "\"threadId\": {\n            \"type\": \"string\",\n            \"description\": \"Reply in this thread.\"\n          }", `"threadId": {"$ref": "https://example.com/thread.json"}`,
			"tool send_email: inputSchema references https://example.com/thread.json; only local references are allowed"},
		{"a rest tool names its operation", gen, "\"operation\": {\n        \"method\": \"POST\",\n        \"path\": \"/gmail/v1/users/me/messages/send\"\n      },", "",
			"tool send_email has no operation; every tool of an operations source names its REST call"},
		{"a rest tool has a description", gen, `"description": "Send an email from the user's mailbox. to is a comma-separated list of addresses, such as alice@example.com.",`, "",
			"tool send_email has no description"},
		{"an upstream tool has no operation", linear, "      scopes: [write]\n", "      scopes: [write]\n      operation: {method: POST, path: /issues}\n",
			"tool create_issue has an operation, but only an operations source makes REST calls"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantError(t, CheckL1(mustLoad(t, fsys)), tc.path, tc.want)
		})
	}
}

func TestToolListTokenBudget(t *testing.T) {
	cat := mustLoad(t, fixture(t))
	rest := cat.App("gmail").Provider("futureagi-rest")
	if n := ToolListTokens(rest.AllTools()); n <= 0 || n > ToolListTokenBudget {
		t.Fatalf("ToolListTokens(gmail) = %d", n)
	}
	fsys := fixture(t)
	long := strings.Repeat("Search the user's mail threads. ", 40)[:999]
	var items strings.Builder
	for i := range 100 {
		fmt.Fprintf(&items, "    - {name: search_%03d, description: %q, annotations: {readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true}, operation: {method: GET, path: /gmail/v1/users/me/threads}, scopes: [\"https://www.googleapis.com/auth/gmail.readonly\"], inputSchema: {type: object, properties: {q: {type: string, description: %q}}}}\n", i, long, long)
	}
	edit(t, fsys, "apps/gmail/providers/futureagi-rest.yaml", "  generated: tools.generated.json\n", "  generated: tools.generated.json\n  items:\n"+items.String())
	wantError(t, CheckL1(mustLoad(t, fsys)), "apps/gmail/providers/futureagi-rest.yaml: tools/list is about", fmt.Sprintf("tokens; the budget is %d", ToolListTokenBudget))

	t.Run("a vendor's own server is measured, not refused", func(t *testing.T) {
		fsys := fixture(t)
		var items strings.Builder
		for i := range 100 {
			fmt.Fprintf(&items, "    - {name: big_%03d, description: %q, posture: ask, scopes: [read], annotations: {readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true}, inputSchema: {type: object, properties: {q: {type: string, description: %q}}}}\n", i, long, long)
		}
		edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "  items:\n", "  items:\n"+items.String())
		if err := CheckL1(mustLoad(t, fsys)); err != nil {
			t.Fatalf("an upstream server over the budget at tier listed: %v", err)
		}
	})
}
