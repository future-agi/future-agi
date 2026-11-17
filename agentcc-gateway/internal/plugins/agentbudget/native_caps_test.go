package agentbudget

import (
	"context"
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestNativeC1RunRequiresVerifiedControl(t *testing.T) {
	for _, endpoint := range []string{"anthropic_messages", "responses", "genai_generate", "genai_stream"} {
		rc := models.AcquireRequestContext()
		rc.EndpointType = endpoint
		limit := 0.01
		rc.Agent.MaxCostUSD, rc.Agent.RunID = &limit, "unverified-run"
		p := New(Deps{})
		if e := p.CallCap(rc, nil); e == nil {
			t.Fatal("unverified native scope accepted")
		}
		if got := p.ProcessRequest(context.Background(), rc); got.Error == nil {
			t.Fatal("native admission skipped")
		}
		if q := p.estimate(rc, &runState{}); q.priced {
			t.Fatal("nil canonical request priced as zero")
		}
		if _, ok := p.states.Load(rc); ok {
			t.Fatal("untrusted scope acquired run state")
		}
		if rc.Request != nil {
			t.Fatal("native request projected to chat")
		}
		rc.Release()
	}
}

func TestNativeC1RunAmountsRejectInvalid(t *testing.T) {
	for _, n := range []float64{-1, math.NaN(), math.Inf(1), 1_000_000_001} {
		rc := models.AcquireRequestContext()
		rc.EndpointType = "responses"
		rc.Agent.BudgetUSD = &n
		p := New(Deps{})
		if p.CallCap(rc, nil) == nil {
			t.Fatal("invalid native budget admitted")
		}
		rc.Release()
	}
}

func TestNativeC1UnregisteredCellsStayUncounted(t *testing.T) {
	for _, endpoint := range []string{"genai_count_tokens", "genai_embed", "anthropic_count_tokens", "responses_background", "unknown"} {
		rc := models.AcquireRequestContext()
		rc.EndpointType = endpoint
		if counted(rc) {
			t.Fatal("unregistered native execution counted")
		}
		rc.Release()
	}
}
