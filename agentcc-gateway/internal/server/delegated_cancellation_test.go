package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestDelegated733CancellationReasonReadback(t *testing.T) {
	srv := newDelegated733Server(t)
	prepareBatch733(t, srv)
	started, release := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	p := batch.NewProcessor(func(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		if calls.Add(1) == 1 {
			close(started)
			<-release
		}
		return &models.ChatCompletionResponse{}, nil, nil
	}, batch.WithKeyStore(srv.keyStore, true))
	srv.batchProcessor = p
	t.Cleanup(p.Stop)
	body := `{"requests":[` + delegated733Body + `,` + delegated733Body + `],"max_concurrency":1}`
	env, grant, _ := signed733("/-/batches", body, "batch", time.Now().Unix())
	w := request733(srv, "/-/batches", body, env, grant)
	if w.Code != 202 {
		close(release)
		t.Fatalf("submit: %d %s", w.Code, w.Body.String())
	}
	var submitted struct {
		ID string `json:"batch_id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &submitted); err != nil {
		close(release)
		t.Fatal(err)
	}
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		close(release)
		t.Fatal("first item not started")
	}
	// Drop the cancel RPC: the very next authoritative membership sync stops it.
	srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true}, time.Now())
	close(release)
	b, ok := p.GetForOrg(submitted.ID, delegated733Org)
	if !ok {
		t.Fatal("batch missing")
	}
	for deadline := time.Now().Add(5 * time.Second); b.GetCompletedAt() == nil; time.Sleep(time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("batch did not finish")
		}
	}
	for _, route := range []struct{ method, suffix string }{{"GET", ""}, {"POST", "/cancel"}, {"POST", "/cancel"}} {
		r := httptest.NewRequest(route.method, "/-/batches/"+b.ID+route.suffix+"?org_id="+delegated733Org, nil)
		r.Header.Set("Authorization", "Bearer "+delegated733Secret)
		result := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(result, r)
		var state struct {
			Status    string `json:"status"`
			ErrorCode string `json:"error_code"`
		}
		if err := json.Unmarshal(result.Body.Bytes(), &state); err != nil {
			t.Fatal(err)
		}
		if result.Code != http.StatusOK || state.Status != "cancelled" || state.ErrorCode != "delegation_member_removed" {
			t.Fatalf("readback: %d %s", result.Code, result.Body.String())
		}
	}
	if calls.Load() != 1 {
		t.Fatalf("queued item started after removal: calls=%d", calls.Load())
	}
}
