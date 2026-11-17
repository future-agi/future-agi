package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

// The call chain must preserve a pool's lifetime gate, not unwrap it to the
// transport after resolving a session's tools.
type credentialDispatchGuard struct{ UpstreamClient }

func (g *credentialDispatchGuard) Upstream() UpstreamClient { return g.UpstreamClient }
func (g *credentialDispatchGuard) CallTool(context.Context, string, json.RawMessage, CallOptions) (json.RawMessage, error) {
	return nil, &Denial{Type: "auth_required", Code: "auth_required", Message: "Connect your account for this server to use its tools."}
}

func TestCredentialLifecycleDispatchPreservesPoolGate(t *testing.T) {
	h := newMeterHarness(t)
	addDispatchUpstream(t, h, "lifetime", nil)
	h.s.clientsMu.RLock()
	c := h.s.clients["lifetime"]
	h.s.clientsMu.RUnlock()
	pool := &w3bPool{org: "org-a", servers: map[string]PoolServer{"lifetime": {ID: "lifetime"}}, clients: map[string]UpstreamClient{"lifetime": &credentialDispatchGuard{UpstreamClient: &legacyClient{c: c}}}}
	ctx := context.WithValue(context.Background(), tenantPoolKey{}, pool)
	ctx = context.WithValue(ctx, callViewKey{}, &callView{})
	cc := &CallContext{ctx: ctx}
	client, d := h.s.callClient(cc, "lifetime")
	if d != nil {
		t.Fatal("client resolution unexpectedly refused")
	}
	_, err := client.CallToolRaw(context.Background(), "ok", nil, CallOptions{})
	var denial *Denial
	if !errors.As(err, &denial) || denial.Code != "auth_required" {
		t.Fatal("dispatch bypassed the pool credential gate")
	}
	if got := upstreamFailure(cc, "tool call failed", err); got.Code != "auth_required" || !got.InBand() {
		t.Fatal("credential refusal lost its in-band auth_required shape")
	}
}
