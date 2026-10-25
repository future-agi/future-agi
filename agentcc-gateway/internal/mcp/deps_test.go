package mcp

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// countingValidator refuses every call in band and counts its calls.
type countingValidator struct{ calls int }

func (v *countingValidator) Validate(*CallContext, json.RawMessage) *Denial {
	v.calls++
	return &Denial{Type: "invalid_request_error", Code: "invalid_arguments", Message: "fake validator refused"}
}

// blockingScanner blocks every result and counts its calls.
type blockingScanner struct{ results int }

func (*blockingScanner) ScanArgs(*CallContext) *Denial { return nil }
func (b *blockingScanner) ScanResult(*CallContext) *Denial {
	b.results++
	return &Denial{Type: "guardrail_error", Code: "tool_result_blocked", Message: "fake scanner blocked"}
}
func (*blockingScanner) ScanList(_ *CallContext, tools []Tool) []Tool { return tools }

func TestMCPDepsLateBound(t *testing.T) {
	c := newChainServer(t)

	// Before SetDeps the defaults apply: the built-in schema check and guard.
	if resp := decodeResponse(t, c.callTool(t)); resp.Error != nil {
		t.Fatalf("default deps: %v", resp.Error)
	}

	// Installed after the server, its session and a first call: the hooks
	// read Deps at call time, so the next call uses the new members.
	v := &countingValidator{}
	c.SetDeps(Deps{Validator: v})
	resp := decodeResponse(t, c.callTool(t))
	if v.calls != 1 || resp.Error != nil || !strings.Contains(string(resp.Result), "fake validator refused [invalid_arguments]") {
		t.Fatalf("after SetDeps: validator calls %d, answer %s %v", v.calls, resp.Result, resp.Error)
	}
	if n := len(c.up.received()); n != 1 {
		t.Fatalf("the refused call reached the upstream (%d calls in all)", n)
	}

	// Another hand-over replaces the set; unset members keep their defaults.
	b := &blockingScanner{}
	c.SetDeps(Deps{Scanner: b})
	resp = decodeResponse(t, c.callTool(t))
	if v.calls != 1 || b.results != 1 || !strings.Contains(string(resp.Result), "fake scanner blocked") {
		t.Fatalf("scanner swap: validator calls %d, scanner calls %d, answer %s", v.calls, b.results, resp.Result)
	}
	d := c.Deps()
	if d.Clock == nil || d.Events == nil || d.Credits == nil || d.Validator == nil {
		t.Fatalf("SetDeps left members unset: %+v", d)
	}
	if _, ok := d.Validator.(basicValidator); !ok {
		t.Fatalf("an unset Validator = %T, want the default basicValidator", d.Validator)
	}
}

func TestMCPExtensionDepsDefaults(t *testing.T) {
	for _, tc := range []struct {
		name string
		deps func(*Server) *Deps
	}{
		{"before SetDeps", func(s *Server) *Deps { return s.Deps() }},
		{"after an empty SetDeps", func(s *Server) *Deps { s.SetDeps(Deps{}); return s.Deps() }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := NewServer(ServerConfig{})
			d := tc.deps(s)
			cc := &CallContext{}
			result := json.RawMessage(`{"content":[]}`)

			if d.RunGuard == nil || d.RunGuard.Check(cc) != nil {
				t.Error("RunGuard is not a no-op")
			}
			if auth, handled, den := d.TokenExchanger.Exchange(cc); auth != nil || handled || den != nil {
				t.Error("TokenExchanger handles calls by default")
			}
			if dec := d.ToolPolicy.Evaluate(cc); dec.Effect != PolicyAllow || dec.RuleID != "" {
				t.Errorf("ToolPolicy = %+v, want allow", dec)
			}
			if d.FlowControl.Before(cc) != nil || d.FlowControl.After(cc, result) != nil {
				t.Error("FlowControl refuses by default")
			}
			if got := d.ResultShaper.Shape(cc, result); string(got) != string(result) {
				t.Errorf("ResultShaper changed the result: %s", got)
			}
			if _, ok := d.ResultShaper.Read(cc, "mcp://fagi/results/1"); ok {
				t.Error("ResultShaper serves a spilled result by default")
			}
			d.ToolStats.Observe(cc)
			hold, den := d.KeyLimits.Hold(cc, 1_000_000)
			if hold == nil || den != nil {
				t.Fatalf("KeyLimits.Hold = %v, %v; want a hold and no denial", hold, den)
			}
			hold.Settle(500_000)
			hold.Void()
			if d.Builtins == nil || len(d.Builtins.IDs()) != 0 {
				t.Fatal("Builtins is not an empty registry")
			}
			if err := d.Builtins.Register("fagi.web", &legacyClient{}); err != nil {
				t.Fatalf("Register: %v", err)
			}
			if err := d.Builtins.Register("fagi.web", &legacyClient{}); err == nil {
				t.Error("a second registration of the same id was accepted")
			}
			if got, ok := d.Builtins.Get("fagi.web"); !ok || got == nil || strings.Join(d.Builtins.IDs(), ",") != "fagi.web" {
				t.Error("a registered built-in server is not listed")
			}
			if auth, den := d.VendorCredentials.Resolve(cc, "pipedream"); auth != nil || den != nil {
				t.Error("VendorCredentials resolves by default")
			}
			if d.Memory.Scope(cc) != nil {
				t.Error("Memory refuses by default")
			}
			if terms := d.Terms.Resolve(cc); terms.Constrained() {
				t.Errorf("Terms = %+v, want no constraint", terms)
			}
		})
	}
}

