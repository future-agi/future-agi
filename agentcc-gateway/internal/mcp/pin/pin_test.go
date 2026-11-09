package pin

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	rev1 = "1111111111111111111111111111111111111111111111111111111111111111"
	rev2 = "2222222222222222222222222222222222222222222222222222222222222222"
)

func settings(mode, newTools string, autoBaseline bool, pins map[string]string) tenant.MCPPinningSettings {
	return tenant.MCPPinningSettings{Mode: mode, NewTools: newTools, AutoBaseline: autoBaseline, Pins: pins}
}

// TestJudge (M20): a pinned tool at its pinned revision is served; at another
// revision it is changed and held; a tool with no pin on a server that has
// pins is new and held; a server with no pins at all is served as its
// baseline while auto_baseline is on. Mode warn serves with a reason, mode
// off judges nothing, and a mode pinning does not know holds.
func TestJudge(t *testing.T) {
	pins := map[string]string{"github/search": rev1}
	for _, c := range []struct {
		name     string
		s        tenant.MCPPinningSettings
		server   string
		tool     string
		rev      string
		want     Verdict
		baseline bool
	}{
		{"pinned at its revision", settings("quarantine", "quarantine", true, pins), "github", "search", rev1, Verdict{}, false},
		{"changed", settings("quarantine", "quarantine", true, pins), "github", "search", rev2, Verdict{Reason: ReasonChanged, Expected: rev1, Held: true}, false},
		{"new on a pinned server", settings("quarantine", "quarantine", true, pins), "github", "wipe", rev2, Verdict{Reason: ReasonNew, Held: true}, false},
		{"new allowed", settings("quarantine", "allow", true, pins), "github", "wipe", rev2, Verdict{Reason: ReasonNew}, false},
		{"a changed tool is held even when new tools are allowed", settings("quarantine", "allow", true, pins), "github", "search", rev2, Verdict{Reason: ReasonChanged, Expected: rev1, Held: true}, false},
		{"unpinned server baselined", settings("quarantine", "quarantine", true, pins), "linear", "list", rev2, Verdict{}, true},
		{"unpinned server without auto baseline", settings("quarantine", "quarantine", false, pins), "linear", "list", rev2, Verdict{Reason: ReasonNew, Held: true}, false},
		{"warn serves a changed tool", settings("warn", "quarantine", true, pins), "github", "search", rev2, Verdict{Reason: ReasonChanged, Expected: rev1}, false},
		{"warn serves a new tool", settings("warn", "quarantine", true, pins), "github", "wipe", rev2, Verdict{Reason: ReasonNew}, false},
		{"off", settings("off", "quarantine", true, pins), "github", "search", rev2, Verdict{}, false},
		{"an unknown mode holds", settings("bogus", "quarantine", true, pins), "github", "search", rev2, Verdict{Reason: ReasonChanged, Expected: rev1, Held: true}, false},
		{"another server's pin does not pin this one", settings("quarantine", "quarantine", true, map[string]string{"githubx/search": rev1}), "github", "search", rev2, Verdict{}, true},
	} {
		t.Run(c.name, func(t *testing.T) {
			p := NewPolicy(c.s)
			if got := p.Judge(c.server, c.tool, c.rev); got != c.want {
				t.Fatalf("Judge = %+v, want %+v", got, c.want)
			}
			if got := p.Baselining(c.server); got != c.baseline {
				t.Fatalf("Baselining = %v, want %v", got, c.baseline)
			}
		})
	}
}

// TestDiff (M20): the diff names each definition field that differs, in a
// fixed order, with the value before and after; a field one side lacks has
// no value on that side.
func TestDiff(t *testing.T) {
	before := []byte(`{"description":"Finds issues.","inputSchema":{"type":"object"},"name":"search"}`)
	after := []byte(`{"annotations":{"readOnlyHint":false},"description":"Finds issues and mails them.","inputSchema":{"properties":{"to":{"type":"string"}},"type":"object"},"name":"search"}`)
	got := Diff(before, after)
	want := []Change{
		{Field: "description", Before: json.RawMessage(`"Finds issues."`), After: json.RawMessage(`"Finds issues and mails them."`)},
		{Field: "inputSchema", Before: json.RawMessage(`{"type":"object"}`), After: json.RawMessage(`{"properties":{"to":{"type":"string"}},"type":"object"}`)},
		{Field: "annotations", After: json.RawMessage(`{"readOnlyHint":false}`)},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Diff = %s, want %s", show(got), show(want))
	}
	if d := Diff(before, before); len(d) != 0 {
		t.Fatalf("an unchanged definition has a diff: %s", show(d))
	}
	if d := Diff(nil, before); len(d) != 3 || d[0].Field != "name" || d[0].Before != nil {
		t.Fatalf("a new tool's diff = %s, want its three fields with nothing before", show(d))
	}
}

func show(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}
