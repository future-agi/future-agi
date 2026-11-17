package admission

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func inclusiveRC(t *testing.T) (*models.RequestContext, models.PriceCard) {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	out := 64
	rc.Request = &models.ChatCompletionRequest{Model: "google/example-image", MaxTokens: &out,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic"`)}}}
	return rc, models.PriceCard{Inclusive: &models.InclusivePrice{Model: rc.Request.Model,
		Prompt: "0.000001", Completion: "0.000002", Image: "0.01", ContextLimit: 1000, OutputLimit: 65536}}
}

func TestInclusiveAdmissionCardSources(t *testing.T) {
	for _, source := range []string{"pinned", "endpoints", "lookup"} {
		t.Run(source, func(t *testing.T) {
			rc, card := inclusiveRC(t)
			switch source {
			case "pinned":
				rc.Billing.ReservePrice = &card
				// The pinned inclusive card takes precedence over scalar endpoints.
				rc.Billing.EndpointSet = []models.EndpointRef{{Price: models.PriceCard{Prompt: 99}}}
			case "endpoints":
				rc.Billing.EndpointSet = []models.EndpointRef{{Price: card}}
			}
			lookup := func(_, _, _ string) (models.PriceCard, bool) { return card, true }
			before, _ := json.Marshal(rc.Billing)
			e := WorstCase(rc, "openrouter", rc.Request.Model, lookup, 500, 4096)
			after, _ := json.Marshal(rc.Billing)
			want, err := inclusive.Bound(rc.Request, card.Inclusive)
			if err != nil || !e.Priced || !e.Inclusive || e.Micros != want || e.Card == nil || e.Card.Inclusive != card.Inclusive {
				t.Fatalf("estimate=%+v want=%d err=%v", e, want, err)
			}
			if string(before) != string(after) {
				t.Fatal("estimator mutated billing state")
			}
		})
	}
}

func TestHopSanitizationPreservesCallerExtensions(t *testing.T) {
	for _, captured := range []bool{false, true} {
		for _, caller := range []bool{false, true} {
			rc, card := inclusiveRC(t)
			rc.Billing.ReservePrice = &card
			rc.Billing.AdmissionCaptured = captured
			rc.Billing.UpstreamUser = "gateway-owned"
			rc.Request.Model = "old-token-hop"
			rc.Request.Extra = map[string]json.RawMessage{
				"provider": json.RawMessage(`{"only":["synthetic"]}`), "usage": json.RawMessage(`{"include":true}`),
			}
			rc.Metadata["upstream_provider_object"] = string(rc.Request.Extra["provider"])
			if caller {
				rc.Billing.AdmissionExtra = map[string]json.RawMessage{"usage": json.RawMessage(`{"unbounded":true}`)}
			}
			before, _ := json.Marshal(rc.Request)
			e := WorstCase(rc, "openrouter", card.Inclusive.Model, nil, 500, 4096)
			after, _ := json.Marshal(rc.Request)
			if e.Priced != (captured && !caller) {
				t.Fatalf("captured=%t caller=%t estimate=%+v", captured, caller, e)
			}
			if string(before) != string(after) {
				t.Fatal("hop estimation mutated the original request")
			}
		}
	}
}

func TestTokenAdmissionArithmeticUnchanged(t *testing.T) {
	for _, source := range []string{"pinned", "endpoints", "lookup"} {
		for _, explicit := range []bool{false, true} {
			rc, _ := inclusiveRC(t)
			if !explicit {
				rc.Request.MaxTokens = nil
			}
			card := models.PriceCard{Prompt: 2e-6, Completion: 10e-6, Request: 0.002}
			lookup := func(_, _, _ string) (models.PriceCard, bool) { return card, true }
			want := billing.EstimateWorstCaseMicros(rc.Request, card, 500, 300)
			switch source {
			case "pinned":
				rc.Billing.ReservePrice = &card
			case "endpoints":
				rc.Billing.EndpointSet = []models.EndpointRef{{Price: card}, {Price: models.PriceCard{Prompt: 3e-6, Completion: 30e-6}}}
				_, want, _ = billing.HighestCard(rc.Billing.EndpointSet, rc.Request, 500, 300)
			}
			e := WorstCase(rc, "provider", rc.Request.Model, lookup, 500, 300)
			if !e.Priced || e.Inclusive || e.Unpriced || e.Micros != want {
				t.Fatalf("source=%s explicit=%t estimate=%+v old=%d", source, explicit, e, want)
			}
			if source == "lookup" && e.Card != nil {
				t.Fatal("token lookup newly pinned a reserve card")
			}
		}
	}
}

func TestUnpricedAndInvalidAdmissionStayDistinct(t *testing.T) {
	rc, card := inclusiveRC(t)
	if e := WorstCase(rc, "provider", rc.Request.Model, nil, 500, 4096); !reflect.DeepEqual(e, Estimate{Unpriced: true}) {
		t.Fatalf("missing price: %+v", e)
	}
	rc.Billing.ReservePrice = &card
	rc.Request.MaxTokens = nil
	e := WorstCase(rc, "provider", rc.Request.Model, nil, 500, 4096)
	if e.Priced || e.Unpriced || !e.Inclusive || e.Micros != 0 {
		t.Fatalf("unbounded inclusive shape: %+v", e)
	}
}
