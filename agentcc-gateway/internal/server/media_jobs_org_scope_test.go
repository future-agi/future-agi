package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/scheduled"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

// Video and scheduled jobs belong to the org of the API key that created them.
// Another org's key must not list, read, cancel or delete them, and get, cancel
// and delete answer 404 so a job id from another org reveals nothing. A key
// with no org (single-tenant, ops) only reaches jobs that also have no org.

const (
	mediaOrgA     = "org-media-a"
	mediaOrgB     = "org-media-b"
	mediaKeyA     = "sk-agentcc-media-org-a"
	mediaKeyB     = "sk-agentcc-media-org-b"
	mediaKeyNoOrg = "sk-agentcc-media-no-org"
)

// newMediaScopeServer builds the whole gateway (key auth middleware, router,
// handlers) with scheduled completions on. With authEnabled false it is the
// OSS single-tenant setup: no key store, every request has no org.
func newMediaScopeServer(t *testing.T, authEnabled bool) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = authEnabled
	if authEnabled {
		// Internal keys: POST /v1/scheduled refuses keys limited to their org's
		// providers (deferred_run_unavailable), so scheduled org scope is
		// exercised with internal keys that carry an org. Video handlers never
		// read the key type.
		cfg.Auth.Keys = []config.AuthKeyConfig{
			{Name: "org-a", Key: mediaKeyA, Owner: "a", KeyType: "internal", Metadata: map[string]string{"org_id": mediaOrgA}},
			{Name: "org-b", Key: mediaKeyB, Owner: "b", KeyType: "internal", Metadata: map[string]string{"org_id": mediaOrgB}},
			{Name: "no-org", Key: mediaKeyNoOrg, Owner: "ops", KeyType: "internal"},
		}
	}
	cfg.Routing.Scheduled.Enabled = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func mediaCall(t *testing.T, srv *Server, method, path, key, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func mediaDecode(t *testing.T, w *httptest.ResponseRecorder) map[string]interface{} {
	t.Helper()
	var out map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatalf("response is not JSON (status %d): %s", w.Code, w.Body.String())
	}
	return out
}

func submitVideoAs(t *testing.T, srv *Server, key string) string {
	t.Helper()
	w := mediaCall(t, srv, "POST", "/v1/videos", key, `{"model":"video-model","prompt":"a cat"}`)
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit video: status %d body %s", w.Code, w.Body.String())
	}
	return mediaDecode(t, w)["id"].(string)
}

func submitScheduledAs(t *testing.T, srv *Server, key string) string {
	t.Helper()
	w := mediaCall(t, srv, "POST", "/v1/scheduled", key,
		`{"delay":"1h","request":{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}}`)
	if w.Code != http.StatusCreated {
		t.Fatalf("submit scheduled: status %d body %s", w.Code, w.Body.String())
	}
	return mediaDecode(t, w)["id"].(string)
}

// listedIDs returns the ids in a {"data": [...]} list response.
func listedIDs(t *testing.T, w *httptest.ResponseRecorder) map[string]bool {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("list: status %d body %s", w.Code, w.Body.String())
	}
	ids := map[string]bool{}
	data, _ := mediaDecode(t, w)["data"].([]interface{})
	for _, item := range data {
		ids[item.(map[string]interface{})["id"].(string)] = true
	}
	return ids
}

func TestVideoJobs_CreateRecordsCallerOrg(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	for key, wantOrg := range map[string]string{mediaKeyA: mediaOrgA, mediaKeyB: mediaOrgB, mediaKeyNoOrg: ""} {
		id := submitVideoAs(t, srv, key)
		job, err := srv.handlers.videoStore.Get(id)
		if err != nil {
			t.Fatalf("stored job %s: %v", id, err)
		}
		if job.OrgID != wantOrg {
			t.Errorf("video job created with key %q has org %q, want %q", key, job.OrgID, wantOrg)
		}
	}
}

