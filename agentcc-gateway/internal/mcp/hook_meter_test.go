package mcp

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func orgWithContentMode(mode string) *tenant.OrgConfig {
	// Explicit fixture consent lets privacy suites exercise full capture rather
	// than passing merely because bodies were omitted. Default consent is tested
	// independently by TestTraceCaptureExplicitOrgConsent.
	return &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{ContentMode: mode, TraceContent: true}}
}

// TestRecordOnSuccessErrorDenialTimeoutCancel: one record per call, with
// the outcome each exit earns.
func TestRecordOnSuccessErrorDenialTimeoutCancel(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))

	cases := []struct {
		name, tool, key string
		want            meter.Class
	}{
		{"success", "up_ok", "", meter.Class{Outcome: OutcomeSuccess}},
		{"upstream error", "up_broken", "", meter.Class{Outcome: OutcomeError, ErrorClass: "unknown", ErrorOrigin: "upstream", ErrorCode: "upstream_error"}},
		{"ACL denial", "up_fail", "Authorization: Bearer k-deny", meter.Class{Outcome: OutcomeDenied, ErrorClass: "denied", ErrorOrigin: "gateway", ErrorCode: "tool_denied"}},
		{"timeout", "up_slow", "", meter.Class{Outcome: OutcomeTimeout, ErrorClass: "timeout", ErrorOrigin: "upstream", ErrorCode: "upstream_error"}},
	}
	for _, c := range cases {
		h.sink.reset()
		var extra []string
		if c.key != "" {
			extra = append(extra, c.key)
		}
		h.call(t, c.tool, map[string]any{"q": c.name}, extra...)
		rec := h.sink.only(t)
		got := meter.Class{Outcome: rec.Outcome, ErrorClass: string(rec.ErrorClass), ErrorOrigin: string(rec.ErrorOrigin), ErrorCode: rec.ErrorCode}
		if got != c.want {
			t.Errorf("%s: record class %+v, want %+v", c.name, got, c.want)
		}
		if rec.Tool != c.tool || rec.EventID == "" || rec.EventID != rec.ToolCallID {
			t.Errorf("%s: tool %q event %q tool_call %q", c.name, rec.Tool, rec.EventID, rec.ToolCallID)
		}
		if c.want.Outcome != OutcomeSuccess && rec.ErrorMessage == "" {
			t.Errorf("%s: no error message under content mode full", c.name)
		}
	}

	// The client goes away while the upstream is still working.
	h.sink.reset()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		h.post(t, ctx, "up_slow", map[string]any{"q": "cancel"})
	}()
	time.Sleep(meterToolTimeout / 3)
	cancel()
	<-done
	rec := h.sink.only(t)
	if rec.Outcome != OutcomeCancelled || rec.ErrorClass != "cancelled" || rec.ErrorOrigin != "client" {
		t.Fatalf("cancelled call recorded as %s/%s/%s", rec.Outcome, rec.ErrorClass, rec.ErrorOrigin)
	}
	if !rec.Dispatched {
		t.Fatal("a cancelled call that reached the upstream is not marked dispatched")
	}
}

// TestRecordErrorMessageScrubbed: an upstream error that echoes a credential
// keeps its text in the record with the credential scrubbed.
func TestRecordErrorMessageScrubbed(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	h.call(t, "up_leaky", nil)
	rec := h.sink.only(t)
	if rec.Outcome != OutcomeError || rec.ErrorMessage == "" {
		t.Fatalf("leaky call recorded as %s with message %q", rec.Outcome, rec.ErrorMessage)
	}
	// M13 scrubs an upstream error's credentials with "[redacted]" before the
	// record's own scrub (W3b merge), so either marker shows the scrub.
	if strings.Contains(rec.ErrorMessage, "sk-fake-test-value-0000") || !strings.Contains(strings.ToLower(rec.ErrorMessage), "[redacted") {
		t.Fatalf("error message not scrubbed: %q", rec.ErrorMessage)
	}
}

