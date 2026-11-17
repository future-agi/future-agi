package inclusive

import (
	"context"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"testing"
)

type r602ScopedClient struct{ calls int }

func (c *r602ScopedClient) Generation(context.Context, string) (Generation, error) {
	return Generation{}, fmt.Errorf("unscoped generation lookup must not be used")
}
func (c *r602ScopedClient) GenerationFor(_ context.Context, org string, a models.InclusiveAttempt) (Generation, error) {
	if org != "org" {
		return Generation{}, fmt.Errorf("wrong organization")
	}
	c.calls++
	return Generation{ID: a.GenerationID, Model: a.ServedModel, CostUSD: "0.0000095"}, nil
}
func TestR602ReconciliationUsesAttemptIdentity(t *testing.T) {
	rec := pendingRecord()
	client := &r602ScopedClient{}
	store := &reconciliationMock{}
	if _, err := Reconcile(context.Background(), rec, client, store, store); err != nil {
		t.Fatal(err)
	}
	if client.calls == 0 || !store.finished {
		t.Fatal("attempt-scoped lookup not used")
	}
}
