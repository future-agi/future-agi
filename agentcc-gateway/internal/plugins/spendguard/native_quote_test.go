package spendguard

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestNativeC1GuardMissingQuoteRefusesBeforeAdmit(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "responses"
	rc.Metadata["org_id"], rc.Metadata["billing_mode"] = "synthetic", models.BillingModeManaged
	limit := 0.01
	rc.Agent.MaxCostUSD = &limit
	p := New(nil, func(*models.RequestContext) int64 { t.Fatal("missing quote reached estimator"); return 0 })
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil || result.Error.Code != "budget_state_unavailable" {
		t.Fatal("missing quote reached guard")
	}
	if n := (&Estimator{}).Estimate(rc); n >= 0 {
		t.Fatal("unavailable quote became zero")
	}
}

func TestNativeC1GuardBYOKDoesNotAdmitMoney(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "responses"
	rc.Metadata["billing_mode"] = models.BillingModeBYOK
	limit := 0.01
	rc.Agent.MaxCostUSD = &limit
	p := New(nil, func(*models.RequestContext) int64 { t.Fatal("BYOK guard estimate"); return 0 })
	if result := p.ProcessRequest(context.Background(), rc); result.Error != nil {
		t.Fatal(result.Error.Code)
	}
}