// TestRecordCoversDenialsFromHooks3To16: a denial at any of hooks 3 to 16
// (set as cc.Decision when the hook starts) is recorded, exactly once.
func TestRecordCoversDenialsFromHooks3To16(t *testing.T) {
	h := newMeterHarness(t)
	var targets []string
	for _, st := range toolsCallChain {
		if st.name == hookResolveTenant || len(targets) > 0 {
			targets = append(targets, st.name)
		}
		if st.name == hookScanArgs {
			break
		}
	}
	if len(targets) != 16 || targets[len(targets)-1] != hookScanArgs {
		t.Fatalf("hooks 3 to 16 are %v", targets)
	}
	for _, target := range targets {
		h.sink.reset()
		code := "test_denied_at_" + target
		h.onHook(func(hook string, cc *CallContext) {
			attribute("full")(hook, cc)
			if hook == target {
				cc.Decision = &Denial{Type: "permission_denied", Code: code, Message: "refused at " + target}
			}
		})
		resp := h.call(t, "up_ok", nil)
		rec := h.sink.only(t)
		if rec.Outcome != OutcomeDenied || rec.ErrorCode != code || rec.ErrorClass != "denied" || rec.Dispatched {
			t.Errorf("denial at %s recorded as %s/%s/%s dispatched=%v", target, rec.Outcome, rec.ErrorCode, rec.ErrorClass, rec.Dispatched)
		}
		if m := toolCallMetaOf(t, resp); m == nil || m["tool_call_id"] != rec.ToolCallID {
			t.Errorf("denial at %s: _meta tool_call %v, want the record's id %s", target, m, rec.ToolCallID)
		}
	}
}

// TestHiddenToolCallRecorded: a call to a tool the caller cannot see is
// recorded as denied unknown_tool, under the requested name, with no server.
func TestHiddenToolCallRecorded(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	resp := h.call(t, "up_does_not_exist", map[string]any{"probe": true})
	if resp.Error == nil {
		t.Fatalf("an unknown tool was answered %s", resp.Result)
	}
	rec := h.sink.only(t)
	if rec.Outcome != OutcomeDenied || rec.ErrorClass != ErrClassDenied || rec.ErrorCode != meter.CodeUnknownTool {
		t.Fatalf("hidden tool recorded as %s/%s/%s", rec.Outcome, rec.ErrorClass, rec.ErrorCode)
	}
	if rec.Tool != "up_does_not_exist" || rec.ServerID != "" || rec.UpstreamTool != "" {
		t.Fatalf("hidden tool recorded as tool %q server %q upstream %q", rec.Tool, rec.ServerID, rec.UpstreamTool)
	}
	if rec.OrgID != "org-a" || rec.APIKeyID != "key_7f3a" {
		t.Fatalf("probing record lost its attribution: org %q key %q", rec.OrgID, rec.APIKeyID)
	}
}

// TestIsErrorNotSuccess: an isError result is outcome is_error.
func TestIsErrorNotSuccess(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	resp := h.call(t, "up_fail", nil)
	if resp.Error != nil {
		t.Fatalf("isError call answered a JSON-RPC error %v", resp.Error)
	}
	rec := h.sink.only(t)
	if rec.Outcome != OutcomeIsError || !rec.IsError || rec.ErrorClass == "" || rec.ErrorOrigin != "upstream" {
		t.Fatalf("isError result recorded as outcome %s is_error %v class %q origin %q", rec.Outcome, rec.IsError, rec.ErrorClass, rec.ErrorOrigin)
	}
	if !rec.Dispatched {
		t.Fatal("an executed call is not dispatched")
	}
	if c, ok := meter.Default().Counters().Get(meter.Key{OrgID: "org-a", ServerID: "up", Tool: "up_fail"}); !ok || c.Outcomes[OutcomeIsError] < 1 || c.Outcomes[OutcomeSuccess] != 0 {
		t.Fatalf("counters %+v did not count the call as is_error", c)
	}
}

