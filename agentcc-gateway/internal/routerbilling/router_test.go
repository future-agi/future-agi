package routerbilling

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

var routers = []string{"openrouter/auto", "openrouter/auto-beta", "openrouter/pareto-code", "openrouter/bodybuilder", "typesafe/jev-router", "nvidia/switchyard"}

func testPlan(t *testing.T, id string) Plan {
	t.Helper()
	p, err := Bound(id, "frozen-snapshot", false, []Candidate{{ID: "test/served", Prompt: "0", Completion: "0.0000095"}}, Caps{InputTokens: 1, MaxTokens: 1, N: 1}, 1000)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func testGeneration() models.GenerationEvidence {
	return models.GenerationEvidence{ID: "gen-synthetic", ServedModel: "test/served", Provider: "Synthetic"}
}

func TestRouterExactMicrosPerID(t *testing.T) {
	for _, id := range routers {
		t.Run(id, func(t *testing.T) {
			p := testPlan(t, id)
			if p.HoldMicros() != 11 {
				t.Fatalf("two-stage hold = %d, want 11", p.HoldMicros())
			}
			for _, tc := range []struct {
				name, cost          string
				upstream, billed    int64
				pending, quarantine bool
			}{
				{"half-micro", "0.0000095", 10, 11, false, false},
				{"below-half-micro", "0.00000949999999999999999", 9, 9, false, false},
				{"zero", "0", 0, 0, false, false},
				{"missing", "", 10, 11, true, true},
				{"null", "null", 10, 11, true, true},
				{"negative", "-0.001", 10, 11, true, true},
				{"invalid", `"free"`, 10, 11, true, true},
				{"overflow", "1e99", 10, 11, true, true},
				{"over-hold", "0.0001", 100, 105, false, true},
			} {
				t.Run(tc.name, func(t *testing.T) {
					d, err := Decide("request.1", p, testGeneration(), tc.cost, true)
					if err != nil || d.UpstreamMicros != tc.upstream || d.BilledMicros != tc.billed || d.Pending != tc.pending || d.Quarantine != tc.quarantine {
						t.Fatalf("decision=%+v err=%v", d, err)
					}
					if d.Pending && (!d.Estimated || d.Label != PendingLabel) {
						t.Fatal("missing cost was not labelled estimated, pending reconciliation")
					}
					byok, err := Decide("request.1", p, testGeneration(), tc.cost, false)
					if err != nil || byok.BilledMicros != 0 || byok.Pending || byok.Quarantine {
						t.Fatalf("BYOK must have no credit debit, fee, or hold reconciliation: %+v %v", byok, err)
					}
				})
			}
		})
	}
}

func TestRouterHoldMaximumAndCap(t *testing.T) {
	universe := []Candidate{
		{ID: "input", Prompt: "0.0001", Completion: "0"},
		{ID: "output", Prompt: "0", Completion: "0.0002"},
		{ID: "cache", Prompt: "0.00001", Completion: "0", CacheWrite: "0.0003"},
	}
	p, err := Bound(routers[0], "snapshot", false, universe, Caps{InputTokens: 2, MaxTokens: 1, N: 1}, 630)
	if err != nil || p.HoldMicros() != 630 || p.MaximizingCard() != "cache" {
		t.Fatalf("plan=%+v err=%v", p, err)
	}
	universe[2].CacheWrite = "0"
	if p.HoldMicros() != 630 {
		t.Fatal("plan changed after catalogue mutation")
	}
	_, err = Bound(routers[0], "snapshot", false, []Candidate{{ID: "x", Prompt: "0", Completion: "0.0000095"}}, Caps{MaxTokens: 1, N: 1}, 10)
	if !errors.Is(err, ErrBound) {
		t.Fatal("cap must refuse, not truncate an 11-micro hold to 10")
	}
}

func TestRouterHoldCoversEveryBoundedCharge(t *testing.T) {
	// Every half-micro boundary through 100 micros, including markup ties.
	for half := 1; half <= 200; half++ {
		usd := fmt.Sprintf("0.%07d", half*5)
		p, err := Bound(routers[0], "snapshot", false, []Candidate{{ID: "bound", Prompt: "0", Completion: usd}}, Caps{MaxTokens: 1, N: 1}, 1000)
		if err != nil {
			t.Fatal(err)
		}
		_, billed, err := Charge(usd)
		if err != nil || billed > p.HoldMicros() {
			t.Fatalf("upper=%s billed=%d hold=%d err=%v", usd, billed, p.HoldMicros(), err)
		}
	}
}

func TestRouterFusionExpiredAndUnknownRefused(t *testing.T) {
	for _, id := range append(append([]string(nil), routers...), "openrouter/fusion", "unknown/router") {
		for _, expired := range []bool{false, true} {
			_, err := Bound(id, "snapshot", expired, []Candidate{{ID: "x", Prompt: "1", Completion: "1"}}, Caps{MaxTokens: 1, N: 1}, 2_000_000)
			if (expired || !FamilyMember(id)) != (err != nil) {
				t.Fatalf("id=%s expired=%t error=%v", id, expired, err)
			}
		}
	}
}

func testProjection(t *testing.T, ds ...Decision) Projection {
	t.Helper()
	rec := billing.Settlement{RequestID: "request", OrgID: "org", APIKeyID: "key-hash", AgentID: "agent", AgentRunID: "run", TsMs: 123, CatalogSnapshotID: "snapshot"}
	for _, d := range ds {
		cred := "byok"
		if d.Managed {
			cred = "managed"
			rec.HoldMicros += d.Plan.hold
		}
		rec.Attempts = append(rec.Attempts, billing.AttemptSummary{AttemptID: d.AttemptID, Model: d.Plan.listing, Provider: "openrouter", CredentialType: cred, Kind: "primary", Outcome: "success"})
	}
	p, err := Project(rec, ds)
	if err != nil {
		t.Fatal(err)
	}
	if p.Record.AgentID != "agent" || p.Record.AgentRunID != "run" || p.Record.APIKeyID != "key-hash" || p.Record.TsMs != 123 || p.Record.RequestID != "request" || p.Record.GenerationID != "request" {
		t.Fatal("projection dropped attribution or replaced gateway identity")
	}
	return p
}

type fakeUpstream struct {
	calls []string
	rows  map[string]GenerationCost
	err   error
}

func (f *fakeUpstream) GenerationCost(_ context.Context, id string) (GenerationCost, error) {
	f.calls = append(f.calls, id)
	return f.rows[id], f.err
}

type fakeRefund struct {
	mu              sync.Mutex
	credited, calls int64
}

func (f *fakeRefund) Refund(_ context.Context, org, request string, amount int64, _ string) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	if org != "org" || request != "request" {
		return 0, ErrEvidence
	}
	if f.credited == 0 {
		f.credited = amount
	}
	return f.credited, nil
}

