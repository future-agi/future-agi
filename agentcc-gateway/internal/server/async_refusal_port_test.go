package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/async"
)

// Helpers for the org-scope async tests. An org key cannot queue an async
// job: AsyncMiddleware answers 400 deferred_run_unavailable
// (TestAsyncIsRefusedToOrgKeys), because a job runs outside the handler's
// call-order hooks. The tests check that refusal for an org key, and seed the
// org's job in the store where they test reads, deletes or the worker share.

// keyOrgOf is the org of key in srv's key store, "" for a key with no org.
func keyOrgOf(t *testing.T, srv *Server, key string) string {
	t.Helper()
	if srv.keyStore == nil || key == "" {
		return ""
	}
	k := srv.keyStore.Lookup(key)
	if k == nil {
		t.Fatalf("key %q is not in the key store", key)
	}
	return k.Metadata["org_id"]
}

// assertAsyncRefusedToOrgKey checks that w is the refusal of an org key's
// async request and that it stored no job (before is the store's count before
// the request).
func assertAsyncRefusedToOrgKey(t *testing.T, srv *Server, w *httptest.ResponseRecorder, before int) {
	t.Helper()
	if w.Code != http.StatusBadRequest || errorCode(t, w) != "deferred_run_unavailable" {
		t.Fatalf("async request with an org key: status %d body %s, want 400 deferred_run_unavailable", w.Code, w.Body.String())
	}
	if n := srv.handlers.asyncStore.Count(); n != before {
		t.Fatalf("a refused async request stored a job (%d jobs, was %d)", n, before)
	}
}

// seedAsyncJob stores a completed async job of org and returns its id.
func seedAsyncJob(t *testing.T, srv *Server, org string) string {
	t.Helper()
	id := async.GenerateJobID()
	now := time.Now()
	srv.handlers.asyncStore.Put(&async.Job{
		ID: id, OrgID: org, Status: async.StatusCompleted, EndpointType: "chat",
		CreatedAt: now, ExpiresAt: now.Add(time.Hour),
	})
	return id
}

// holdAsyncJobs submits n jobs of org straight to srv's async worker, each of
// which runs until its context ends, and returns how many the worker took
// before the first refusal (with that refusal, nil when it took all n).
// started receives one value per job that starts running.
func holdAsyncJobs(t *testing.T, srv *Server, org string, n int, started chan<- struct{}) (int, error) {
	t.Helper()
	exec := func(ctx context.Context) (*async.Execution, error) {
		started <- struct{}{}
		<-ctx.Done()
		return &async.Execution{Provider: "openai"}, ctx.Err()
	}
	now := time.Now()
	for i := 0; i < n; i++ {
		job := &async.Job{
			ID: async.GenerateJobID(), OrgID: org, Status: async.StatusQueued, EndpointType: "chat",
			Metadata: map[string]string{"timeout_ms": "3600000"},
			Exec:     exec, CreatedAt: now, TTL: time.Hour, ExpiresAt: now.Add(2 * time.Hour),
		}
		if apiErr := srv.handlers.asyncWorker.Submit(job); apiErr != nil {
			return i, apiErr
		}
	}
	return n, nil
}