// TestRecordAttribution: principal, agent, ids, server and session (as a
// hash) are on the record; no session id, key or token is.
func TestRecordAttribution(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(func(hook string, cc *CallContext) {
		attribute("full")(hook, cc)
		switch hook {
		case hookResolvePrincipal:
			cc.Principal.UserID, cc.Principal.ClientID = "user_42", "client_9"
		case hookParseAgent:
			cc.ToolCallID = "tc_FIXED"
			cc.GenerationID = "req_gen_1"
			cc.Agent.ParentRunID, cc.Agent.RootRunID, cc.Agent.TaskID = "run_R0", "run_R0", "task_7"
			cc.Agent.TraceID, cc.Agent.Workflow, cc.Agent.SessionID = "trace_1", "deep-research", "sess_9c1d"
		case hookResolveVirtualServer:
			cc.VirtualServer = "vs_01M4ZEB9E0J3AADR85Z4T4428X"
		}
	})
	resp := h.call(t, "up_ok", map[string]any{"q": "x"})
	rec := h.sink.only(t)
	want := map[string]string{
		"org_id": rec.OrgID, "api_key_id": rec.APIKeyID, "principal_kind": rec.PrincipalKind, "user_id": rec.UserID,
		"client_id": rec.ClientID, "tool_call_id": rec.ToolCallID, "event_id": rec.EventID, "generation_id": rec.GenerationID,
		"virtual_server_id": rec.VirtualServerID, "server_id": rec.ServerID, "tool": rec.Tool, "upstream_tool": rec.UpstreamTool,
		"run_id": rec.Agent.RunID, "parent_run_id": rec.Agent.ParentRunID, "agent_id": rec.Agent.AgentID, "step": rec.Agent.Step,
		"workflow": rec.Agent.Workflow, "framework": rec.Agent.Framework, "billing_mode": rec.BillingMode,
	}
	expect := map[string]string{
		"org_id": "org-a", "api_key_id": "key_7f3a", "principal_kind": "key", "user_id": "user_42", "client_id": "client_9",
		"tool_call_id": "tc_FIXED", "event_id": "tc_FIXED", "generation_id": "req_gen_1",
		"virtual_server_id": "vs_01M4ZEB9E0J3AADR85Z4T4428X", "server_id": "up", "tool": "up_ok", "upstream_tool": "ok",
		"run_id": "run_R1", "parent_run_id": "run_R0", "agent_id": "researcher", "step": "tool", "workflow": "deep-research",
		"framework": "omega", "billing_mode": "byok",
	}
	for k, v := range expect {
		if want[k] != v {
			t.Errorf("record %s = %q, want %q", k, want[k], v)
		}
	}
	if rec.Agent.Turn == nil || *rec.Agent.Turn != 4 || rec.Agent.SessionID != "sess_9c1d" {
		t.Errorf("agent turn %v session %q", rec.Agent.Turn, rec.Agent.SessionID)
	}
	sum := sha256.Sum256([]byte(h.session))
	if rec.SessionHash != hex.EncodeToString(sum[:]) {
		t.Errorf("session_hash %q is not the session id's sha256", rec.SessionHash)
	}
	raw, _ := json.Marshal(rec)
	for _, secret := range []string{h.session, "k-ok", "Bearer"} {
		if strings.Contains(string(raw), secret) {
			t.Errorf("record carries %q: %s", secret, raw)
		}
	}
	if m := toolCallMetaOf(t, resp); m == nil || m["tool_call_id"] != "tc_FIXED" {
		t.Errorf("_meta tool_call %v does not carry the call's id", m)
	}
}

