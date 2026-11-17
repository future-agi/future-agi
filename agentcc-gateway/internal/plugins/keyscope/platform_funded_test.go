package keyscope

import (
	"net/http"
	"testing"
	"time"

	ks "github.com/futureagi/agentcc-gateway/internal/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The per-key USD cap still applies to a platform-funded key: it reads the
// call's real cost and refuses at the limit.
func TestPerKeyCapStillRefusesAPlatformFundedKey(t *testing.T) {
	store, keys := newStore(t, "1.00", "monthly")
	p := New(store, ks.NewSpend(nil))
	p.now = func() time.Time { return time.Date(2026, 11, 12, 4, 0, 0, 0, time.UTC) }
	ctx := admitted(t, store, rawBudgeted)
	call := func(cost string) *models.APIError {
		rc := rcFor(keys[rawBudgeted])
		rc.Metadata["key_billing"] = "platform_funded"
		if res := p.ProcessRequest(ctx, rc); res.Error != nil {
			return res.Error
		}
		rc.Metadata["cost"] = cost
		p.ProcessResponse(ctx, rc)
		return nil
	}
	if err := call("0.70"); err != nil {
		t.Fatalf("first call refused: %v", err)
	}
	if err := call("0.40"); err != nil {
		t.Fatalf("second call refused at $0.70 of $1.00: %v", err)
	}
	err := call("0.01")
	if err == nil {
		t.Fatal("a platform-funded key went past its $1.00 cap")
	}
	if status, code, _ := refusal(t, err); status != http.StatusPaymentRequired || code != ks.CodeBudgetExhausted {
		t.Fatalf("refusal %d %s, want 402 %s", status, code, ks.CodeBudgetExhausted)
	}
}
