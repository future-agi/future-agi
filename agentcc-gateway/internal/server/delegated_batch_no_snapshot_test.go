package server

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// A single gateway turns delegated batch on by default, and it still runs a
// delegated batch only for a member its control-plane sync has confirmed:
// with no complete membership snapshot it refuses the submission and runs
// nothing.
func TestDelegatedBatchWithoutAMembershipSnapshotIsRefused(t *testing.T) {
	srv := newDelegated733Server(t)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	body := `{"requests":[` + delegated733Body + `],"max_concurrency":1}`
	env, grant, _ := signed733("/-/batches", body, "batch", time.Now().Unix())
	w := request733(srv, "/-/batches", body, env, grant)
	if w.Code == 202 || w.Code/100 == 2 {
		t.Fatalf("a delegated batch with no membership snapshot answered %d: %s", w.Code, w.Body.String())
	}
	if rows := srv.batchProcessor.OrgInventory(delegated733Org); len(rows) != 0 {
		t.Fatalf("the refused submission left %d batches", len(rows))
	}
}
