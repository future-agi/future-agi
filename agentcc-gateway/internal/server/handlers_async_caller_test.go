package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

func asyncCallerHandlers() *Handlers {
	store := async.NewStore()
	keys := auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{
		{Key: "synthetic-a", Metadata: map[string]string{"org_id": "org-a"}},
		{Key: "synthetic-a2", Metadata: map[string]string{"org_id": "org-a"}},
		{Key: "synthetic-b", Metadata: map[string]string{"org_id": "org-b"}},
		{Key: "synthetic-static"}, {Key: "synthetic-static2"},
	}})
	// The worker is never started, so jobs stay exactly as the test leaves them.
	return &Handlers{keyStore: keys, asyncStore: store, asyncWorker: async.NewWorker(store, nil, 1)}
}

func asyncCallerRequest(method, jobID, key string) *http.Request {
	r := httptest.NewRequest(method, "/v1/async/"+jobID+"?job_id="+jobID, nil)
	if key != "" {
		r.Header.Set("Authorization", "Bearer "+key)
	}
	return r
}

func asyncCallerSubmit(t *testing.T, h *Handlers, key string) string {
	t.Helper()
	r := asyncCallerRequest(http.MethodPost, "", key)
	r.Header.Set("x-agentcc-async", "true")
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "synthetic-model"}
	// Request metadata must not decide who owns the job.
	rc.Metadata["org_id"] = "forged"
	rc.Metadata["key_org_id"] = "forged"
	setAuthMetadataFromRequest(rc, r)
	w := httptest.NewRecorder()
	if !h.AsyncMiddleware(w, r, rc) || w.Code != http.StatusAccepted {
		t.Fatalf("create: %d %s", w.Code, w.Body.String())
	}
	var result struct {
		JobID string `json:"job_id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil || result.JobID == "" {
		t.Fatalf("create response: %s", w.Body.String())
	}
	return result.JobID
}

func TestAsyncJobOtherCallerGetsNotFound(t *testing.T) {
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		for _, key := range []string{"synthetic-b", "synthetic-static", "unknown", ""} {
			t.Run(method+"/"+key, func(t *testing.T) {
				h := asyncCallerHandlers()
				id := asyncCallerSubmit(t, h, "synthetic-a")
				cancelled := false
				h.asyncStore.Update(id, func(j *async.Job) { j.CancelFn = func() { cancelled = true } })
				call := h.GetAsyncJob
				if method == http.MethodDelete {
					call = h.DeleteAsyncJob
				}
				w, missing := httptest.NewRecorder(), httptest.NewRecorder()
				call(w, asyncCallerRequest(method, id, key))
				call(missing, asyncCallerRequest(method, "missing", key))
				want := http.StatusNotFound
				if key == "unknown" || key == "" {
					want = http.StatusUnauthorized
				}
				if w.Code != want || w.Body.String() != missing.Body.String() {
					t.Errorf("other caller's job response differs from missing job: %d %s / %s", w.Code, w.Body.String(), missing.Body.String())
				}
				if cancelled || h.asyncStore.Get(id) == nil {
					t.Error("request from another caller changed the job or ran its cancel callback")
				}
			})
		}
	}
}

func TestAsyncJobSameOrgAndSameKeyAccess(t *testing.T) {
	for _, pair := range [][2]string{{"synthetic-a", "synthetic-a2"}, {"synthetic-static", "synthetic-static"}} {
		t.Run(pair[0], func(t *testing.T) {
			h := asyncCallerHandlers()
			id := asyncCallerSubmit(t, h, pair[0])
			w := httptest.NewRecorder()
			r := asyncCallerRequest(http.MethodGet, id, "")
			r.Header.Set("x-api-key", pair[1])
			h.GetAsyncJob(w, r)
			if w.Code != http.StatusOK {
				t.Fatalf("owner read: %d %s", w.Code, w.Body.String())
			}
			cancelled := false
			h.asyncStore.Update(id, func(j *async.Job) { j.CancelFn = func() { cancelled = true; _ = h.asyncStore.Count() } })
			w = httptest.NewRecorder()
			h.DeleteAsyncJob(w, asyncCallerRequest(http.MethodDelete, id, pair[1]))
			if w.Code != http.StatusOK || !cancelled || h.asyncStore.Get(id) != nil {
				t.Errorf("owner cancellation: status=%d callback=%v", w.Code, cancelled)
			}
		})
	}
}

func TestAsyncJobKeyWithoutOrgCannotReadOtherKeysJob(t *testing.T) {
	h := asyncCallerHandlers()
	id := asyncCallerSubmit(t, h, "synthetic-static")
	w := httptest.NewRecorder()
	h.GetAsyncJob(w, asyncCallerRequest(http.MethodGet, id, "synthetic-static2"))
	if w.Code != http.StatusNotFound {
		t.Fatalf("other static key read: %d", w.Code)
	}
}

func TestAsyncJobWithoutOwnerNotReturned(t *testing.T) {
	h := asyncCallerHandlers()
	h.asyncStore.Put(&async.Job{ID: "legacy", Status: async.StatusQueued})
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		w := httptest.NewRecorder()
		if method == http.MethodGet {
			h.GetAsyncJob(w, asyncCallerRequest(method, "legacy", "synthetic-a"))
		} else {
			h.DeleteAsyncJob(w, asyncCallerRequest(method, "legacy", "synthetic-a"))
		}
		if w.Code != http.StatusNotFound {
			t.Errorf("job without owner %s: %d", method, w.Code)
		}
	}
}

func TestAsyncJobStandaloneModeWorksWithoutKey(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	s := createTestServer(t, mock.URL)
	s.asyncWorker.Stop()
	h := s.handlers
	id := asyncCallerSubmit(t, h, "")
	w := httptest.NewRecorder()
	h.GetAsyncJob(w, asyncCallerRequest(http.MethodGet, id, ""))
	if w.Code != http.StatusOK {
		t.Fatalf("standalone read: %d", w.Code)
	}
	w = httptest.NewRecorder()
	h.DeleteAsyncJob(w, asyncCallerRequest(http.MethodDelete, id, ""))
	if w.Code != http.StatusOK {
		t.Fatalf("standalone delete: %d", w.Code)
	}
}

// License auth without key auth has no API key to own an async job, so submit,
// read and cancel return 401.
func TestAsyncJobLicenseModeWithoutKeysRequiresKey(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: mock.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.LicenseAuth.Enabled = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	s := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	s.asyncWorker.Stop()
	s.asyncWorker = nil
	h := s.handlers
	h.asyncStore.Put(&async.Job{ID: "license-job", Status: async.StatusCompleted})

	r := asyncCallerRequest(http.MethodPost, "", "")
	r.Header.Set("x-agentcc-async", "true")
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	w := httptest.NewRecorder()
	if !h.AsyncMiddleware(w, r, rc) || w.Code != http.StatusUnauthorized || h.asyncStore.Count() != 1 {
		t.Fatalf("submit without a key: handled with %d count=%d", w.Code, h.asyncStore.Count())
	}
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		w := httptest.NewRecorder()
		if method == http.MethodGet {
			h.GetAsyncJob(w, asyncCallerRequest(method, "license-job", ""))
		} else {
			h.DeleteAsyncJob(w, asyncCallerRequest(method, "license-job", ""))
		}
		if w.Code != http.StatusUnauthorized {
			t.Errorf("%s without a key got %d: %s", method, w.Code, w.Body.String())
		}
	}
	if h.asyncStore.Get("license-job") == nil {
		t.Error("job removed by a caller without a key")
	}
}

func TestAsyncSubmitWithUnknownKeyRejected(t *testing.T) {
	h := asyncCallerHandlers()
	r := asyncCallerRequest(http.MethodPost, "", "unknown")
	r.Header.Set("x-agentcc-async", "true")
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Model: "synthetic-model"}
	rc.Metadata["org_id"] = "org-a"
	w := httptest.NewRecorder()
	if !h.AsyncMiddleware(w, r, rc) || w.Code != http.StatusUnauthorized || h.asyncStore.Count() != 0 {
		t.Fatalf("unknown key submit: %d count=%d", w.Code, h.asyncStore.Count())
	}
}

func TestAsyncSubmitStoresKeyOrganization(t *testing.T) {
	for key, want := range map[string]string{"synthetic-a": "org-a", "synthetic-static": ""} {
		h := asyncCallerHandlers()
		id := asyncCallerSubmit(t, h, key)
		if got := h.asyncStore.Get(id); got == nil || got.OrgID != want {
			t.Errorf("key %s: stored org = %v, want %q", key, got, want)
		}
	}
}
