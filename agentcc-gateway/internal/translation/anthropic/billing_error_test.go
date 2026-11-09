package anthropic

import (
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A 402 is Anthropic's billing_error, so Anthropic clients show billing
// guidance rather than a server failure.
func TestPaymentRequiredIsBillingError(t *testing.T) {
	tr := &Translator{}
	status, body, _ := tr.ErrorFromCanonical(models.ErrPaymentRequired(models.ErrTypePaymentRequired,
		"managed_credits_exhausted", "no credit", models.PaymentMeta{}))
	if status != http.StatusPaymentRequired || !strings.Contains(string(body), `"type":"billing_error"`) {
		t.Fatalf("%d %s", status, body)
	}
	if got := anthropicStatusForType("billing_error"); got != http.StatusPaymentRequired {
		t.Fatalf("billing_error status %d", got)
	}
}
