package mcp

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestMCPCallContextTermsAndEvidence(t *testing.T) {
	cc := &CallContext{}
	if cc.Terms.Constrained() || (Terms{}).Constrained() {
		t.Fatal("zero Terms constrain the call")
	}
	for _, terms := range []Terms{{Retention: RetentionHashed}, {EvalExport: EvalExportForbidden}, {Attribution: "Powered by X"}} {
		if !terms.Constrained() {
			t.Errorf("%+v does not constrain the call", terms)
		}
	}

	if len(cc.Evidence) != 0 || len(cc.ResponseMeta) != 0 {
		t.Fatal("evidence or response meta present by default")
	}
	cc.SetEvidence("policy", map[string]any{"rule": "r1", "action": "deny", "shadow": false})
	cc.SetEvidence("identity", json.RawMessage(`{"sub":"u1"}`))
	cc.SetEvidence("flow", json.RawMessage(`{not json`))
	cc.SetEvidence("shaped", make(chan int))
	if got := string(cc.Evidence["policy"]); got != `{"action":"deny","rule":"r1","shadow":false}` {
		t.Fatalf("policy evidence = %s", got)
	}
	if got := string(cc.Evidence["identity"]); got != `{"sub":"u1"}` {
		t.Fatalf("raw evidence = %s, want it stored as given", got)
	}
	if _, ok := cc.Evidence["flow"]; ok {
		t.Fatal("invalid raw JSON was stored")
	}
	if _, ok := cc.Evidence["shaped"]; ok {
		t.Fatal("a value that does not marshal was stored")
	}
	if len(cc.Evidence) != 2 {
		t.Fatalf("evidence = %v, want two entries", cc.Evidence)
	}

	raw := json.RawMessage(`{"a":1}`)
	cc.SetEvidence("account_label", raw)
	raw[2] = 'X'
	if string(cc.Evidence["account_label"]) != `{"a":1}` {
		t.Fatal("stored evidence aliases the caller's buffer")
	}

	cc.SetResponseMeta(MetaKeyToolCall, map[string]any{"executed": true})
	if string(cc.ResponseMeta[MetaKeyToolCall]) != `{"executed":true}` {
		t.Fatalf("response meta = %s", cc.ResponseMeta[MetaKeyToolCall])
	}
}

// TestMCPResponseMetaReachesTheAnswer checks that what a hook puts in
// ResponseMeta is merged into the result's _meta, keeping the upstream's own
// _meta entries, and into a denial's _meta next to the error object.
func TestMCPResponseMetaReachesTheAnswer(t *testing.T) {
	c := newChainServer(t)
	replaceStep(t, toolsCallChain, hookEvidenceMeta, func(_ *Server, cc *CallContext) *Denial {
		observeHook(cc, hookEvidenceMeta)
		cc.SetResponseMeta(MetaKeyToolCall, map[string]any{"executed": cc.Executed, "billing_mode": "byok"})
		return nil
	})
	resp := decodeResponse(t, c.callTool(t))
	if !strings.Contains(string(resp.Result), `"_meta":{"io.futureagi/tool_call":{"billing_mode":"byok","executed":true}}`) ||
		!strings.Contains(string(resp.Result), `"echoed"`) {
		t.Fatalf("result = %s, want the evidence block in _meta", resp.Result)
	}

	merged, err := mergeMeta(json.RawMessage(`{"content":[],"_meta":{"upstream":1}}`), map[string]json.RawMessage{"k": json.RawMessage(`2`)})
	if err != nil || string(merged) != `{"_meta":{"k":2,"upstream":1},"content":[]}` {
		t.Fatalf("mergeMeta = %s, %v", merged, err)
	}

	replaceStep(t, toolsCallChain, hookCheckPin, func(_ *Server, cc *CallContext) *Denial {
		observeHook(cc, hookCheckPin)
		return &Denial{Type: "permission_error", Code: "tool_quarantined", Message: "tool changed"}
	})
	resp = decodeResponse(t, c.callTool(t))
	var res inBandResult
	if err := json.Unmarshal(resp.Result, &res); err != nil || !res.IsError || len(res.Meta) != 2 ||
		string(res.Meta[MetaKeyToolCall]) != `{"billing_mode":"byok","executed":false}` {
		t.Fatalf("denial = %s, want the error object and the evidence block", resp.Result)
	}
}
