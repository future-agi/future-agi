package server

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestMessagesRoutingBYOKToCredits(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			g := newManagedTestGateway(t, r7ManagedFallbackConfig(true))
			// BYOK to credits changes billing mode: it runs only where the node
			// allows mixed billing chains; with them off the hop is refused
			// (TestMixedHopGuardRefusesCrossModeHops, messages org chain).
			allowMixedBillingChains(g)
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			g.operator.failFor.Store("org-model", http.StatusServiceUnavailable)
			fundedAttempt := false
			g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
				if hook != hookBeginAttempt || credentialType(rc) != models.CredentialManaged {
					return
				}
				fundedAttempt = true
				if stream && (rc.Request.StreamOptions == nil || !rc.Request.StreamOptions.IncludeUsage) {
					t.Error("fallback to credit-funded stream did not request usage")
				}
			}
			w := messagesRoutingCall(g.srv, managedOrgKey, "org-model", stream, "")
			if w.Code != 200 || !fundedAttempt || g.operator.calls() != 2 || g.managed.calls() != 1 {
				t.Fatalf("BYOK -> funded fallback: %d %s; own calls=%d funded calls=%d", w.Code, w.Body, g.operator.calls(), g.managed.calls())
			}
			want := fmt.Sprintf("%.6f", 1-upstreamCost*1.05)
			if got := fmt.Sprintf("%.6f", g.ledger.BalanceUSD("org-m")); got != want {
				t.Fatalf("balance=%s, want %s after the successful fallback's charge plus 5%%", got, want)
			}
		})
	}
}
