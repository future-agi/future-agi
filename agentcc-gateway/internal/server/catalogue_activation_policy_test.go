package server

import (
	"context"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCAT2OperatorLyriaExactSettlement(t *testing.T) {
	for _, tc := range []struct {
		id, cost string
		micros   int64
	}{
		{"google/lyria-3-pro-preview", "0.08", 84000},
		{"google/lyria-3-clip-preview", "0.04", 42000},
	} {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/%t", tc.id, surface, stream), func(t *testing.T) {
					g, ledger, _ := cat2OperatorGateway(t, true)
					answer := r601Response(tc.cost, stream)
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)
					body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"synthetic"}]}`, tc.id, stream)
					status := sendBillabilityBody(t, g, surface, body)
					acct, _ := ledger.Account(context.Background(), "org-m")
					if status != 200 || acct.HeldMicros != 0 || acct.SpentMicros != tc.micros {
						t.Fatalf("status=%d account=%+v want=%d", status, acct, tc.micros)
					}
				})
			}
		}
	}
}

func TestCAT2OperatorMixedBridgePolicy(t *testing.T) {
	g, _, _ := cat2OperatorGateway(t, true)
	inclusive := &models.PriceCard{Inclusive: &models.InclusivePrice{}}
	token := &models.PriceCard{Prompt: 0.001}
	for _, reverse := range []bool{false, true} {
		first, next := token, inclusive
		if reverse {
			first, next = next, first
		}
		rc := &models.RequestContext{}
		rc.AddAttempt(models.Attempt{CredentialType: models.CredentialManaged, Outcome: models.OutcomeError, Pricing: &models.AttemptPricing{ReservePrice: first}})
		rc.AddAttempt(models.Attempt{CredentialType: models.CredentialBYOK, Outcome: models.OutcomeError})
		if !g.srv.handlers.mixedCatalogueHop(rc, next) {
			t.Fatal("BYOK bridge laundered mixed chain")
		}
		if g.srv.handlers.mixedCatalogueHop(rc, first) {
			t.Fatal("same-family retry was blocked")
		}
	}
}