// KeyAuth also accepts the key in x-api-key; the org must come from it too.
func TestVideoJobs_XAPIKeyHeaderCarriesOrg(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	req := httptest.NewRequest("POST", "/v1/videos", bytes.NewBufferString(`{"model":"video-model","prompt":"a cat"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", mediaKeyA)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit video: status %d body %s", w.Code, w.Body.String())
	}
	job, err := srv.handlers.videoStore.Get(mediaDecode(t, w)["id"].(string))
	if err != nil {
		t.Fatal(err)
	}
	if job.OrgID != mediaOrgA {
		t.Errorf("video job created with x-api-key has org %q, want %q", job.OrgID, mediaOrgA)
	}
}

// The org comes from the same key KeyAuth authenticated: a Bearer
// Authorization header first, otherwise x-api-key; an Authorization header of
// any other scheme is ignored.
func TestMediaJobs_CallerOrgFollowsKeyAuthHeaderPrecedence(t *testing.T) {
	cases := []struct {
		name          string
		authorization string
		xAPIKey       string
		wantOrg       string
	}{
		{"a non-Bearer Authorization header is ignored for x-api-key", "Basic Zm9vOmJhcg==", mediaKeyA, mediaOrgA},
		{"a Bearer key wins over x-api-key", "Bearer " + mediaKeyA, mediaKeyB, mediaOrgA},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newMediaScopeServer(t, true)
			none := submitVideoAs(t, srv, mediaKeyNoOrg)
			for _, path := range []string{"/v1/videos", "/v1/scheduled"} {
				body := `{"model":"video-model","prompt":"a cat"}`
				if path == "/v1/scheduled" {
					body = `{"delay":"1h","request":{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}}`
				}
				req := httptest.NewRequest("POST", path, bytes.NewBufferString(body))
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("Authorization", tc.authorization)
				req.Header.Set("x-api-key", tc.xAPIKey)
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				if w.Code != http.StatusAccepted && w.Code != http.StatusCreated {
					t.Fatalf("POST %s: status %d body %s", path, w.Code, w.Body.String())
				}
				id := mediaDecode(t, w)["id"].(string)
				var gotOrg string
				if path == "/v1/videos" {
					job, err := srv.handlers.videoStore.Get(id)
					if err != nil {
						t.Fatal(err)
					}
					gotOrg = job.OrgID
				} else {
					job, err := srv.handlers.scheduledStore.Get(id)
					if err != nil {
						t.Fatal(err)
					}
					gotOrg = job.OrgID
				}
				if gotOrg != tc.wantOrg {
					t.Errorf("POST %s stored org %q, want %q", path, gotOrg, tc.wantOrg)
				}
			}

			// The same headers must not reach the no-org tenant's job.
			req := httptest.NewRequest("GET", "/v1/videos/"+none, nil)
			req.Header.Set("Authorization", tc.authorization)
			req.Header.Set("x-api-key", tc.xAPIKey)
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != http.StatusNotFound {
				t.Errorf("GET a no-org job: status %d, want 404; body %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestVideoJobs_ListReturnsOnlyCallerOrg(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	a := submitVideoAs(t, srv, mediaKeyA)
	b := submitVideoAs(t, srv, mediaKeyB)
	none := submitVideoAs(t, srv, mediaKeyNoOrg)

	want := map[string]string{mediaKeyA: a, mediaKeyB: b, mediaKeyNoOrg: none}
	for key, own := range want {
		got := listedIDs(t, mediaCall(t, srv, "GET", "/v1/videos", key, ""))
		if len(got) != 1 || !got[own] {
			t.Errorf("key %q listed %v, want only its own job %s", key, got, own)
		}
	}
}

func TestVideoJobs_OtherOrgGetsNotFound(t *testing.T) {
	cases := []struct {
		name, owner, caller string
	}{
		{"org B asks for org A's job", mediaKeyA, mediaKeyB},
		{"a key with no org asks for org A's job", mediaKeyA, mediaKeyNoOrg},
		{"org A asks for a job that has no org", mediaKeyNoOrg, mediaKeyA},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newMediaScopeServer(t, true)
			id := submitVideoAs(t, srv, tc.owner)
			if w := mediaCall(t, srv, "GET", "/v1/videos/"+id, tc.caller, ""); w.Code != http.StatusNotFound {
				t.Errorf("GET: status %d, want 404; body %s", w.Code, w.Body.String())
			}
			if w := mediaCall(t, srv, "DELETE", "/v1/videos/"+id, tc.caller, ""); w.Code != http.StatusNotFound {
				t.Errorf("DELETE: status %d, want 404; body %s", w.Code, w.Body.String())
			}
			// The refused delete left the job queued for its owner.
			w := mediaCall(t, srv, "GET", "/v1/videos/"+id, tc.owner, "")
			if w.Code != http.StatusOK {
				t.Fatalf("owner GET after refused DELETE: status %d body %s", w.Code, w.Body.String())
			}
			if status := mediaDecode(t, w)["status"]; status != video.StatusQueued {
				t.Errorf("job status %v after another org's DELETE, want %q", status, video.StatusQueued)
			}
		})
	}
}

func TestVideoJobs_OwnerCanGetAndDelete(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	for _, key := range []string{mediaKeyA, mediaKeyNoOrg} {
		id := submitVideoAs(t, srv, key)
		if w := mediaCall(t, srv, "GET", "/v1/videos/"+id, key, ""); w.Code != http.StatusOK {
			t.Errorf("key %q GET own job: status %d body %s", key, w.Code, w.Body.String())
		}
		w := mediaCall(t, srv, "DELETE", "/v1/videos/"+id, key, "")
		if w.Code != http.StatusOK || mediaDecode(t, w)["status"] != "cancelled" {
			t.Errorf("key %q DELETE own queued job: status %d body %s", key, w.Code, w.Body.String())
		}
		if _, err := srv.handlers.videoStore.Get(id); err == nil {
			t.Errorf("key %q DELETE left job %s in the store", key, id)
		}
	}
}

func TestScheduledJobs_CreateRecordsCallerOrg(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	for key, wantOrg := range map[string]string{mediaKeyA: mediaOrgA, mediaKeyB: mediaOrgB, mediaKeyNoOrg: ""} {
		id := submitScheduledAs(t, srv, key)
		job, err := srv.handlers.scheduledStore.Get(id)
		if err != nil {
			t.Fatalf("stored job %s: %v", id, err)
		}
		if job.OrgID != wantOrg {
			t.Errorf("scheduled job created with key %q has org %q, want %q", key, job.OrgID, wantOrg)
		}
	}
}

func TestScheduledJobs_ListReturnsOnlyCallerOrg(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	a := submitScheduledAs(t, srv, mediaKeyA)
	b := submitScheduledAs(t, srv, mediaKeyB)
	none := submitScheduledAs(t, srv, mediaKeyNoOrg)

	want := map[string]string{mediaKeyA: a, mediaKeyB: b, mediaKeyNoOrg: none}
	for key, own := range want {
		got := listedIDs(t, mediaCall(t, srv, "GET", "/v1/scheduled", key, ""))
		if len(got) != 1 || !got[own] {
			t.Errorf("key %q listed %v, want only its own job %s", key, got, own)
		}
	}
}

func TestScheduledJobs_OtherOrgGetsNotFound(t *testing.T) {
	cases := []struct {
		name, owner, caller string
	}{
		{"org B asks for org A's job", mediaKeyA, mediaKeyB},
		{"a key with no org asks for org A's job", mediaKeyA, mediaKeyNoOrg},
		{"org A asks for a job that has no org", mediaKeyNoOrg, mediaKeyA},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newMediaScopeServer(t, true)
			id := submitScheduledAs(t, srv, tc.owner)
			if w := mediaCall(t, srv, "GET", "/v1/scheduled/"+id, tc.caller, ""); w.Code != http.StatusNotFound {
				t.Errorf("GET: status %d, want 404; body %s", w.Code, w.Body.String())
			}
			// Another org's cancel of a pending job answers 404, exactly as an
			// unknown job id does.
			if w := mediaCall(t, srv, "DELETE", "/v1/scheduled/"+id, tc.caller, ""); w.Code != http.StatusNotFound {
				t.Errorf("DELETE: status %d, want 404; body %s", w.Code, w.Body.String())
			}
			job, err := srv.handlers.scheduledStore.Get(id)
			if err != nil {
				t.Fatalf("job gone after another org's cancel: %v", err)
			}
			if job.Status != scheduled.StatusPending {
				t.Errorf("job status %q after another org's cancel, want %q", job.Status, scheduled.StatusPending)
			}
		})
	}
}

func TestScheduledJobs_OwnerCanGetAndCancel(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	for _, key := range []string{mediaKeyA, mediaKeyNoOrg} {
		id := submitScheduledAs(t, srv, key)
		if w := mediaCall(t, srv, "GET", "/v1/scheduled/"+id, key, ""); w.Code != http.StatusOK {
			t.Errorf("key %q GET own job: status %d body %s", key, w.Code, w.Body.String())
		}
		w := mediaCall(t, srv, "DELETE", "/v1/scheduled/"+id, key, "")
		if w.Code != http.StatusOK || mediaDecode(t, w)["status"] != scheduled.StatusCancelled {
			t.Errorf("key %q cancel own pending job: status %d body %s", key, w.Code, w.Body.String())
		}
	}
}

// With auth off (OSS, single tenant) there are no orgs: every job has none and
// every request reaches all of them.
func TestMediaJobs_AuthDisabledIsSingleTenant(t *testing.T) {
	srv := newMediaScopeServer(t, false)

	v1, v2 := submitVideoAs(t, srv, ""), submitVideoAs(t, srv, "")
	if got := listedIDs(t, mediaCall(t, srv, "GET", "/v1/videos", "", "")); len(got) != 2 || !got[v1] || !got[v2] {
		t.Errorf("video list %v, want %s and %s", got, v1, v2)
	}
	if w := mediaCall(t, srv, "GET", "/v1/videos/"+v1, "", ""); w.Code != http.StatusOK {
		t.Errorf("video GET: status %d body %s", w.Code, w.Body.String())
	}
	if w := mediaCall(t, srv, "DELETE", "/v1/videos/"+v1, "", ""); w.Code != http.StatusOK {
		t.Errorf("video DELETE: status %d body %s", w.Code, w.Body.String())
	}

	s1, s2 := submitScheduledAs(t, srv, ""), submitScheduledAs(t, srv, "")
	if got := listedIDs(t, mediaCall(t, srv, "GET", "/v1/scheduled", "", "")); len(got) != 2 || !got[s1] || !got[s2] {
		t.Errorf("scheduled list %v, want %s and %s", got, s1, s2)
	}
	if w := mediaCall(t, srv, "GET", "/v1/scheduled/"+s1, "", ""); w.Code != http.StatusOK {
		t.Errorf("scheduled GET: status %d body %s", w.Code, w.Body.String())
	}
	if w := mediaCall(t, srv, "DELETE", "/v1/scheduled/"+s1, "", ""); w.Code != http.StatusOK {
		t.Errorf("scheduled cancel: status %d body %s", w.Code, w.Body.String())
	}
}
