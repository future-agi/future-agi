package server

import (
	"context"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func TestCAT2OperatorDeclaredMixedPlans(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, source := range []string{"request", "node"} {
			for _, reverse := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/%t", surface, source, reverse), func(t *testing.T) {
					g, ledger, _ := cat2OperatorGateway(t, true)
					primary, fallback := "openai/o1-pro", "google/lyria-3-pro-preview"
					if reverse {
						primary, fallback = fallback, primary
					}
					extra := ""
					if source == "request" {
						extra = fmt.Sprintf(`,"models":[%q]`, fallback)
					} else {
						g.srv.handlers.modelFallbacks.Store(routing.NewModelFallbacks(map[string][]string{primary: {fallback}}))
					}
					body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"synthetic"}]%s}`, primary, extra)
					status := sendBillabilityBody(t, g, surface, body)
					acct, _ := ledger.Account(context.Background(), "org-m")
					want := 403
					if surface == "/v1/messages" && source == "request" {
						// Native Messages refuses the unsupported models[] field even
						// earlier than catalogue admission; it must still do no work.
						want = 400
					}
					if status != want || g.managed.calls() != 0 || acct.HeldMicros != 0 || acct.SpentMicros != 0 || len(ledger.Outbox("org-m")) != 0 {
						t.Fatalf("status=%d calls=%d account=%+v", status, g.managed.calls(), acct)
					}
				})
			}
		}
	}
}
