package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/async"
	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func putAsyncJob(store *async.Store, id, orgID string) {
	store.Put(&async.Job{
		ID:        id,
		Status:    async.StatusCompleted,
		OrgID:     orgID,
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
		Response:  &models.ChatCompletionResponse{ID: "resp-" + id},
	})
}

func asyncKeyStore(t *testing.T, keys ...config.AuthKeyConfig) *authpkg.KeyStore {
	t.Helper()
	return authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: keys})
}

func TestGetAsyncJob_OtherOrgIsNotFound(t *testing.T) {
	store := async.NewStore()
	putAsyncJob(store, "job-1", "org-1")
	ks := asyncKeyStore(t,
		config.AuthKeyConfig{Name: "a", Key: "sk-test-org-a", Metadata: map[string]string{"org_id": "org-1"}},
		config.AuthKeyConfig{Name: "b", Key: "sk-test-org-b", Metadata: map[string]string{"org_id": "org-2"}},
	)
	h := &Handlers{asyncStore: store, keyStore: ks}

	req := httptest.NewRequest(http.MethodGet, "/v1/async?job_id=job-1", nil)
	req.Header.Set("Authorization", "Bearer sk-test-org-b")
	rec := httptest.NewRecorder()
	h.GetAsyncJob(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404; body = %s", rec.Code, rec.Body.String())
	}
	if json.Valid(rec.Body.Bytes()) {
		var body map[string]interface{}
		_ = json.Unmarshal(rec.Body.Bytes(), &body)
		if _, ok := body["response"]; ok {
			t.Fatal("cross-org GET leaked a response field")
		}
	}
	if store.Get("job-1") == nil {
		t.Fatal("cross-org GET removed the job")
	}
}

func TestGetAsyncJob_SameOrgAllowed(t *testing.T) {
	store := async.NewStore()
	putAsyncJob(store, "job-1", "org-1")
	ks := asyncKeyStore(t,
		config.AuthKeyConfig{Name: "a", Key: "sk-test-org-a", Metadata: map[string]string{"org_id": "org-1"}},
	)
	h := &Handlers{asyncStore: store, keyStore: ks}

	req := httptest.NewRequest(http.MethodGet, "/v1/async?job_id=job-1", nil)
	req.Header.Set("Authorization", "Bearer sk-test-org-a")
	rec := httptest.NewRecorder()
	h.GetAsyncJob(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body = %s", rec.Code, rec.Body.String())
	}
}

func TestDeleteAsyncJob_OtherOrgDoesNotCancel(t *testing.T) {
	store := async.NewStore()
	putAsyncJob(store, "job-1", "org-1")
	store.Update("job-1", func(job *async.Job) {
		job.Status = async.StatusProcessing
	})
	ks := asyncKeyStore(t,
		config.AuthKeyConfig{Name: "b", Key: "sk-test-org-b", Metadata: map[string]string{"org_id": "org-2"}},
	)
	h := &Handlers{asyncStore: store, keyStore: ks}

	req := httptest.NewRequest(http.MethodDelete, "/v1/async?job_id=job-1", nil)
	req.Header.Set("Authorization", "Bearer sk-test-org-b")
	rec := httptest.NewRecorder()
	h.DeleteAsyncJob(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404; body = %s", rec.Code, rec.Body.String())
	}
	got := store.Get("job-1")
	if got == nil {
		t.Fatal("cross-org DELETE removed the job")
	}
	if got.Status != async.StatusProcessing {
		t.Fatalf("status = %s, want processing", got.Status)
	}
}

func TestGetAsyncJob_KeyWithoutOrgIsolatedByKeyID(t *testing.T) {
	ks := asyncKeyStore(t,
		config.AuthKeyConfig{Name: "a", Key: "sk-test-key-a"},
		config.AuthKeyConfig{Name: "b", Key: "sk-test-key-b"},
	)
	owner := ks.Lookup("sk-test-key-a")
	if owner == nil || owner.ID == "" {
		t.Fatal("expected key a")
	}
	store := async.NewStore()
	putAsyncJob(store, "job-1", owner.ID)
	h := &Handlers{asyncStore: store, keyStore: ks}

	req := httptest.NewRequest(http.MethodGet, "/v1/async?job_id=job-1", nil)
	req.Header.Set("Authorization", "Bearer sk-test-key-b")
	rec := httptest.NewRecorder()
	h.GetAsyncJob(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("other key status = %d, want 404; body = %s", rec.Code, rec.Body.String())
	}

	req = httptest.NewRequest(http.MethodGet, "/v1/async?job_id=job-1", nil)
	req.Header.Set("Authorization", "Bearer sk-test-key-a")
	rec = httptest.NewRecorder()
	h.GetAsyncJob(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("owner key status = %d, want 200; body = %s", rec.Code, rec.Body.String())
	}
}

