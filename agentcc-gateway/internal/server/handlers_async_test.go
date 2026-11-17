package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/async"
)

func TestDeleteAsyncJob_InvokesLiveCancelFn(t *testing.T) {
	store := async.NewStore()
	h := &Handlers{asyncStore: store}

	called := false
	store.Put(&async.Job{
		ID:     "job-1",
		Status: async.StatusProcessing,
		CancelFn: func() {
			called = true
		},
	})

	req := httptest.NewRequest(http.MethodDelete, "/v1/async?job_id=job-1", nil)
	rec := httptest.NewRecorder()
	h.DeleteAsyncJob(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body = %s", rec.Code, rec.Body.String())
	}
	if !called {
		t.Fatal("DELETE did not invoke the live CancelFn")
	}
	if store.Get("job-1") != nil {
		t.Fatal("job still in store after DELETE")
	}

	var body map[string]interface{}
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body["status"] != "cancelled" {
		t.Errorf("status = %v, want cancelled", body["status"])
	}
	if body["deleted"] != true {
		t.Errorf("deleted = %v, want true", body["deleted"])
	}
}

func TestDeleteAsyncJob_NotFound(t *testing.T) {
	store := async.NewStore()
	h := &Handlers{asyncStore: store}

	req := httptest.NewRequest(http.MethodDelete, "/v1/async?job_id=missing", nil)
	rec := httptest.NewRecorder()
	h.DeleteAsyncJob(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rec.Code)
	}
}
