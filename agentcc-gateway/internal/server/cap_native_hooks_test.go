package server

import (
	"context"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
)

func TestNativeC1FacadeClosedWithoutOwnerInputs(t *testing.T) {
	h := &Handlers{}
	f := &nativeCapFacade{handlers: h}
	h.SetNativeCapHooks(f)
	if h.nativeCaps != f {
		t.Fatal("setter did not store instance")
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "responses"
	limit := 0.1
	rc.Agent.MaxCostUSD = &limit
	if e := f.Bind(httptest.NewRequest("POST", "/v1/responses", nil), rc, nil, models.NativeResponses); e == nil {
		t.Fatal("missing identity accepted")
	}
	if a, e := f.BeforeDispatch(context.Background(), rc, nil, models.NativeRouteBinding{}, nil, nil, nil); e == nil || a != nil {
		t.Fatal("missing preparation dispatched")
	}
	if f.claimPrepared(rc, "synthetic", "synthetic") == nil {
		t.Fatal("direct native claim accepted")
	}
	if f.AfterBilling(context.Background(), rc).State == capbudget.NativeSettled {
		t.Fatal("absent ledgers reported settled")
	}
	if rc.AttemptCount() != 0 || rc.Request != nil {
		t.Fatal("closed facade manufactured attempt/chat")
	}
}

func TestNativeC1SettlementAggregation(t *testing.T) {
	for _, state := range []capbudget.NativeSettlementState{0, capbudget.NativeFenced, capbudget.NativeSettlementUnavailable} {
		got := aggregateNativeSettlement(capbudget.NativeSettlement{State: capbudget.NativeSettled}, capbudget.NativeSettlement{State: state})
		if got.State == capbudget.NativeSettled {
			t.Fatal("one failed ledger reported success")
		}
	}
}