func TestDeleteAsyncJob_SameOrgInvokesLiveCancelFn(t *testing.T) {
	store := async.NewStore()
	called := false
	store.Put(&async.Job{
		ID:     "job-1",
		Status: async.StatusProcessing,
		OrgID:  "org-1",
		CancelFn: func() {
			called = true
		},
	})
	ks := asyncKeyStore(t,
		config.AuthKeyConfig{Name: "a", Key: "sk-test-org-a", Metadata: map[string]string{"org_id": "org-1"}},
	)
	h := &Handlers{asyncStore: store, keyStore: ks}

	req := httptest.NewRequest(http.MethodDelete, "/v1/async?job_id=job-1", nil)
	req.Header.Set("Authorization", "Bearer sk-test-org-a")
	rec := httptest.NewRecorder()
	h.DeleteAsyncJob(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body = %s", rec.Code, rec.Body.String())
	}
	if !called {
		t.Fatal("same-org DELETE did not invoke the live CancelFn")
	}
	if store.Get("job-1") != nil {
		t.Fatal("job still present after same-org DELETE")
	}
}

func TestGetAsyncJob_EmptyStoredOwnerHiddenFromOrg(t *testing.T) {
	store := async.NewStore()
	putAsyncJob(store, "job-1", "")
	ks := asyncKeyStore(t,
		config.AuthKeyConfig{Name: "a", Key: "sk-test-org-a", Metadata: map[string]string{"org_id": "org-1"}},
	)
	h := &Handlers{asyncStore: store, keyStore: ks}

	req := httptest.NewRequest(http.MethodGet, "/v1/async?job_id=job-1", nil)
	req.Header.Set("Authorization", "Bearer sk-test-org-a")
	rec := httptest.NewRecorder()
	h.GetAsyncJob(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404; body = %s", rec.Code, rec.Body.String())
	}
}

func TestGetAsyncJob_AuthDisabledStillReadsByID(t *testing.T) {
	store := async.NewStore()
	putAsyncJob(store, "job-1", "org-1")
	h := &Handlers{asyncStore: store}

	req := httptest.NewRequest(http.MethodGet, "/v1/async?job_id=job-1", nil)
	rec := httptest.NewRecorder()
	h.GetAsyncJob(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body = %s", rec.Code, rec.Body.String())
	}
}

func TestAsyncMiddleware_KeyWithoutOrgStampsKeyID(t *testing.T) {
	store := async.NewStore()
	ks := asyncKeyStore(t,
		config.AuthKeyConfig{Name: "a", Key: "sk-test-key-a"},
		config.AuthKeyConfig{Name: "b", Key: "sk-test-key-b"},
	)
	owner := ks.Lookup("sk-test-key-a")
	if owner == nil || owner.ID == "" {
		t.Fatal("expected key a")
	}
	worker := async.NewWorker(store, func(ctx context.Context, req *models.ChatCompletionRequest, metadata map[string]string) (*models.ChatCompletionResponse, map[string]string, error) {
		t.Fatal("create must not call the provider")
		return nil, nil, nil
	}, 1)
	h := &Handlers{asyncStore: store, asyncWorker: worker, keyStore: ks}

	// auth_key_id is written by the auth plugin, which runs after this middleware.
	rc := models.AcquireRequestContext()
	rc.EndpointType = "chat"
	rc.Request = &models.ChatCompletionRequest{Model: "fixture"}
	rc.Metadata["authorization"] = "Bearer sk-test-key-a"

	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	req.Header.Set("Authorization", "Bearer sk-test-key-a")
	req.Header.Set("x-agentcc-async", "true")
	rec := httptest.NewRecorder()
	if !h.AsyncMiddleware(rec, req, rc) {
		t.Fatal("expected async create")
	}
	rc.Release()
	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202; body = %s", rec.Code, rec.Body.String())
	}
	var created map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	jobID, _ := created["job_id"].(string)
	if jobID == "" {
		t.Fatal("missing job_id")
	}
	stored := store.Get(jobID)
	if stored == nil {
		t.Fatal("job was not stored")
	}
	if stored.OrgID != owner.ID {
		t.Fatalf("stored owner = %q, want key id %q", stored.OrgID, owner.ID)
	}

	getReq := httptest.NewRequest(http.MethodGet, "/v1/async?job_id="+jobID, nil)
	getReq.Header.Set("Authorization", "Bearer sk-test-key-a")
	getRec := httptest.NewRecorder()
	h.GetAsyncJob(getRec, getReq)
	if getRec.Code != http.StatusOK {
		t.Fatalf("owner GET = %d, want 200; body = %s", getRec.Code, getRec.Body.String())
	}

	other := httptest.NewRequest(http.MethodGet, "/v1/async?job_id="+jobID, nil)
	other.Header.Set("Authorization", "Bearer sk-test-key-b")
	otherRec := httptest.NewRecorder()
	h.GetAsyncJob(otherRec, other)
	if otherRec.Code != http.StatusNotFound {
		t.Fatalf("other key GET = %d, want 404; body = %s", otherRec.Code, otherRec.Body.String())
	}
}
