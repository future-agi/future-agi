package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/scheduled"
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
		cfg.Auth.Keys = []config.AuthKeyConfig{
			{Name: "org-a", Key: mediaKeyA, Owner: "a", Metadata: map[string]string{"org_id": mediaOrgA}},
			{Name: "org-b", Key: mediaKeyB, Owner: "b", Metadata: map[string]string{"org_id": mediaOrgB}},
			// An internal (ops) key: on this branch only a key that may use
			// the gateway's providers can queue a scheduled job.
			{Name: "no-org", Key: mediaKeyNoOrg, Owner: "ops", KeyType: "internal"},
		}
	}
	cfg.Routing.Scheduled.Enabled = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	// The video routes answer 501 until a video provider is registered
	// (TestVideosUnconfigured501); nothing calls it.
	registry.RegisterProvider("fake-video", fakeVideoProvider{}, []string{"clip-1"})
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	releaseTestServer(t, srv)
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
	w := videoPost(t, srv, key, videoServedBody)
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit video: status %d body %s", w.Code, w.Body.String())
	}
	return mediaDecode(t, w)["id"].(string)
}

// submitScheduledAs returns the id of a pending scheduled job that belongs to
// key's tenant. An internal key, or any request with auth off, creates it over
// HTTP. An org key cannot on this branch (400 deferred_run_unavailable; in
// production every /v1/scheduled route is 501, TestScheduledRoutesAll501):
// the helper checks that refusal, then seeds the org's job in the store, so
// the read, list and cancel scope rows still run over HTTP.
func submitScheduledAs(t *testing.T, srv *Server, key string) string {
	t.Helper()
	w := mediaCall(t, srv, "POST", "/v1/scheduled", key,
		`{"delay":"1h","request":{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}}`)
	if w.Code == http.StatusCreated {
		return mediaDecode(t, w)["id"].(string)
	}
	org := keyOrgOf(t, srv, key)
	if org == "" {
		t.Fatalf("submit scheduled: status %d body %s", w.Code, w.Body.String())
	}
	if w.Code != http.StatusBadRequest || errorCode(t, w) != "deferred_run_unavailable" {
		t.Fatalf("submit scheduled with an org key: status %d body %s, want 400 deferred_run_unavailable", w.Code, w.Body.String())
	}
	now := time.Now()
	job := &scheduled.ScheduledJob{
		ID: fmt.Sprintf("sched-seed-%s-%d", org, now.UnixNano()), OrgID: org,
		Status: scheduled.StatusPending, ScheduledAt: now.Add(time.Hour), CreatedAt: now, UpdatedAt: now,
		Request: json.RawMessage(`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`),
		Model:   "gpt-4o", MaxAttempts: 1, ExpiresAt: now.Add(48 * time.Hour),
	}
	if err := srv.handlers.scheduledStore.Create(job); err != nil {
		t.Fatalf("seeding org %s's scheduled job: %v", org, err)
	}
	return job.ID
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
	srv := newMediaVideoServer(t, true)
	for key, wantOrg := range map[string]string{mediaKeyA: mediaOrgA, mediaKeyB: mediaOrgB} {
		id := submitVideoAs(t, srv, key)
		job, err := videoStoreOf(t, srv.handlers).Get(id)
		if err != nil {
			t.Fatalf("stored job %s: %v", id, err)
		}
		if job.OrgID != wantOrg {
			t.Errorf("video job created with key %q has org %q, want %q", key, job.OrgID, wantOrg)
		}
	}
	// A key with no org cannot create a video job.
	if w := videoPost(t, srv, mediaKeyNoOrg, videoServedBody); w.Code != http.StatusForbidden || errorCode(t, w) != "missing_org" {
		t.Errorf("submit with a key with no org: status %d body %s, want 403 missing_org", w.Code, w.Body.String())
	}
}