func TestRouterReconcilePerID(t *testing.T) {
	for _, id := range routers {
		t.Run(id, func(t *testing.T) {
			d, _ := Decide("request.1", testPlan(t, id), testGeneration(), "", true)
			p := testProjection(t, d)
			client := &fakeUpstream{rows: map[string]GenerationCost{"gen-synthetic": {ID: "gen-synthetic", ServedModel: "test/served", CostDecimal: "0.000004"}}}
			refund := &fakeRefund{}
			for i := 0; i < 2; i++ {
				r, err := Reconcile(context.Background(), p, client, refund)
				if err != nil || r.ActualBilledMicros != 4 || r.RefundMicros != 7 || r.ShortfallMicros != 0 || len(r.Quarantine) != 1 {
					t.Fatalf("resolution=%+v err=%v", r, err)
				}
			}
			if refund.credited != 7 || client.calls[0] != "gen-synthetic" {
				t.Fatal("refund duplicated or lookup not bound to upstream generation")
			}
		})
	}
}

func TestRouterReconcileWaitsForWholeRequest(t *testing.T) {
	p := testPlan(t, routers[0])
	first, _ := Decide("request.1", p, testGeneration(), "", true)
	second := first
	second.AttemptID, second.Generation.ID = "request.2", "gen-2"
	projection := testProjection(t, first, second)
	if projection.Record.BilledMicros != 22 || len(projection.Record.Attempts) != 2 {
		t.Fatal("request projection is not one aggregate spend")
	}
	client := &fakeUpstream{rows: map[string]GenerationCost{"gen-synthetic": {ID: "gen-synthetic", ServedModel: "test/served", CostDecimal: "0"}}}
	refund := &fakeRefund{}
	if _, err := Reconcile(context.Background(), projection, client, refund); err == nil || refund.calls != 0 {
		t.Fatal("partial evidence must not consume the request's one refund")
	}
	client.rows["gen-2"] = GenerationCost{ID: "gen-2", ServedModel: "test/served", CostDecimal: "0.000001"}
	r, err := Reconcile(context.Background(), projection, client, refund)
	if err != nil || r.RefundMicros != 21 || refund.credited != 21 {
		t.Fatalf("%+v %v", r, err)
	}
}

func TestRouterLateOverHoldRecordsShortfall(t *testing.T) {
	d, _ := Decide("request.1", testPlan(t, routers[0]), testGeneration(), "", true)
	p := testProjection(t, d)
	client := &fakeUpstream{rows: map[string]GenerationCost{"gen-synthetic": {ID: "gen-synthetic", ServedModel: "test/served", CostDecimal: "0.0001"}}}
	refund := &fakeRefund{}
	r, err := Reconcile(context.Background(), p, client, refund)
	if err != nil || r.ShortfallMicros != 94 || r.RefundMicros != 0 || refund.calls != 0 || len(r.Quarantine) != 1 {
		t.Fatalf("late overshoot must not silently disappear or create another spend: %+v %v", r, err)
	}
}
