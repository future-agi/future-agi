package mcp

import (
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
)

func hint(v bool) *bool { return &v }

// commonListedTools is scripts/scenarios/fixtures/mcp/common's listing as
// the probe reports it, in the fixture's order.
var commonListedTools = []ProbeTool{
	{Name: "echo", Title: "Echo", Description: "Returns its arguments as text.", ReadOnlyHint: hint(true)},
	{Name: "get_weather", Title: "Weather", Description: "Canned weather for a city.", ReadOnlyHint: hint(true), OpenWorldHint: hint(true)},
	{Name: "delete_file", Description: "Pretends to delete a file.", DestructiveHint: hint(true)},
}

// TestProbeReportsTheListedTools (CN-ADD): an ok probe reports the tools it
// listed, in the order listed and across pages, with each one's name, title,
// description and behaviour hints, never its input schema, and one entry per
// tool tool_count counts.
func TestProbeReportsTheListedTools(t *testing.T) {
	for _, c := range []struct {
		era, path string
		behavior  mcpmock.Behavior
	}{
		{era: "dual", path: "/mcp"},
		{era: "2025-06-18", path: "/mcp", behavior: mcpmock.Behavior{SSE: true, PageSize: 1}},
		{era: "2024-11-05", path: "/sse", behavior: mcpmock.Behavior{PageSize: 2}},
	} {
		t.Run(fmt.Sprintf("%s%s page=%d", c.era, c.path, c.behavior.PageSize), func(t *testing.T) {
			srv, m := upstream(t, c.era, nil)
			m.State().SetBehavior(c.behavior)
			s := probeServer(t, hostOf(t, srv.URL))
			code, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + c.path}))
			if code != http.StatusOK || !res.OK {
				t.Fatalf("status %d, body %s; want ok", code, raw)
			}
			// Each tool's revision is TestProbeReportsEachToolsRevision's.
			got := append([]ProbeTool(nil), res.Tools...)
			for i := range got {
				got[i].Revision = ""
			}
			if !reflect.DeepEqual(got, commonListedTools) {
				t.Errorf("tools = %+v, want %+v", got, commonListedTools)
			}
			if res.ToolCount == nil || *res.ToolCount != len(res.Tools) {
				t.Errorf("tool_count %v for %d tools", res.ToolCount, len(res.Tools))
			}
			if res.ToolsHash != commonToolsHash {
				t.Errorf("tools_hash = %s: the listing changed the hash", res.ToolsHash)
			}
			for _, field := range []string{"inputSchema", "input_schema", "outputSchema", "annotations"} {
				if strings.Contains(raw, field) {
					t.Errorf("body %s carries %s", raw, field)
				}
			}
		})
	}
}

// TestProbeToolsProjection: keys are read exactly (a duplicate keeping its
// last value), a hint counts only as the JSON literal true or false, and a
// field of the wrong type is reported empty, never guessed.
func TestProbeToolsProjection(t *testing.T) {
	raw := []json.RawMessage{
		json.RawMessage(`{"name":"read","annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}}`),
		json.RawMessage(`{"name":"quoted","annotations":{"readOnlyHint":"true","destructiveHint":null,"idempotentHint":1,"openWorldHint":{}}}`),
		json.RawMessage(`{"NAME":"upper","Annotations":{"readOnlyHint":true}}`),
		json.RawMessage(`{"name":"twice","name":"last","annotations":{"readOnlyHint":true,"readOnlyHint":false}}`),
		json.RawMessage(`{"name":7,"title":null,"description":["x"],"annotations":"readOnly"}`),
		json.RawMessage(`{"name":"bare"}`),
		json.RawMessage(`[]`),
	}
	want := []ProbeTool{
		{Name: "read", ReadOnlyHint: hint(true), DestructiveHint: hint(false), IdempotentHint: hint(true), OpenWorldHint: hint(false)},
		{Name: "quoted"},
		{},
		{Name: "last", ReadOnlyHint: hint(false)},
		{},
		{Name: "bare"},
		{},
	}
	if got := ProbeTools(raw); !reflect.DeepEqual(got, want) {
		t.Fatalf("ProbeTools = %+v, want %+v", got, want)
	}
}

// TestProbeToolsPresentOnlyWhenOK: an ok result with no tools sends an
// empty array, so a caller can tell it from a gateway built before tools;
// a failed result sends none.
func TestProbeToolsPresentOnlyWhenOK(t *testing.T) {
	n := 0
	ok, err := json.Marshal(ProbeResult{OK: true, Era: "2026-07-28", ToolCount: &n, Tools: ProbeTools(nil)})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(ok), `"tools":[]`) {
		t.Errorf("an ok result with no tools: %s, want tools []", ok)
	}
	failed, err := json.Marshal(probeFailed(ProbeErrNotMCP))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(failed), `"tools"`) {
		t.Errorf("a failed result: %s, want no tools", failed)
	}
}
