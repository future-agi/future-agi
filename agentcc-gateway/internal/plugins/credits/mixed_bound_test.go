package credits

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestMixedBoundUsesCandidateCapsWithoutMutatingRequest(t *testing.T) {
	g := newManagedGateway(t, 10000000)
	rc := managedRequest("mixed-bound", 64)
	defer rc.Release()
	if _, ok := g.plugin.primaryEstimate(rc); !ok {
		t.Fatal("token estimate refused")
	}
	originalModel := rc.Request.Model
	rc.Request.Extra = map[string]json.RawMessage{"provider": json.RawMessage(`{"only":["synthetic"]}`), "usage": json.RawMessage(`{"include":true}`)}
	rc.Metadata["upstream_provider_object"] = string(rc.Request.Extra["provider"])
	rc.Billing.UpstreamUser = "gateway-owned-user"
	card := &models.InclusivePrice{Model: "google/gemini-3.5-flash", Prompt: "0.000001", Completion: "0.000002", Image: "0.01", ContextLimit: 1000, OutputLimit: 65536}
	rc.Billing.ReservePrice = &models.PriceCard{Inclusive: card}
	before, _ := json.Marshal(rc.Request)
	got, ok := g.plugin.admittedEstimate(rc, "openrouter", card.Model)
	candidate := *rc.Request
	candidate.Model = card.Model
	candidate.Extra = nil
	want, err := inclusive.Bound(&candidate, card)
	after, _ := json.Marshal(rc.Request)
	if !ok || err != nil || got != want || !reflect.DeepEqual(before, after) || rc.Request.Model != originalModel {
		t.Fatalf("bound=%d/%t want=%d err=%v mutated=%t", got, ok, want, err, string(before) != string(after))
	}
	cap := 128
	rc.Request.MaxTokens = &cap
	higher, ok := g.plugin.admittedEstimate(rc, "openrouter", card.Model)
	if !ok || higher <= got {
		t.Fatalf("actual enforced cap ignored: %d <= %d", higher, got)
	}
}

func TestMixedBoundNeverStripsCallerExtensions(t *testing.T) {
	for _, field := range []string{"usage", "provider", "unknown_extension"} {
		t.Run(field, func(t *testing.T) {
			g := newManagedGateway(t, 10000000)
			rc := managedRequest("caller-bound", 64)
			defer rc.Release()
			rc.Request.Extra = map[string]json.RawMessage{field: json.RawMessage(`{"unbounded":true}`)}
			_, _ = g.plugin.primaryEstimate(rc)
			// The old token hop may strip or overwrite it. Admission must still know
			// this was caller input, not just delete fields named provider or usage.
			rc.Request.Extra = map[string]json.RawMessage{"provider": json.RawMessage(`{}`), "usage": json.RawMessage(`{"include":true}`)}
			rc.Metadata["upstream_provider_object"] = "{}"
			rc.Billing.UpstreamUser = "gateway"
			rc.Billing.ReservePrice = &models.PriceCard{Inclusive: &models.InclusivePrice{Model: "google/gemini-3.5-flash", Prompt: "0.000001", Completion: "0.000002", Image: "0.01", ContextLimit: 1000}}
			if _, ok := g.plugin.admittedEstimate(rc, "openrouter", "google/gemini-3.5-flash"); ok {
				t.Fatal("caller extension accepted")
			}
		})
	}
}