// TestRecordTimings: latency is receipt to response, the upstream's share
// is its own, overhead is the rest; bytes are the arguments and result.
func TestRecordTimings(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(func(hook string, cc *CallContext) {
		attribute("full")(hook, cc)
		if hook == hookScanArgs {
			time.Sleep(30 * time.Millisecond) // gateway-side time before the upstream
		}
	})
	resp := h.call(t, "up_ok", map[string]any{"text": "hello"})
	rec := h.sink.only(t)
	if rec.LatencyMs < 30 || rec.UpstreamLatencyMs < 0 || rec.UpstreamLatencyMs > rec.LatencyMs {
		t.Fatalf("latency %d ms, upstream %d ms", rec.LatencyMs, rec.UpstreamLatencyMs)
	}
	if rec.GatewayOverheadMs != rec.LatencyMs-rec.UpstreamLatencyMs || rec.GatewayOverheadMs < 30 {
		t.Fatalf("overhead %d ms, want latency %d - upstream %d (at least 30)", rec.GatewayOverheadMs, rec.LatencyMs, rec.UpstreamLatencyMs)
	}
	if rec.BytesIn != int64(len(`{"text":"hello"}`)) {
		t.Fatalf("bytes_in %d, want the arguments' %d", rec.BytesIn, len(`{"text":"hello"}`))
	}
	if want := int64(len(`{"content":[{"type":"text","text":"done"}],"_meta":{"up":1}}`)); rec.BytesOut != want {
		t.Fatalf("bytes_out %d, want the result's %d", rec.BytesOut, want)
	}
	if !rec.StartedAt.Before(time.UnixMilli(rec.TsMs).Add(time.Millisecond)) || rec.StartedAt.Location() != time.UTC {
		t.Fatalf("started_at %v is not before ts_ms %d, or not UTC", rec.StartedAt, rec.TsMs)
	}
	if m := toolCallMetaOf(t, resp); m == nil || int64(m["latency_ms"].(float64)) != rec.LatencyMs {
		t.Fatalf("_meta latency %v differs from the record's %d", m, rec.LatencyMs)
	}

	// A call that never reached the upstream has no upstream share.
	h.sink.reset()
	h.call(t, "up_ok", nil, "Authorization: Bearer k-deny")
	h.sink.reset()
	h.call(t, "up_fail", nil, "Authorization: Bearer k-deny")
	if rec := h.sink.only(t); rec.UpstreamLatencyMs != 0 || rec.GatewayOverheadMs != rec.LatencyMs || rec.BytesOut != 0 {
		t.Fatalf("denied call: upstream %d overhead %d latency %d bytes_out %d", rec.UpstreamLatencyMs, rec.GatewayOverheadMs, rec.LatencyMs, rec.BytesOut)
	}
}

// TestContentModeFullMetadataNone: full keeps arguments and result
// (redacted, capped), metadata and none keep neither, names are kept in
// every mode, and a call with no org config keeps nothing.
func TestContentModeFullMetadataNone(t *testing.T) {
	h := newMeterHarness(t)
	args := map[string]any{"text": "hello"}

	for _, c := range []struct {
		name, org, header, key, want string
	}{
		{"org full", "full", "", "", "full"},
		{"org metadata", "metadata", "", "", "metadata"},
		{"org none", "none", "", "", "none"},
		{"no org config", "", "", "", "none"},
		{"key log_content none", "full", "", "none", "none"},
		{"header metadata", "full", "x-agentcc-log-content: metadata", "", "metadata"},
		{"header cannot loosen", "metadata", "x-agentcc-log-content: full", "", "metadata"},
	} {
		h.sink.reset()
		h.onHook(attribute(c.org))
		h.mu.Lock()
		h.keyModes["key_7f3a"] = c.key
		h.mu.Unlock()
		var extra []string
		if c.header != "" {
			extra = append(extra, c.header)
		}
		h.call(t, "up_ok", args, extra...)
		rec := h.sink.only(t)
		if rec.ContentMode != c.want {
			t.Errorf("%s: content_mode %s, want %s", c.name, rec.ContentMode, c.want)
		}
		kept := len(rec.Arguments) > 0 || len(rec.Result) > 0
		if kept != (c.want == "full") {
			t.Errorf("%s (%s): arguments %s result %s", c.name, rec.ContentMode, rec.Arguments, rec.Result)
		}
		if c.want == "full" && (string(rec.Arguments) != `{"text":"hello"}` || !strings.Contains(string(rec.Result), `"done"`)) {
			t.Errorf("%s: full kept arguments %s result %s", c.name, rec.Arguments, rec.Result)
		}
		if rec.Tool != "up_ok" || rec.ServerID != "up" || rec.UpstreamTool != "ok" {
			t.Errorf("%s: names not kept: %q %q %q", c.name, rec.Tool, rec.ServerID, rec.UpstreamTool)
		}
	}

	// Under none an upstream error keeps its class, not its text.
	h.sink.reset()
	h.onHook(attribute("none"))
	h.call(t, "up_broken", args)
	if rec := h.sink.only(t); rec.ErrorMessage != "" || rec.ErrorClass == "" {
		t.Fatalf("none kept error text %q (class %q)", rec.ErrorMessage, rec.ErrorClass)
	}

	// Full content is capped at 16 KiB.
	h.sink.reset()
	h.onHook(attribute("full"))
	h.mu.Lock()
	h.keyModes = map[string]string{}
	h.mu.Unlock()
	h.call(t, "up_big", args)
	if rec := h.sink.only(t); len(rec.Result) == 0 || len(rec.Result) > meter.MaxContentBytes || !strings.Contains(string(rec.Result), `"truncated":true`) {
		t.Fatalf("big result kept as %d bytes", len(rec.Result))
	}
}

