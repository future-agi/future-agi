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
)

func TestAsyncJobOwnership(t *testing.T) {
	for _, orgScoped := range []bool{true, false} {
		name := "key"
		ownerMeta := map[string]string{}
		if orgScoped {
			name = "organization"
			ownerMeta["org_id"] = "owner-org"
		}
		t.Run(name, func(t *testing.T) {
			store := async.NewStore()
			keys := auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{
				{Key: "owner-token", Metadata: ownerMeta},
				{Key: "peer-token", Metadata: ownerMeta},
				{Key: "other-token", Metadata: map[string]string{"org_id": "other-org"}},
				{Key: "unscoped-token"},
			}})
			h := &Handlers{asyncStore: store, asyncWorker: async.NewWorker(store, nil, 1), keyStore: keys}
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Request = &models.ChatCompletionRequest{Model: "test-model"}
			rc.Metadata["org_id"] = ownerMeta["org_id"]
			req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
			req.Header.Set("Authorization", "Bearer owner-token")
			req.Header.Set("x-agentcc-async", "true")
			rec := httptest.NewRecorder()
			if !h.AsyncMiddleware(rec, req, rc) || rec.Code != http.StatusAccepted {
				t.Fatalf("submit: %d %s", rec.Code, rec.Body.String())
			}
			var submitted struct {
				JobID string `json:"job_id"`
			}
			if err := json.Unmarshal(rec.Body.Bytes(), &submitted); err != nil || submitted.JobID == "" {
				t.Fatalf("submit response: %s (%v)", rec.Body.String(), err)
			}
			cancelled := 0
			store.Update(submitted.JobID, func(job *async.Job) {
				job.CancelFn = func() { cancelled++ }
			})
			request := func(method, id, token string, xAPIKey bool) *httptest.ResponseRecorder {
				r := httptest.NewRequest(method, "/v1/async?job_id="+id, nil)
				if token != "" {
					if xAPIKey {
						r.Header.Set("x-api-key", token)
					} else {
						r.Header.Set("Authorization", "Bearer "+token)
					}
				}
				r.Header.Set("x-agentcc-org-id", "owner-org")
				w := httptest.NewRecorder()
				if method == http.MethodGet {
					h.GetAsyncJob(w, r)
				} else {
					h.DeleteAsyncJob(w, r)
				}
				return w
			}
			for _, token := range []string{"other-token", "unscoped-token", "invalid-token", ""} {
				for _, method := range []string{http.MethodGet, http.MethodDelete} {
					for _, xAPIKey := range []bool{false, true} {
						got := request(method, submitted.JobID, token, xAPIKey)
						want := http.StatusNotFound
						if token == "invalid-token" || token == "" {
							want = http.StatusUnauthorized
						}
						if got.Code != want {
							t.Errorf("%s token=%q x-api-key=%v: status=%d want=%d body=%s", method, token, xAPIKey, got.Code, want, got.Body.String())
						}
						missing := request(method, "missing-job", token, xAPIKey)
						if got.Body.String() != missing.Body.String() {
							t.Errorf("%s token=%q: existing and missing job responses differ: %s / %s", method, token, got.Body.String(), missing.Body.String())
						}
						if store.Get(submitted.JobID) == nil || cancelled != 0 {
							t.Fatalf("negative request mutated job: count=%d cancelled=%d", store.Count(), cancelled)
						}
					}
				}
			}
			peer := request(http.MethodGet, submitted.JobID, "peer-token", false)
			wantPeer := http.StatusNotFound
			if orgScoped {
				wantPeer = http.StatusOK
			}
			if peer.Code != wantPeer {
				t.Errorf("peer status=%d want=%d", peer.Code, wantPeer)
			}
			for _, xAPIKey := range []bool{false, true} {
				got := request(http.MethodGet, submitted.JobID, "owner-token", xAPIKey)
				if got.Code != http.StatusOK {
					t.Errorf("owner GET status=%d body=%s", got.Code, got.Body.String())
				}
			}
			got := request(http.MethodDelete, submitted.JobID, "owner-token", true)
			if got.Code != http.StatusOK || cancelled != 1 || store.Count() != 0 {
				t.Fatalf("owner cancel status=%d cancelled=%d count=%d", got.Code, cancelled, store.Count())
			}
			if got := request(http.MethodDelete, submitted.JobID, "owner-token", false); got.Code != http.StatusNotFound || cancelled != 1 {
				t.Fatalf("repeat cancel status=%d cancelled=%d", got.Code, cancelled)
			}
		})
	}
}
