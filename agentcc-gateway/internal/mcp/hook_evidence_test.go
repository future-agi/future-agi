package mcp

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// TestEvidenceMetaBlockOnResultAndDenial: every answered tools/call result,
// in-band denials included, carries _meta["io.futureagi/tool_call"] in
// mcp-denial-v1's ToolCallMeta shape.
func TestEvidenceMetaBlockOnResultAndDenial(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("full"))

	// An executed call: byok, billed 0, the upstream's _meta kept.
	resp := h.call(t, "up_ok", nil)
	m := toolCallMetaOf(t, resp)
	rec := h.sink.only(t)
	if m == nil {
		t.Fatalf("result %s has no tool_call block", resp.Result)
	}
	if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "ToolCallMeta", m); len(errs) > 0 {
		t.Fatalf("tool_call block %v: %s", m, strings.Join(errs, "; "))
	}
	if m["tool_call_id"] != rec.ToolCallID || m["executed"] != true || m["billing_mode"] != "byok" || m["billed_usd"] != float64(0) {
		t.Fatalf("tool_call block %v (record %s)", m, rec.ToolCallID)
	}

	// An in-band denial: not executed, with the error object beside it.
	h.sink.reset()
	h.onHook(func(hook string, cc *CallContext) {
		attribute("full")(hook, cc)
		if hook == hookCheckApproval {
			cc.Decision = &Denial{Type: "approval_required", Code: "approval_required", Message: "needs approval"}
		}
	})
	resp = h.call(t, "up_ok", nil)
	m = toolCallMetaOf(t, resp)
	rec = h.sink.only(t)
	if m == nil || m["executed"] != false || m["tool_call_id"] != rec.ToolCallID || m["billing_mode"] != "byok" {
		t.Fatalf("denial's tool_call block %v (record %s)", m, rec.ToolCallID)
	}
	var res struct {
		IsError bool                       `json:"isError"`
		Meta    map[string]json.RawMessage `json:"_meta"`
	}
	_ = json.Unmarshal(resp.Result, &res)
	if !res.IsError || res.Meta[MetaKeyError] == nil {
		t.Fatalf("denial answer %s lost its error object", resp.Result)
	}
	if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "ToolCallMeta", m); len(errs) > 0 {
		t.Fatalf("denial's tool_call block: %s", strings.Join(errs, "; "))
	}

	// MCP billing's fields, when set, are what the block reports, and the
	// record agrees.
	h.sink.reset()
	h.onHook(func(hook string, cc *CallContext) {
		attribute("full")(hook, cc)
		if hook == hookSettleCredits {
			cc.Billing = CallBilling{Mode: BillingManaged, CredentialType: BillingManaged, UpstreamMicros: 5000,
				BilledMicros: 5250, MarkupMicros: 250, MarkupBps: 500, CostSource: "tool_price", Settled: true}
		}
	})
	m = toolCallMetaOf(t, h.call(t, "up_ok", nil))
	rec = h.sink.only(t)
	if m["billing_mode"] != "managed" || m["billed_usd"] != 0.00525 {
		t.Fatalf("managed block %v", m)
	}
	if rec.BillingMode != "managed" || rec.BilledCostMicros != 5250 || rec.UpstreamCostMicros != 5000 ||
		rec.MarkupMicros != 250 || rec.MarkupBps != 500 || rec.CostSource != "tool_price" || rec.CredentialType != "managed" {
		t.Fatalf("managed record %+v", rec)
	}
	assertGolden(t, rec)

	// A timed-out dispatched call may have run upstream: executed is true.
	h.sink.reset()
	h.onHook(attribute("full"))
	resp = h.call(t, "up_slow", nil)
	if resp.Error == nil {
		if m := toolCallMetaOf(t, resp); m == nil || m["executed"] != true {
			t.Fatalf("timed-out in-band answer's block %v", m)
		}
	}
	if rec := h.sink.only(t); !rec.Dispatched {
		t.Fatal("a timed-out call is not dispatched")
	}
}

// TestEvidenceMetaWithoutM18IsByokZero: with no billing set, the block says
// byok and 0, whatever the call did.
func TestEvidenceMetaWithoutM18IsByokZero(t *testing.T) {
	cc := &CallContext{ToolCallID: "tc_1"}
	s := NewServer(ServerConfig{})
	t.Cleanup(s.Close)
	cc.Timings.Started = s.Deps().Clock()
	if d := evidenceMeta(s, cc); d != nil {
		t.Fatalf("evidenceMeta refused: %v", d)
	}
	var m map[string]any
	if err := json.Unmarshal(cc.ResponseMeta[MetaKeyToolCall], &m); err != nil {
		t.Fatal(err)
	}
	if m["billing_mode"] != "byok" || m["billed_usd"] != float64(0) || m["executed"] != false || m["tool_call_id"] != "tc_1" {
		t.Fatalf("block %v", m)
	}
}
