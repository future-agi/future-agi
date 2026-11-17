package mcp

import (
	"encoding/json"
	"testing"
	"time"
)

// parkedPair runs the record steps of a call that parks (the translation step's
// input_required round) and of the retry that resumes it, on one server
// with a recording sink; nothing is emitted until the logical call ends.
func parkedPair(t *testing.T) (*Server, *recordSink, *CallContext, *Parked) {
	t.Helper()
	s := NewServer(ServerConfig{ToolCallTimeout: time.Second})
	t.Cleanup(s.Close)
	sink := &recordSink{}
	s.SetDeps(Deps{Events: sink.add})

	first := &CallContext{RequestID: "mcp_first", ToolCallID: "tc_parked", Method: MethodToolsCall}
	first.Principal = Principal{Kind: PrincipalKey, KeyID: "key_1", OrgID: "org-a"}
	first.Tool.ExposedName = "up_ok"
	first.Timings.Started = s.Deps().Clock()
	registerRecord(s, first)
	first.Decision = &Denial{Code: "input_required", Message: "needs input"}
	evidenceMeta(s, first)
	p := first.Park()
	first.runFinalizers() // the round that parked ends: nothing is recorded yet
	if n := len(sink.records(t)); n != 0 {
		t.Fatalf("the parking round emitted %d records", n)
	}
	return s, sink, first, p
}

// TestAdoptedCallRecordedOnceWithAdopterFields: a parked call adopted by its
// retry is recorded once, under the parked tool_call_id, with the fields of
// the call context that finished it.
func TestAdoptedCallRecordedOnceWithAdopterFields(t *testing.T) {
	s, sink, _, p := parkedPair(t)

	retry := &CallContext{RequestID: "mcp_retry", ToolCallID: "tc_retry_own", Method: MethodToolsCall, retry: true}
	retry.Principal = Principal{Kind: PrincipalKey, KeyID: "key_1", OrgID: "org-a"}
	retry.Tool.ExposedName = "up_ok"
	retry.Upstream.ServerID = "up"
	retry.Timings.Started = s.Deps().Clock()
	registerRecord(s, retry) // step 2a on the retry, before hook 7 adopts
	if !retry.Adopt(p) {
		t.Fatal("the retry could not adopt the parked call")
	}
	retry.Executed, retry.Dispatched = true, true
	retry.Result = json.RawMessage(`{"content":[{"type":"text","text":"resumed"}]}`)
	evidenceMeta(s, retry)
	retry.runFinalizers()

	rec := sink.only(t)
	if rec.ToolCallID != "tc_parked" || rec.EventID != "tc_parked" {
		t.Fatalf("recorded as %s/%s, want the parked call's id", rec.EventID, rec.ToolCallID)
	}
	if rec.RequestID != "mcp_retry" || rec.Outcome != OutcomeSuccess || rec.ServerID != "up" || !rec.Dispatched {
		t.Fatalf("record has request %q outcome %s server %q dispatched %v; want the retry's fields",
			rec.RequestID, rec.Outcome, rec.ServerID, rec.Dispatched)
	}
	var m map[string]any
	_ = json.Unmarshal(retry.ResponseMeta[MetaKeyToolCall], &m)
	if m["tool_call_id"] != "tc_parked" {
		t.Fatalf("the retry's evidence block carries %v, want the parked id", m["tool_call_id"])
	}

	// Nothing is left over for either call context.
	if n := openRecordCount(); n != 0 {
		t.Fatalf("%d record cells still open", n)
	}
	p.Expire()
	if n := len(sink.records(t)); n != 1 {
		t.Fatalf("expiry after adoption emitted again: %d records", n)
	}
}

// TestParkedCallExpiringRecordedOnce: a parked call nobody resumes is
// recorded once, when it expires, with the parking round's fields.
func TestParkedCallExpiringRecordedOnce(t *testing.T) {
	_, sink, _, p := parkedPair(t)
	p.Expire()
	p.Complete()
	rec := sink.only(t)
	if rec.ToolCallID != "tc_parked" || rec.RequestID != "mcp_first" || rec.Outcome != OutcomeDenied || rec.ErrorCode != "input_required" {
		t.Fatalf("expired call recorded as %+v", rec)
	}
	if n := openRecordCount(); n != 0 {
		t.Fatalf("%d record cells still open", n)
	}
}

// TestRetryThatCannotAdoptIsItsOwnRecord: a retry whose parked call already
// ended keeps its own record finalizer and is recorded as itself.
func TestRetryThatCannotAdoptIsItsOwnRecord(t *testing.T) {
	s, sink, _, p := parkedPair(t)
	p.Expire()
	sink.reset()

	retry := &CallContext{RequestID: "mcp_late", ToolCallID: "tc_late", Method: MethodToolsCall, retry: true}
	retry.Principal = Principal{Kind: PrincipalKey, KeyID: "key_1", OrgID: "org-a"}
	retry.Timings.Started = s.Deps().Clock()
	registerRecord(s, retry)
	if retry.Adopt(p) {
		t.Fatal("an expired parked call was adopted")
	}
	retry.Decision = &Denial{Code: "request_state_invalid", Message: "expired"}
	evidenceMeta(s, retry)
	retry.runFinalizers()
	rec := sink.only(t)
	if rec.ToolCallID != "tc_late" || rec.ErrorCode != "request_state_invalid" || rec.ErrorClass != ErrClassProtocol {
		t.Fatalf("late retry recorded as %+v", rec)
	}
}

// TestAdoptedRetryPanicBeforeStep20ClosesItsCell: a retry that adopted a
// parked call and then ended before step 20 (a panic) closes its own record
// cell, whose record finalizer Adopt dropped, so nothing stays in the
// process-wide index. The logical call is still recorded once. Without step
// 20 nothing rebinds the parked cell to the retry, so that record carries
// the parking round's fields: rebinding at adoption needs hook 7.
func TestAdoptedRetryPanicBeforeStep20ClosesItsCell(t *testing.T) {
	s, sink, _, p := parkedPair(t)

	retry := &CallContext{RequestID: "mcp_retry", ToolCallID: "tc_retry_own", Method: MethodToolsCall, retry: true}
	retry.Principal = Principal{Kind: PrincipalKey, KeyID: "key_1", OrgID: "org-a"}
	retry.Timings.Started = s.Deps().Clock()
	registerRecord(s, retry)
	if !retry.Adopt(p) {
		t.Fatal("the retry could not adopt the parked call")
	}
	retry.runFinalizers() // the chain's deferred finalizers, as a panic unwinds

	rec := sink.only(t)
	if rec.ToolCallID != "tc_parked" || rec.RequestID != "mcp_first" {
		t.Fatalf("recorded as %s from %s, want the parked call from its parking round", rec.ToolCallID, rec.RequestID)
	}
	if n := openRecordCount(); n != 0 {
		t.Fatalf("%d record cells still open after the retry ended", n)
	}
	p.Expire()
	if n := len(sink.records(t)); n != 1 {
		t.Fatalf("expiry after adoption emitted again: %d records", n)
	}
}
