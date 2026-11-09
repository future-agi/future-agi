package inclusive

import (
	"context"
	"errors"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

type reconciliationMock struct {
	mu          sync.Mutex
	prepared    *Correction
	refund      int64
	refundCalls int
	lost        bool
	finished    bool
	spends      map[string]int64
	workspace   *string
	fields      map[string]string
	pending     bool
}

func (m *reconciliationMock) Prepared(_ context.Context, _, _ string) (*Correction, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.prepared == nil {
		return nil, nil
	}
	c := *m.prepared
	return &c, nil
}
func (m *reconciliationMock) Prepare(_ context.Context, c Correction) (Correction, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.prepared == nil {
		m.prepared = &c
	}
	return *m.prepared, nil
}
func (m *reconciliationMock) Finish(_ context.Context, c Correction) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.finished = true
	return nil
}
func (m *reconciliationMock) Refund(_ context.Context, org, req string, n int64, reason string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if org != "org" || req != "original-request" || reason == "" {
		return errors.New("wrong original identity")
	}
	if m.refund != 0 && m.refund != n {
		return errors.New("conflicting refund")
	}
	if m.refund == 0 {
		m.refund = n
		m.refundCalls++
	}
	if m.lost {
		m.lost = false
		return errors.New("lost refund acknowledgement")
	}
	return nil
}
func (m *reconciliationMock) Save(_ context.Context, rec billing.Settlement) error {
	m.pending = true
	return nil
}
func (m *reconciliationMock) SettleSpend(_ context.Context, fields map[string]string, workspace *string) error {
	if !m.pending {
		return errors.New("evidence not durable before spend")
	}
	m.fields, m.workspace = fields, workspace
	if m.spends == nil {
		m.spends = map[string]int64{}
	}
	m.spends[fields["request_id"]] = 1
	return nil
}

type generationMock struct {
	cost  string
	badID bool
}

func (m generationMock) Generation(_ context.Context, id string) (Generation, error) {
	if m.badID {
		id = "other"
	}
	return Generation{ID: id, Model: "served", CostUSD: m.cost}, nil
}
func pendingRecord() billing.Settlement {
	return billing.Settlement{Kind: billing.KindSpend, OrgID: "org", RequestID: "original-request", BilledMicros: 84000,
		APIKeyID: "key-hash", AgentID: "agent", AgentRunID: "run", CatalogSnapshotID: "snapshot-not-price-catalog", TsMs: 1,
		Dimensional: &billing.DimensionalReceipt{Contract: "openrouter-inclusive-r601-image-1", State: "estimated_pending_reconciliation",
			InclusiveAttempts: []models.InclusiveAttempt{{AttemptID: "original-request.1", Model: "requested", ServedModel: "served", GenerationID: "generation", HoldMicros: 84000, BilledMicros: 84000, Estimated: true}}}}
}
func TestR601ReconcileRefundReplayAndProjection(t *testing.T) {
	ctx := context.Background()
	rec := pendingRecord()
	m := &reconciliationMock{lost: true}
	workspace := "workspace"
	for i := 0; i < 2; i++ {
		if err := Consume(ctx, rec, &workspace, m, m); err != nil {
			t.Fatal(err)
		}
	}
	if len(m.spends) != 1 || m.workspace == nil || *m.workspace != workspace || m.fields["agent_id"] != "agent" || m.fields["agent_run_id"] != "run" || m.fields["ts_ms"] != "1" || m.fields["v"] != "1" || m.fields["price_catalog_version"] != "" || m.fields["dimensions"] != "" {
		t.Fatalf("projection=%v", m.fields)
	}
	if _, err := Reconcile(ctx, rec, generationMock{cost: "0.04"}, m, m); err == nil {
		t.Fatal("lost acknowledgement hidden")
	}
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c, err := Reconcile(ctx, rec, generationMock{cost: "100"}, m, m)
			if err != nil || c.RefundMicros != 42000 {
				t.Errorf("replay=%+v %v", c, err)
			}
		}()
	}
	wg.Wait()
	if m.refundCalls != 1 || m.refund != 42000 || !m.finished {
		t.Fatalf("refund=%d calls=%d finished=%v", m.refund, m.refundCalls, m.finished)
	}
}
func TestR601ReconciliationMissingInvalidOverHold(t *testing.T) {
	for _, tc := range []struct {
		name, cost       string
		badID, missingID bool
	}{
		{"missing_cost", "", false, false}, {"invalid_cost", "-1", false, false}, {"wrong_generation", "0.04", true, false}, {"missing_generation", "0.04", false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rec := pendingRecord()
			if tc.missingID {
				rec.Dimensional.InclusiveAttempts[0].GenerationID = ""
			}
			m := &reconciliationMock{}
			if _, err := Reconcile(context.Background(), rec, generationMock{tc.cost, tc.badID}, m, m); err == nil || m.refundCalls != 0 || m.finished {
				t.Fatal("unproven work waived or finalized")
			}
		})
	}
	m := &reconciliationMock{}
	c, err := Reconcile(context.Background(), pendingRecord(), generationMock{cost: "0.10"}, m, m)
	if err != nil || c.ShortfallMicros != 21000 || len(c.Anomalies) != 1 || c.Anomalies[0] != "requested" || m.refundCalls != 0 {
		t.Fatalf("upward correction=%+v %v", c, err)
	}
}