func TestMCPDepsSafeDefaults(t *testing.T) {
	s := NewServer(ServerConfig{})
	d := s.Deps()
	cc := &CallContext{}

	// With no ledger a managed call is refused, never served unbilled.
	if _, den := d.Credits.Hold(cc, 10); den == nil || den.Code != "managed_billing_unavailable" || !den.InBand() {
		t.Fatalf("Credits default = %+v, want an in-band managed_billing_unavailable", den)
	} else if obj, _ := json.Marshal(den.Object()); !strings.Contains(string(obj), `"reason":"redis_unavailable"`) {
		t.Fatalf("Credits refusal object = %s, want metadata.reason", obj)
	}
	for _, h := range []Holder{d.SpendGuard, d.RunBudget, d.ToolBudget} {
		if hold, den := h.Hold(cc, 10); hold == nil || den != nil {
			t.Fatalf("%T refuses by default", h)
		}
	}
	if p, err := d.BearerVerifier.Verify(context.Background(), "eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln", "aud"); p != nil || err == nil {
		t.Fatal("the default verifier accepted a JWT")
	}
	if p, err := d.BearerVerifier.Verify(context.Background(), "sk-agentcc-abc", "aud"); p != nil || err != nil {
		t.Fatal("the default verifier did not leave a virtual key to key auth")
	}
	if out, err := d.Translator.ServerRequest(cc, "elicitation/create", nil); err != nil || string(out) != `{"action":"decline"}` {
		t.Fatalf("Translator elicitation = %s, %v; want decline", out, err)
	}
	if _, err := d.Translator.ServerRequest(cc, "sampling/createMessage", nil); err == nil {
		t.Fatal("Translator accepted a sampling request")
	}
	if release, den := d.RateLimiter.Admit(cc); den != nil || release == nil {
		t.Fatal("RateLimiter refuses by default")
	} else {
		release()
	}
	if d.Pins.CheckCall(cc) != nil || d.Approvals.Check(cc) != nil {
		t.Fatal("Pins or Approvals refuse by default")
	}
	if auth, den := d.CredentialResolver.Resolve(cc); auth != nil || den != nil {
		t.Fatal("the default credential resolver overrides the configured credential")
	}
	d.Tracer.Start(cc)()
	d.DecorateUpstream(cc, &Message{})
	d.Events("tool_call", ToolCallRecord{})
	if len(d.ManagedServers.ForOrg("org-1")) != 0 || d.ResourceMetadataURL != "" {
		t.Fatal("managed servers or a resource metadata URL by default")
	}
	if got := d.Clock(); time.Since(got) > time.Minute {
		t.Fatalf("Clock = %v, want now", got)
	}

	// The memory session store round-trips and expires.
	store := newMemorySessionStore()
	now := time.Unix(1_800_000_000, 0)
	store.now = func() time.Time { return now }
	ctx := context.Background()
	if err := store.Save(ctx, "s1", []byte("state"), time.Minute); err != nil {
		t.Fatal(err)
	}
	if got, ok, err := store.Load(ctx, "s1"); err != nil || !ok || string(got) != "state" {
		t.Fatalf("Load = %q %v %v", got, ok, err)
	}
	now = now.Add(time.Minute)
	if _, ok, _ := store.Load(ctx, "s1"); ok {
		t.Fatal("an expired session loaded")
	}
	_ = store.Save(ctx, "s2", []byte("x"), 0)
	_ = store.Delete(ctx, "s2")
	if _, ok, _ := store.Load(ctx, "s2"); ok {
		t.Fatal("a deleted session loaded")
	}
}

func TestDenialFromAPIErrorKeepsTheModelPathObject(t *testing.T) {
	five := 5.0
	apiErr := models.ErrLimit(402, models.ErrTypePaymentRequired, "managed_credits_insufficient", "not enough credits",
		models.PaymentMeta{LimitSource: models.LimitSourceInFlight, RequiredUSD: &five, Scope: models.ScopeOrg, RetryAfterSec: 2})
	d := DenialFromAPIError(apiErr)
	if !d.InBand() || d.Code != "managed_credits_insufficient" || d.RetryAfterSec != 2 {
		t.Fatalf("denial = %+v", d)
	}
	obj, err := json.Marshal(d.Object())
	if err != nil {
		t.Fatal(err)
	}
	want := `{"type":"payment_required","code":"managed_credits_insufficient","message":"not enough credits","param":null,"metadata":{"limit_source":"agentcc_in_flight","required_usd":5,"resets_at":null,"retry_after_s":2,"scope":"org"}}`
	if string(obj) != want {
		t.Fatalf("object = %s\nwant %s", obj, want)
	}
	if DenialFromAPIError(nil) != nil {
		t.Fatal("a nil error made a denial")
	}
}
