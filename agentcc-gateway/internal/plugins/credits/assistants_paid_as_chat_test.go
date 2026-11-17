package credits

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// paidCall is a call of the given endpoint on a managed key ($10 prepaid
// allowance), with the given billing mode and a priced answer of $0.50.
func paidCall(t *testing.T, ks *auth.KeyStore, endpoint, billingMode string) (*models.RequestContext, *auth.APIKey) {
	t.Helper()
	managed := findKey(ks, "managed")
	if managed == nil {
		t.Fatal("expected to find managed key")
	}
	rc := makeRC(managed.ID, "managed")
	rc.EndpointType = endpoint
	if billingMode != "" {
		rc.Metadata["billing_mode"] = billingMode
	}
	rc.Metadata["cost"] = "0.500000"
	return rc, managed
}

// An Assistants call is paid as a chat call is, as on dev: on the org's own
// key (billing mode byok) and on any other provider key alike, a key with its
// own prepaid allowance draws the call's priced cost.
func TestCreditsPlugin_ManagedKey_AssistantsIsPaidAsChat(t *testing.T) {
	for _, mode := range []string{models.BillingModeBYOK, ""} {
		for _, endpoint := range []string{"chat", "assistants"} {
			name := endpoint + " on the org's own key"
			if mode == "" {
				name = endpoint + " on another provider key"
			}
			t.Run(name, func(t *testing.T) {
				ks := newKeyStore()
				p := New(true, ks)
				rc, managed := paidCall(t, ks, endpoint, mode)
				defer rc.Release()
				if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
					t.Fatalf("pre-check refused a call on a key with a balance: %v", res.Error)
				}
				if res := p.ProcessResponse(context.Background(), rc); res.Error != nil {
					t.Fatalf("post-check: %v", res.Error)
				}
				if bal := managed.BalanceUSD(); bal != 9.5 {
					t.Errorf("balance = %f, want 9.5: the call's cost is drawn from the key's allowance", bal)
				}
				if got := rc.Metadata["credits_used"]; got != "0.500000" {
					t.Errorf("credits_used = %q, want 0.500000", got)
				}
			})
		}
	}
}

// A key whose allowance is empty is refused when an Assistants call is
// admitted, as it is for a chat call.
func TestCreditsPlugin_ManagedKey_AssistantsNeedsABalanceAsChatDoes(t *testing.T) {
	for _, endpoint := range []string{"chat", "assistants"} {
		t.Run(endpoint, func(t *testing.T) {
			ks := newKeyStore()
			p := New(true, ks)
			rc, managed := paidCall(t, ks, endpoint, models.BillingModeBYOK)
			defer rc.Release()
			managed.DeductMicros(auth.USDToMicros(10.0))
			res := p.ProcessRequest(context.Background(), rc)
			if res.Error == nil {
				t.Fatalf("a %s call on a key with an empty allowance was admitted", endpoint)
			}
			if res.Error.Status != http.StatusPaymentRequired {
				t.Errorf("status = %d, want 402", res.Error.Status)
			}
		})
	}
}