// TestRecordMatchesGolden: every kind of record validates against M2's
// mcp-tool-call-v1 schema, and the golden itself does.
func TestRecordMatchesGolden(t *testing.T) {
	h := newMeterHarness(t)
	// up_ok with the "deny" argument is refused at hook 10, after the tool
	// and its server are resolved. (At the PIN today's ACL refuses before
	// hook 6 resolves the server, so its record has no server_id; see
	// TestHiddenToolCallRecorded and integrator request IR-1.)
	h.onHook(func(hook string, cc *CallContext) {
		attribute("full")(hook, cc)
		switch {
		case hook == hookResolveVirtualServer:
			cc.VirtualServer = "vs_01M4ZEB9E0J3AADR85Z4T4428X"
		case hook == hookCheckApproval && strings.Contains(string(cc.Args), "deny"):
			cc.Decision = &Denial{Type: "approval_required", Code: "approval_required", Message: "needs approval"}
		}
	})
	for _, tool := range []string{"up_ok", "up_fail", "up_broken", "up_slow", "up_big"} {
		for _, arg := range []string{tool, "deny"} {
			h.sink.reset()
			h.call(t, tool, map[string]any{"q": arg})
			assertGolden(t, h.sink.only(t))
		}
	}

	// A record with no agent context and no org config still conforms.
	h.sink.reset()
	h.onHook(func(hook string, cc *CallContext) {
		if hook == hookResolvePrincipal {
			cc.Principal = Principal{Kind: PrincipalKey, KeyID: "key_1", OrgID: "org-a"}
		}
		if hook == hookParseAgent {
			cc.Era, cc.ProtocolVersion = "2026-07-28", "2026-07-28"
		}
	})
	h.call(t, "up_ok", nil)
	rec := h.sink.only(t)
	assertGolden(t, rec)
	if w := wireRecord(t, rec); w["error_class"] != nil || w["error_origin"] != nil {
		t.Fatalf("a success record carries error_class %v / error_origin %v", w["error_class"], w["error_origin"])
	}
}

// TestOneRecordPerCall: n calls make n records, each id once, each matching
// the id the answer carries.
func TestOneRecordPerCall(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	tools := []string{"up_ok", "up_fail", "up_broken", "up_ok", "up_does_not_exist", "up_ok"}
	var answered []string
	for _, tool := range tools {
		if m := toolCallMetaOf(t, h.call(t, tool, nil)); m != nil {
			answered = append(answered, m["tool_call_id"].(string))
		}
	}
	recs := h.sink.records(t)
	if len(recs) != len(tools) {
		t.Fatalf("%d calls made %d records", len(tools), len(recs))
	}
	seen := map[string]bool{}
	for i, rec := range recs {
		if rec.Tool != tools[i] || rec.EventID != rec.ToolCallID || seen[rec.EventID] || !strings.HasPrefix(rec.EventID, "tc_") {
			t.Fatalf("record %d: tool %q event %q tool_call %q (seen %v)", i, rec.Tool, rec.EventID, rec.ToolCallID, seen[rec.EventID])
		}
		seen[rec.EventID] = true
	}
	for _, id := range answered {
		if !seen[id] {
			t.Fatalf("answer carried tool_call_id %s, which no record has", id)
		}
	}
	if len(answered) < 4 {
		t.Fatalf("only %d answers carried the evidence block", len(answered))
	}
}

// TestRecordsUnderConcurrentCalls: concurrent calls each get exactly one
// record and leave no open record cell (run with -race).
func TestRecordsUnderConcurrentCalls(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	const n = 24
	tools := []string{"up_ok", "up_fail", "up_does_not_exist", "up_broken"}
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			h.post(t, context.Background(), tools[i%len(tools)], map[string]any{"i": i})
		}(i)
	}
	wg.Wait()
	recs := h.sink.records(t)
	ids := map[string]bool{}
	for _, rec := range recs {
		ids[rec.ToolCallID] = true
	}
	if len(recs) != n || len(ids) != n {
		t.Fatalf("%d concurrent calls made %d records with %d distinct ids", n, len(recs), len(ids))
	}
	if left := openRecordCount(); left != 0 {
		t.Fatalf("%d record cells left open", left)
	}
}

