package server

import (
	"fmt"
	"net/http"
	"testing"
)

// An org's managed_models.default_max_tokens_estimate is the output a
// managed request that sets no max_tokens is held for (below the model's
// own maximum). A stored value below 1024 reads as 1024; 1024 and above,
// and an unset value, read as stored.

func TestOrgDefaultMaxTokensEstimate_FloorsALowValue(t *testing.T) {
	for _, tc := range []struct {
		stored, want int
	}{
		{1, 1024},
		{500, 1024},
		{1023, 1024},
		{1024, 1024},
		{2048, 2048},
		{4096, 4096},
	} {
		t.Run(fmt.Sprint(tc.stored), func(t *testing.T) {
			stored := tc.stored
			cfg := managedOptIn()
			cfg.ManagedModels.DefaultMaxTokensEstimate = &stored
			g := newManagedTestGateway(t, cfg)
			if got := g.srv.handlers.OrgDefaultMaxTokensEstimate("org-m"); got != tc.want {
				t.Fatalf("stored %d reads %d, want %d", tc.stored, got, tc.want)
			}
		})
	}

	// Unset stays unset (0): the gateway's managed.reserve_default_output_tokens applies.
	g := newManagedTestGateway(t, managedOptIn())
	if got := g.srv.handlers.OrgDefaultMaxTokensEstimate("org-m"); got != 0 {
		t.Fatalf("an org with no estimate reads %d, want 0", got)
	}
}

// End to end: an org that stored 1 has its no-max_tokens request held for
// 1024 output tokens.
func TestManagedHold_OrgEstimateOfOneHoldsTheFloor(t *testing.T) {
	estimate := 1
	cfg := managedOptIn()
	cfg.ManagedModels.DefaultMaxTokensEstimate = &estimate
	g := newManagedTestGateway(t, cfg)
	g.grant(t, "org-m", 1_000_000)
	g.managed.status.Store(http.StatusServiceUnavailable) // keep the hold: the request fails, so it is voided
	g.credits.SetMaxOutput(func(string, string) int { return 64_000 })
	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
	if w.Code == http.StatusOK {
		t.Fatal("the failing upstream served the request")
	}
	out := g.outbox("org-m")
	if len(out) != 1 || out[0]["kind"] != "void" {
		t.Fatalf("outbox = %v, want one void", out)
	}
	var held int64
	fmt.Sscan(out[0]["hold_micros"], &held)
	// "hi" is 8 input tokens: (8 × $2 + 1024 × $10) per million × 1.05 =
	// 10,768.8 → 10,769 micros.
	if held != 10_769 {
		t.Fatalf("held %d micros, want 10769 (1024 output tokens, the floor)", held)
	}
}
