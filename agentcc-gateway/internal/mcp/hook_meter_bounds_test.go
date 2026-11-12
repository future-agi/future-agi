package mcp

import (
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// The bounds a record and the meter's counters keep whatever a caller sends
// (review findings).

// TestUnresolvedToolProbesShareOneCounter: calls to tools that never
// resolve, each with a new long name, are counted under one key per org,
// so probing cannot grow the process's counters; each record still carries
// the (cut) name it asked for.
func TestUnresolvedToolProbesShareOneCounter(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	const n = 50
	for i := 0; i < n; i++ {
		h.call(t, fmt.Sprintf("probe_%d_", i)+strings.Repeat("x", 4<<10), nil)
	}
	h.call(t, "up_ok", nil)

	snap := meter.Default().Counters().Snapshot()
	if len(snap) != 2 {
		t.Fatalf("%d counter keys after %d unknown-tool probes and one call, want 2: %+v", len(snap), n, keysOf(snap))
	}
	probes, ok := meter.Default().Counters().Get(meter.Key{OrgID: "org-a", Tool: meter.UnresolvedTool})
	if !ok || probes.Calls != n || probes.Outcomes[meter.OutcomeDenied] != n {
		t.Fatalf("unresolved counter = %+v (found %v), want %d denied calls", probes, ok, n)
	}
	if resolved, _ := meter.Default().Counters().Get(meter.Key{OrgID: "org-a", ServerID: "up", Tool: "up_ok"}); resolved.Calls != 1 {
		t.Fatalf("the resolved call's counter = %+v, want 1 call", resolved)
	}

	recs := h.sink.records(t)
	if len(recs) != n+1 {
		t.Fatalf("%d records, want %d", len(recs), n+1)
	}
	if want := meter.CapName("probe_7_" + strings.Repeat("x", 4<<10)); recs[7].Tool != want || recs[7].ErrorCode != meter.CodeUnknownTool {
		t.Fatalf("probe 7 recorded as tool of %d chars, code %s; want the requested name cut to %d", len(recs[7].Tool), recs[7].ErrorCode, meter.MaxNameRunes)
	}
}

// TestUnknownToolCodeCountsUnresolvedEvenWithAServer: if hook 6 sets the
// server before the tool lookup fails, the unknown_tool code still keeps the
// caller's name out of the counter keys.
func TestUnknownToolCodeCountsUnresolvedEvenWithAServer(t *testing.T) {
	k := counterKey(ToolCallRecord{OrgID: "org-a", ServerID: "up", Tool: "up_guess_1", ErrorCode: meter.CodeUnknownTool})
	if k != (meter.Key{OrgID: "org-a", Tool: meter.UnresolvedTool}) {
		t.Fatalf("counter key = %+v, want org-a's unresolved key", k)
	}
	k = counterKey(ToolCallRecord{OrgID: "org-a", ServerID: "up", Tool: "up_ok", ErrorCode: "tool_denied"})
	if k != (meter.Key{OrgID: "org-a", ServerID: "up", Tool: "up_ok"}) {
		t.Fatalf("a resolved tool's denial counted as %+v", k)
	}
}

// TestRecordCapsCallerStrings: a 300-character tool name (two-byte runes,
// with a NUL) and a 40-character protocol version are cut to the record's column
// lengths on a rune boundary, NUL replaced, and the call is still recorded
// once as an unknown-tool denial.
func TestRecordCapsCallerStrings(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(func(hook string, cc *CallContext) {
		attribute("full")(hook, cc)
		if hook == hookParseAgent {
			cc.ProtocolVersion = strings.Repeat("9", 40)
		}
	})
	name := "up_\x00" + strings.Repeat("é", 296) // 300 runes
	h.call(t, name, nil)
	rec := h.sink.only(t)
	if n := utf8.RuneCountInString(rec.Tool); n != meter.MaxNameRunes || !utf8.ValidString(rec.Tool) {
		t.Fatalf("tool is %d runes (valid %v), want %d", n, utf8.ValidString(rec.Tool), meter.MaxNameRunes)
	}
	if strings.Contains(rec.Tool, "\x00") || !strings.HasPrefix(rec.Tool, "up_�") {
		t.Fatalf("tool kept its NUL: %q", rec.Tool[:8])
	}
	if strings.Contains(rec.ErrorMessage, "\x00") || utf8.RuneCountInString(rec.ErrorMessage) > meter.MaxErrorMessage {
		t.Fatalf("error message has a NUL or is over %d characters", meter.MaxErrorMessage)
	}
	if rec.ProtocolVersion != strings.Repeat("9", maxProtocolVersionRunes) {
		t.Fatalf("protocol_version = %q, want 16 characters", rec.ProtocolVersion)
	}
	if rec.Outcome != OutcomeDenied || rec.ErrorCode != meter.CodeUnknownTool || rec.ServerID != "" {
		t.Fatalf("long-name probe recorded as %s/%s server %q", rec.Outcome, rec.ErrorCode, rec.ServerID)
	}
}

// TestRecordErrorMessageRedacted: upstream error text goes through the org's
// redactor after the credential scrub, as the record's arguments and result
// do, so a person named in it is not kept under content mode full.
func TestRecordErrorMessageRedacted(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	email := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "email", Pattern: `[a-z]+@[a-z]+\.example`}})
	t.Cleanup(meter.Install(meter.New(meter.Options{Redactor: func(org string) *privacy.Redactor {
		if org == "org-a" {
			return email
		}
		return nil
	}})))
	h.call(t, "up_pii", nil)
	rec := h.sink.only(t)
	if rec.Outcome != OutcomeError || rec.ErrorMessage == "" {
		t.Fatalf("pii call recorded as %s with message %q", rec.Outcome, rec.ErrorMessage)
	}
	if strings.Contains(rec.ErrorMessage, "bob@corp.example") || !strings.Contains(rec.ErrorMessage, "[REDACTED:email]") {
		t.Fatalf("error message not redacted by the org's redactor: %q", rec.ErrorMessage)
	}
	if strings.Contains(rec.ErrorMessage, "sk-fake-test-value-0001") {
		t.Fatalf("error message lost its credential scrub: %q", rec.ErrorMessage)
	}
}

func keysOf(snap []meter.Count) []meter.Key {
	out := make([]meter.Key, 0, len(snap))
	for _, c := range snap {
		c.Tool = meter.CapRunes(c.Tool, 24)
		out = append(out, c.Key)
	}
	return out
}
