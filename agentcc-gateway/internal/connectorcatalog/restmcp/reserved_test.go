package restmcp

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

func reservedTool(t *testing.T, pattern string, fixed any) (*Tool, error) {
	t.Helper()
	app, _ := gmailCopy(t)
	prop := map[string]any{"type": "string"}
	if pattern != "" {
		prop["pattern"] = pattern
	}
	if fixed != nil {
		prop["const"] = fixed
	}
	raw, _ := json.Marshal(map[string]any{"type": "object", "x-origin": "https://gmail.googleapis.com", "properties": map[string]any{"name": prop}, "required": []string{"name"}})
	return compileTool(app, manifest.Tool{Name: "get_resource", InputSchema: raw, Operation: &manifest.Operation{Method: "GET", Path: "/v2/{+name}:inspect"}}, retryPolicy{})
}

func TestReservedExpansionSegments(t *testing.T) {
	tool, err := reservedTool(t, `^spaces/[^/]+$`, nil)
	if err != nil {
		t.Fatal(err)
	}
	value, _ := json.Marshal("spaces/demo space")
	req, err := tool.buildRequest(map[string]json.RawMessage{"name": value})
	if err != nil {
		t.Fatal(err)
	}
	if req.path != "/v2/spaces/demo%20space:inspect" || len(req.query) != 0 {
		t.Fatalf("wrong placement: %#v", req)
	}
	if strings.Contains(string(tool.schema.listed), "x-") {
		t.Fatal("generator annotation listed")
	}
}

func TestReservedExpansionRefusesUnsafeValues(t *testing.T) {
	tool, err := reservedTool(t, `^spaces/[^/]+$`, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []any{"spaces/..", "spaces/.", "spaces/", "spaces//demo", "spaces/a/b", "rooms/a", "spaces/a?b", "spaces/a#b", "spaces/a\\b", "spaces/a\n", "spaces/a\x00", "spaces/a\x7f", "spaces/a\u0085", "spaces/%2f", "spaces/%2E", "spaces/%3f", "spaces/%23", "spaces/%252e%252e", true, 4} {
		t.Run(stringMustJSON(value), func(t *testing.T) {
			raw, _ := json.Marshal(value)
			if _, err := tool.buildRequest(map[string]json.RawMessage{"name": raw}); err == nil {
				t.Fatal("accepted unsafe reserved expansion")
			}
			if _, err := reservedTool(t, `^spaces/[^/]+$`, value); err == nil {
				t.Fatal("accepted unsafe fixed reserved expansion at load")
			}
		})
	}
}

func stringMustJSON(v any) string { b, _ := json.Marshal(v); return string(b) }

func TestReservedExpansionRequiresPattern(t *testing.T) {
	for _, pattern := range []string{"", "spaces/.*", "^[invalid$", "^spaces/(?=x).*$"} {
		if _, err := reservedTool(t, pattern, nil); err == nil {
			t.Errorf("accepted pattern %q", pattern)
		}
	}
}
