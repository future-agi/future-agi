package server

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// replicaNonceStore stands in for the nonce store a gateway cluster shares.
type replicaNonceStore struct {
	mu   sync.Mutex
	seen map[string]bool
}

func (s *replicaNonceStore) Consume(_ context.Context, id string, _, _ int64) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.seen == nil {
		s.seen = map[string]bool{}
	}
	if s.seen[id] {
		return false, nil
	}
	s.seen[id] = true
	return true, nil
}

// In a cluster, a delegated call is served when the replicas share a nonce
// store, and the same envelope sent to another replica is refused.
func TestDelegatedClusterWithSharedNonceStore(t *testing.T) {
	store := &replicaNonceStore{}
	replica := func() *Server {
		srv := newDelegated733Server(t)
		srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
		srv.cfg.Cluster.Enabled = true
		srv.SetEnvelopeNonceStore(store)
		return srv
	}
	first, second := replica(), replica()
	env, grant, _ := signed733(delegated733Route, delegated733Body, "playground", time.Now().Unix())
	if w := request733(first, delegated733Route, delegated733Body, env, grant); w.Code != 200 {
		t.Fatalf("a cluster replica with a shared nonce store answered %d: %s", w.Code, w.Body.String())
	}
	if w := request733(second, delegated733Route, delegated733Body, env, grant); w.Code != 401 {
		t.Fatalf("another replica answered the same envelope %d, want 401", w.Code)
	}
}

// A shared nonce store alone does not make a batch reachable from another
// replica: a cluster without a shared batch store still refuses a delegated
// batch submission, while it serves the delegated playground call. With one,
// the batch routes are served (delegated_batch_cluster_test.go).
func TestDelegatedClusterWithSharedNonceStoreRefusesBatch(t *testing.T) {
	srv := newDelegated733Server(t)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: delegated733Org, User: delegated733User, Workspace: "-"}}}, time.Now())
	srv.cfg.Cluster.Enabled = true
	srv.SetEnvelopeNonceStore(&replicaNonceStore{})
	body := `{"requests":[` + delegated733Body + `],"max_concurrency":1}`
	env, grant, _ := signed733("/-/batches", body, "batch", time.Now().Unix())
	if w := request733(srv, "/-/batches", body, env, grant); w.Code != 401 {
		t.Fatalf("a cluster with a shared nonce store answered a delegated batch submission %d, want 401: %s", w.Code, w.Body.String())
	}
	if rows := srv.batchProcessor.OrgInventory(delegated733Org); len(rows) != 0 {
		t.Fatalf("the refused submission left %d batches", len(rows))
	}
	env, grant, _ = signed733(delegated733Route, delegated733Body, "playground", time.Now().Unix())
	if w := request733(srv, delegated733Route, delegated733Body, env, grant); w.Code != 200 {
		t.Fatalf("the same cluster replica answered a delegated playground call %d: %s", w.Code, w.Body.String())
	}
}