// TestRecordFinalizerObservesToolStats: Deps.ToolStats sees each finished
// call exactly once, denials included, with the call's own context.
func TestRecordFinalizerObservesToolStats(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))
	h.call(t, "up_ok", nil)
	h.call(t, "up_fail", nil, "Authorization: Bearer k-deny")
	h.call(t, "up_does_not_exist", nil)
	seen, recs := h.stats.seen(), h.sink.records(t)
	if len(seen) != 3 || len(recs) != 3 {
		t.Fatalf("ToolStats observed %d calls, %d records; want 3 each", len(seen), len(recs))
	}
	for i := range seen {
		if seen[i].ToolCallID != recs[i].ToolCallID {
			t.Fatalf("ToolStats saw call %s, record %d is %s", seen[i].ToolCallID, i, recs[i].ToolCallID)
		}
	}
	if seen[1].Decision == nil || seen[2].Decision == nil {
		t.Fatal("ToolStats did not see the denials' decisions")
	}
}

// TestRecordCarriesCallContextEvidence: the five PC.6 evidence entries
// become the record's fields; any other name is not emitted.
func TestRecordCarriesCallContextEvidence(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(func(hook string, cc *CallContext) {
		attribute("full")(hook, cc)
		if hook == hookScanArgs {
			cc.SetEvidence("identity", json.RawMessage(`{"actor_chain":[{"agent_id":"planner","run_id":"run_R1"}],"source":"runtime"}`))
			cc.SetEvidence("account_label", "work")
			cc.SetEvidence("policy", map[string]any{"rule": "policy:read-only", "action": "allow", "shadow": false})
			cc.SetEvidence("flow", map[string]any{"state_before": map[string]any{}, "decision": "allow"})
			cc.SetEvidence("shaped", map[string]any{"original_bytes": 900, "returned_bytes": 100, "spilled": true})
			cc.SetEvidence("budget_hold", map[string]any{"micros": 5})
		}
	})
	h.call(t, "up_ok", nil)
	rec := h.sink.only(t)
	assertGolden(t, rec)
	w := wireRecord(t, rec)
	for _, name := range []string{"identity", "account_label", "policy", "flow", "shaped"} {
		if w[name] == nil {
			t.Errorf("evidence %s not on the record", name)
		}
	}
	if w["account_label"] != "work" || w["budget_hold"] != nil {
		t.Fatalf("account_label %v, unknown entry %v", w["account_label"], w["budget_hold"])
	}

	h.sink.reset()
	h.onHook(attribute("full"))
	h.call(t, "up_ok", nil)
	if w := wireRecord(t, h.sink.only(t)); w["identity"] != nil || w["policy"] != nil {
		t.Fatal("a call with no evidence carries evidence fields")
	}
}

// TestTermsRetentionTightensContentMode: under org content mode full, terms
// retention metadata_only or hashed drop arguments and result; the zero
// value keeps them.
func TestTermsRetentionTightensContentMode(t *testing.T) {
	h := newMeterHarness(t)
	for _, c := range []struct {
		retention TermsRetention
		want      string
	}{{"", "full"}, {RetentionFull, "full"}, {RetentionMetadataOnly, "metadata"}, {RetentionHashed, "metadata"}} {
		h.sink.reset()
		h.onHook(func(hook string, cc *CallContext) {
			attribute("full")(hook, cc)
			if hook == hookApplyTerms {
				cc.Terms.Retention = c.retention
			}
		})
		h.call(t, "up_ok", map[string]any{"q": "terms"})
		rec := h.sink.only(t)
		kept := len(rec.Arguments) > 0 && len(rec.Result) > 0
		if rec.ContentMode != c.want || kept != (c.want == "full") {
			t.Errorf("retention %q: content_mode %s, arguments %s, result %s", c.retention, rec.ContentMode, rec.Arguments, rec.Result)
		}
	}
}
