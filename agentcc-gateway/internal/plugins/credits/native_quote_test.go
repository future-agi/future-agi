package credits

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestNativeC1CreditsMissingQuoteIsUnpriced(t *testing.T) {
	for _, endpoint := range []string{"anthropic_messages", "responses", "genai_generate", "genai_stream"} {
		rc := models.AcquireRequestContext()
		rc.EndpointType, rc.Provider, rc.Model = endpoint, "synthetic", "synthetic"
		rc.Metadata["billing_mode"] = models.BillingModeManaged
		limit := 0.01
		rc.Agent.MaxCostUSD = &limit
		p := &Plugin{}
		if amount, priced := p.primaryEstimate(rc); priced || amount != 0 {
			t.Fatal("missing native quote treated as priced")
		}
		if amount, priced := p.admittedEstimate(rc, "other", "other"); priced || amount != 0 {
			t.Fatal("unsealed route admitted")
		}
		if rc.Billing.Reserved || rc.Request != nil {
			t.Fatal("estimator reserved money or created chat")
		}
		rc.Release()
	}
}