// KeyAuth also accepts the key in x-api-key; the org must come from it too.
func TestVideoJobs_XAPIKeyHeaderCarriesOrg(t *testing.T) {
	srv := newMediaVideoServer(t, true)
	req := httptest.NewRequest("POST", "/v1/videos", bytes.NewBufferString(videoServedBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", mediaKeyA)
	req.Header.Set("Idempotency-Key", nextVideoIdempotencyKey())
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit video: status %d body %s", w.Code, w.Body.String())
	}
	job, err := videoStoreOf(t, srv.handlers).Get(mediaDecode(t, w)["id"].(string))
	if err != nil {
		t.Fatal(err)
	}
	if job.OrgID != mediaOrgA {
		t.Errorf("video job created with x-api-key has org %q, want %q", job.OrgID, mediaOrgA)
	}
}

// The org must come from the same key KeyAuth authenticated. KeyAuth takes a
// Bearer Authorization header first and otherwise x-api-key, ignoring an
// Authorization header of any other scheme; reading the org from a different
// header would file org A's job, and its reads, under the no-org tenant.
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
			srv := newMediaVideoServer(t, true)
			other := submitVideoAs(t, srv, mediaKeyB)
			for _, path := range []string{"/v1/videos", "/v1/scheduled"} {
				body := videoServedBody
				if path == "/v1/scheduled" {
					body = `{"delay":"1h","request":{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}}`
				}
				req := httptest.NewRequest("POST", path, bytes.NewBufferString(body))
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("Authorization", tc.authorization)
				req.Header.Set("x-api-key", tc.xAPIKey)
				req.Header.Set("Idempotency-Key", nextVideoIdempotencyKey())
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				if path == "/v1/scheduled" {
					// Both keys are org keys, which cannot create a scheduled
					// job on this branch. The precedence is pinned on reads:
					// the headers reach the job of wantOrg and no other.
					if w.Code != http.StatusBadRequest || errorCode(t, w) != "deferred_run_unavailable" {
						t.Fatalf("POST %s: status %d body %s, want 400 deferred_run_unavailable", path, w.Code, w.Body.String())
					}
					own, other := submitScheduledAs(t, srv, mediaKeyA), submitScheduledAs(t, srv, mediaKeyB)
					if tc.wantOrg != mediaOrgA {
						own, other = other, own
					}
					for id, want := range map[string]int{own: http.StatusOK, other: http.StatusNotFound} {
						get := httptest.NewRequest("GET", "/v1/scheduled/"+id, nil)
						get.Header.Set("Authorization", tc.authorization)
						get.Header.Set("x-api-key", tc.xAPIKey)
						gw := httptest.NewRecorder()
						srv.httpServer.Handler.ServeHTTP(gw, get)
						if gw.Code != want {
							t.Errorf("GET /v1/scheduled/%s: status %d, want %d; body %s", id, gw.Code, want, gw.Body.String())
						}
					}
					continue
				}
				if w.Code != http.StatusAccepted {
					t.Fatalf("POST %s: status %d body %s", path, w.Code, w.Body.String())
				}
				id := mediaDecode(t, w)["id"].(string)
				job, err := videoStoreOf(t, srv.handlers).Get(id)
				if err != nil {
					t.Fatal(err)
				}
				if gotOrg := job.OrgID; gotOrg != tc.wantOrg {
					t.Errorf("POST %s stored org %q, want %q", path, gotOrg, tc.wantOrg)
				}
			}

			// The same headers must not reach another org's job.
			req := httptest.NewRequest("GET", "/v1/videos/"+other, nil)
			req.Header.Set("Authorization", tc.authorization)
			req.Header.Set("x-api-key", tc.xAPIKey)
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != http.StatusNotFound {
				t.Errorf("GET another org's job: status %d, want 404; body %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestVideoJobs_ListReturnsOnlyCallerOrg(t *testing.T) {
	srv := newMediaVideoServer(t, true)
	a := submitVideoAs(t, srv, mediaKeyA)
	b := submitVideoAs(t, srv, mediaKeyB)

	want := map[string]string{mediaKeyA: a, mediaKeyB: b}
	for key, own := range want {
		got := listedIDs(t, mediaCall(t, srv, "GET", "/v1/videos", key, ""))
		if len(got) != 1 || !got[own] {
			t.Errorf("key %q listed %v, want only its own job %s", key, got, own)
		}
	}
	// A key with no org lists nothing: it is refused.
	if w := mediaCall(t, srv, "GET", "/v1/videos", mediaKeyNoOrg, ""); w.Code != http.StatusForbidden || errorCode(t, w) != "missing_org" {
		t.Errorf("list with a key with no org: status %d body %s, want 403 missing_org", w.Code, w.Body.String())
	}
}

func TestVideoJobs_OtherOrgGetsNotFound(t *testing.T) {
	cases := []struct {
		name, caller string
		code         int
		errCode      string
	}{
		{"org B reaches org A's job", mediaKeyB, http.StatusNotFound, ""},
		// A key with no org is refused before any job is looked up.
		{"a key with no org reaches org A's job", mediaKeyNoOrg, http.StatusForbidden, "missing_org"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newMediaVideoServer(t, true)
			id := submitVideoAs(t, srv, mediaKeyA)
			before := mediaCall(t, srv, "GET", "/v1/videos/"+id, mediaKeyA, "")
			if before.Code != http.StatusOK {
				t.Fatalf("owner GET: status %d body %s", before.Code, before.Body.String())
			}
			status := mediaDecode(t, before)["status"]
			for _, method := range []string{"GET", "DELETE"} {
				w := mediaCall(t, srv, method, "/v1/videos/"+id, tc.caller, "")
				if w.Code != tc.code || (tc.errCode != "" && errorCode(t, w) != tc.errCode) {
					t.Errorf("%s: status %d, want %d %s; body %s", method, w.Code, tc.code, tc.errCode, w.Body.String())
				}
			}
			// The refused delete left the job as it was for its owner.
			w := mediaCall(t, srv, "GET", "/v1/videos/"+id, mediaKeyA, "")
			if w.Code != http.StatusOK {
				t.Fatalf("owner GET after refused DELETE: status %d body %s", w.Code, w.Body.String())
			}
			if got := mediaDecode(t, w)["status"]; got != status {
				t.Errorf("job status %v after another key's DELETE, want %v", got, status)
			}
		})
	}
	// A job with no org cannot be made: a key with no org cannot create one.
	t.Run("a key with no org cannot create a job", func(t *testing.T) {
		srv := newMediaVideoServer(t, true)
		if w := videoPost(t, srv, mediaKeyNoOrg, videoServedBody); w.Code != http.StatusForbidden || errorCode(t, w) != "missing_org" {
			t.Errorf("submit: status %d body %s, want 403 missing_org", w.Code, w.Body.String())
		}
	})
}

func TestVideoJobs_OwnerCanGetAndDelete(t *testing.T) {
	srv := newMediaVideoServer(t, true)
	id := submitVideoAs(t, srv, mediaKeyA)
	if w := mediaCall(t, srv, "GET", "/v1/videos/"+id, mediaKeyA, ""); w.Code != http.StatusOK {
		t.Errorf("GET own job: status %d body %s", w.Code, w.Body.String())
	}
	// A delete is local: the job leaves the owner's view and its accounting
	// is kept.
	w := mediaCall(t, srv, "DELETE", "/v1/videos/"+id, mediaKeyA, "")
	if w.Code != http.StatusOK || mediaDecode(t, w)["deleted"] != true {
		t.Errorf("DELETE own job: status %d body %s", w.Code, w.Body.String())
	}
	if w := mediaCall(t, srv, "GET", "/v1/videos/"+id, mediaKeyA, ""); w.Code != http.StatusNotFound {
		t.Errorf("GET after DELETE: status %d, want 404; body %s", w.Code, w.Body.String())
	}
	// A key with no org owns no video job: its submit is refused.
	if w := videoPost(t, srv, mediaKeyNoOrg, videoServedBody); w.Code != http.StatusForbidden || errorCode(t, w) != "missing_org" {
		t.Errorf("submit with a key with no org: status %d body %s, want 403 missing_org", w.Code, w.Body.String())
	}
}

// On this branch only a key with no org (an internal key) can create a
// scheduled job, and its job has no org; an org key is refused and stores
// nothing.
func TestScheduledJobs_CreateRecordsCallerOrg(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	for key, wantOrg := range map[string]string{mediaKeyA: mediaOrgA, mediaKeyB: mediaOrgB, mediaKeyNoOrg: ""} {
		w := mediaCall(t, srv, "POST", "/v1/scheduled", key,
			`{"delay":"1h","request":{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}}`)
		if wantOrg != "" {
			if w.Code != http.StatusBadRequest || errorCode(t, w) != "deferred_run_unavailable" {
				t.Errorf("scheduled job with org key %q: status %d body %s, want 400 deferred_run_unavailable", key, w.Code, w.Body.String())
			}
			if jobs, _ := srv.handlers.scheduledStore.ListByOrg(wantOrg, "", 10); len(jobs) != 0 {
				t.Errorf("a refused scheduled request stored %d jobs for org %q", len(jobs), wantOrg)
			}
			continue
		}
		if w.Code != http.StatusCreated {
			t.Fatalf("submit scheduled with key %q: status %d body %s", key, w.Code, w.Body.String())
		}
		id := mediaDecode(t, w)["id"].(string)
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
		{"org B reaches org A's job", mediaKeyA, mediaKeyB},
		{"a key with no org reaches org A's job", mediaKeyA, mediaKeyNoOrg},
		{"org A reaches a job that has no org", mediaKeyNoOrg, mediaKeyA},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newMediaScopeServer(t, true)
			id := submitScheduledAs(t, srv, tc.owner)
			if w := mediaCall(t, srv, "GET", "/v1/scheduled/"+id, tc.caller, ""); w.Code != http.StatusNotFound {
				t.Errorf("GET: status %d, want 404; body %s", w.Code, w.Body.String())
			}
			// A pending job is cancellable, so anything but 404 here (409
			// included) tells the caller the job exists.
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

// With auth off (OSS, single tenant) there are no orgs. Scheduled jobs have
// none and every request reaches all of them, as before. Video needs an
// organization API key, so every video route refuses a request without one:
// 403 missing_org, and nothing is stored.
func TestMediaJobs_AuthDisabledKeepsSingleTenantBehaviour(t *testing.T) {
	srv := newMediaVideoServer(t, false)

	if w := videoPost(t, srv, "", videoServedBody); w.Code != http.StatusForbidden || errorCode(t, w) != "missing_org" {
		t.Errorf("video submit: status %d body %s, want 403 missing_org", w.Code, w.Body.String())
	}
	for _, rq := range [][2]string{{"GET", "/v1/videos"}, {"GET", "/v1/videos/video_any"}, {"DELETE", "/v1/videos/video_any"}} {
		if w := mediaCall(t, srv, rq[0], rq[1], "", ""); w.Code != http.StatusForbidden || errorCode(t, w) != "missing_org" {
			t.Errorf("video %s %s: status %d body %s, want 403 missing_org", rq[0], rq[1], w.Code, w.Body.String())
		}
	}
	if jobs, err := videoStoreOf(t, srv.handlers).GetActiveJobs(); err != nil || len(jobs) != 0 {
		t.Errorf("video jobs %d (%v), want 0", len(jobs), err)
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
