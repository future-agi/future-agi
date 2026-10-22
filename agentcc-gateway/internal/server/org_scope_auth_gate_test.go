package server

import (
	"bufio"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/video"
)

// The org-scoped object handlers (files, stored responses, async, video and
// scheduled jobs) serve the org of the key KeyAuth authenticated, and nothing
// else. These tests pin that for every path spelling, a revoked key, a
// handler run without KeyAuth, a key update that names another org, a key
// update concurrent with a request, and a response id the provider chose.

// rawRequest sends one request over a real TCP connection with the target
// exactly as given: net/http clients would clean "//v1/..." first, and the
// server does not.
func rawRequest(t *testing.T, addr, method, target, key string) (int, string) {
	t.Helper()
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	auth := ""
	if key != "" {
		auth = "Authorization: Bearer " + key + "\r\n"
	}
	fmt.Fprintf(conn, "%s %s HTTP/1.1\r\nHost: gateway\r\n%sConnection: close\r\n\r\n", method, target, auth)
	resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(body)
}

// seedVideoJob stores a finished video job directly: nothing runs video jobs
// in this build.
func seedVideoJob(t *testing.T, srv *Server, id, orgID string) {
	t.Helper()
	now := time.Now()
	if err := srv.handlers.videoStore.Create(&video.VideoJob{
		ID: id, OrgID: orgID, Status: video.StatusCompleted, Model: "video-model",
		CreatedAt: now, ExpiresAt: now.Add(time.Hour),
	}); err != nil {
		t.Fatal(err)
	}
}

var nonCanonicalPrefixes = []string{"//", "///"}

// TestOrgScopedObjects_NonCanonicalPathsNeedAValidKey sends "//v1/..." and
// "///v1/..." over TCP with no key and with a revoked key. Every request must
// be refused with 401 and must leave the object in place.
func TestOrgScopedObjects_NonCanonicalPathsNeedAValidKey(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	h := srv.handlers
	ts := httptest.NewServer(srv.httpServer.Handler)
	defer ts.Close()
	addr := strings.TrimPrefix(ts.URL, "http://")

	aFile, noOrgFile := uploadFileAs(t, srv, objKeyA), uploadFileAs(t, srv, objKeyNoOrg)
	aResp, noOrgResp := createResponseAs(t, srv, objKeyA), createResponseAs(t, srv, objKeyNoOrg)
	aJob, noOrgJob := submitAsyncAs(t, srv, objKeyA), submitAsyncAs(t, srv, objKeyNoOrg)
	seedVideoJob(t, srv, "video-org-a", objOrgA)
	seedVideoJob(t, srv, "video-no-org", "")

	targets := func(file, resp, job, vid string) [][2]string {
		return [][2]string{
			{"GET", "v1/files/" + file}, {"GET", "v1/files/" + file + "/content"}, {"DELETE", "v1/files/" + file},
			{"GET", "v1/responses/" + resp}, {"DELETE", "v1/responses/" + resp},
			{"GET", "v1/async/" + job}, {"DELETE", "v1/async/" + job},
			{"GET", "v1/videos/" + vid}, {"DELETE", "v1/videos/" + vid},
		}
	}

	send := func(who, key string, reqs [][2]string) {
		for _, prefix := range nonCanonicalPrefixes {
			for _, rq := range reqs {
				target := prefix + rq[1]
				if code, body := rawRequest(t, addr, rq[0], target, key); code != http.StatusUnauthorized {
					t.Errorf("%s: %s %s answered %d, want 401; body %s", who, rq[0], target, code, body)
				}
			}
		}
	}

	send("no key", "", targets(noOrgFile, noOrgResp, noOrgJob, "video-no-org"))
	send("no key", "", targets(aFile, aResp, aJob, "video-org-a"))

	keyA := h.keyStore.Lookup(objKeyA)
	if keyA == nil || !h.keyStore.Revoke(keyA.ID) {
		t.Fatal("could not revoke key A")
	}
	send("revoked org-A key", objKeyA, targets(aFile, aResp, aJob, "video-org-a"))

	for _, f := range []struct{ id, org string }{{aFile, objOrgA}, {noOrgFile, ""}} {
		if h.fileStore.Get(f.id, f.org) == nil {
			t.Errorf("file %s was deleted", f.id)
		}
	}
	for _, r := range []struct{ id, org string }{{aResp, objOrgA}, {noOrgResp, ""}} {
		if h.responsesStore.Get(r.id, r.org) == nil {
			t.Errorf("stored response %s was deleted", r.id)
		}
	}
	for _, id := range []string{aJob, noOrgJob} {
		if h.asyncStore.Get(id) == nil {
			t.Errorf("async job %s was deleted", id)
		}
	}
	for _, id := range []string{"video-org-a", "video-no-org"} {
		if job, err := h.videoStore.Get(id); err != nil || job == nil {
			t.Errorf("video job %s was deleted", id)
		}
	}
}

// TestScheduledJobs_NonCanonicalPathsNeedAValidKey is the same check for
// scheduled jobs, which need scheduled completions enabled.
func TestScheduledJobs_NonCanonicalPathsNeedAValidKey(t *testing.T) {
	srv := newMediaScopeServer(t, true)
	h := srv.handlers
	ts := httptest.NewServer(srv.httpServer.Handler)
	defer ts.Close()
	addr := strings.TrimPrefix(ts.URL, "http://")

	aJob, noOrgJob := submitScheduledAs(t, srv, mediaKeyA), submitScheduledAs(t, srv, mediaKeyNoOrg)

	send := func(who, key, job string) {
		for _, prefix := range nonCanonicalPrefixes {
			for _, method := range []string{"GET", "DELETE"} {
				target := prefix + "v1/scheduled/" + job
				if code, body := rawRequest(t, addr, method, target, key); code != http.StatusUnauthorized {
					t.Errorf("%s: %s %s answered %d, want 401; body %s", who, method, target, code, body)
				}
			}
		}
	}
	send("no key", "", noOrgJob)
	send("no key", "", aJob)
	keyA := h.keyStore.Lookup(mediaKeyA)
	if keyA == nil || !h.keyStore.Revoke(keyA.ID) {
		t.Fatal("could not revoke key A")
	}
	send("revoked org-A key", mediaKeyA, aJob)

	for _, id := range []string{aJob, noOrgJob} {
		job, err := h.scheduledStore.Get(id)
		if err != nil || job == nil || job.Status != "pending" {
			t.Errorf("scheduled job %s was cancelled or deleted: %+v", id, job)
		}
	}
}

// TestOrgScopedHandlers_RefuseRequestsKeyAuthDidNotAuthenticate runs the
// handlers without KeyAuth in front, with a key that is valid, then revoked.
// With auth on, a handler must not look the key up itself: it answers 401
// and leaves the object alone.
func TestOrgScopedHandlers_RefuseRequestsKeyAuthDidNotAuthenticate(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	h := srv.handlers
	aFile := uploadFileAs(t, srv, objKeyA)
	aResp := createResponseAs(t, srv, objKeyA)
	aJob := submitAsyncAs(t, srv, objKeyA)
	seedVideoJob(t, srv, "video-org-a", objOrgA)

	calls := []struct {
		name    string
		method  string
		target  string
		handler http.HandlerFunc
	}{
		{"ListFiles", "GET", "/v1/files", h.ListFiles},
		{"GetFile", "GET", "/v1/files/" + aFile + "?file_id=" + aFile, h.GetFile},
		{"GetFileContent", "GET", "/v1/files/" + aFile + "/content?file_id=" + aFile, h.GetFileContent},
		{"DeleteFile", "DELETE", "/v1/files/" + aFile + "?file_id=" + aFile, h.DeleteFile},
		{"GetResponse", "GET", "/v1/responses/" + aResp + "?id=" + aResp, h.GetResponse},
		{"DeleteResponse", "DELETE", "/v1/responses/" + aResp + "?id=" + aResp, h.DeleteResponse},
		{"GetAsyncJob", "GET", "/v1/async/" + aJob + "?job_id=" + aJob, h.GetAsyncJob},
		{"DeleteAsyncJob", "DELETE", "/v1/async/" + aJob + "?job_id=" + aJob, h.DeleteAsyncJob},
		{"ListVideos", "GET", "/v1/videos", h.ListVideos},
		{"GetVideoStatus", "GET", "/v1/videos/video-org-a?video_id=video-org-a", h.GetVideoStatus},
		{"DeleteVideo", "DELETE", "/v1/videos/video-org-a?video_id=video-org-a", h.DeleteVideo},
	}
	run := func(who string) {
		for _, c := range calls {
			req := httptest.NewRequest(c.method, c.target, nil)
			req.Header.Set("Authorization", "Bearer "+objKeyA)
			w := httptest.NewRecorder()
			c.handler(w, req)
			if w.Code != http.StatusUnauthorized {
				t.Errorf("%s: %s without KeyAuth answered %d, want 401; body %s", who, c.name, w.Code, w.Body.String())
			}
		}
	}
	run("valid org-A key")
	keyA := h.keyStore.Lookup(objKeyA)
	if keyA == nil || !h.keyStore.Revoke(keyA.ID) {
		t.Fatal("could not revoke key A")
	}
	run("revoked org-A key")

	if h.fileStore.Get(aFile, objOrgA) == nil {
		t.Error("file was deleted")
	}
	if h.responsesStore.Get(aResp, objOrgA) == nil {
		t.Error("stored response was deleted")
	}
	if job := h.asyncStore.Get(aJob); job == nil || job.Status == "cancelled" {
		t.Errorf("async job was cancelled or deleted: %+v", job)
	}
	if job, err := h.videoStore.Get("video-org-a"); err != nil || job == nil {
		t.Error("video job was deleted")
	}
}

// TestKeyAdminUpdate_CannotRepointAKeyAtAnotherOrg drives the admin
// PUT /-/keys/{id} (what Django's key update calls) with metadata.org_id set
// to org B on org A's key. It must be refused (409 reserved_metadata, the
// key store's ReservedMetadataError), and key A must still get 404 on org B's
// objects.
func TestKeyAdminUpdate_CannotRepointAKeyAtAnotherOrg(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	h := srv.handlers
	const adminToken = "test-admin-token"
	admin := NewKeyHandlers(h.keyStore, adminToken)
	keyA := h.keyStore.Lookup(objKeyA)
	if keyA == nil {
		t.Fatal("key A not in store")
	}

	put := func(body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("PUT", "/-/keys/"+keyA.ID+"?key_id="+keyA.ID, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+adminToken)
		w := httptest.NewRecorder()
		admin.UpdateKey(w, req)
		return w
	}

	bFile := uploadFileAs(t, srv, objKeyB)
	bResp := createResponseAs(t, srv, objKeyB)
	bJob := submitAsyncAs(t, srv, objKeyB)

	// The key store owns the refusal: 409 reserved_metadata.
	if w := put(`{"metadata":{"org_id":"` + objOrgB + `"}}`); w.Code != http.StatusConflict || !strings.Contains(w.Body.String(), "reserved_metadata") {
		t.Errorf("re-pointing key A at org B answered %d, want 409 reserved_metadata; body %s", w.Code, w.Body.String())
	}
	if got := h.keyStore.Get(keyA.ID).Metadata["org_id"]; got != objOrgA {
		t.Errorf("key A's org_id is %q after the refused update, want %q", got, objOrgA)
	}
	for _, p := range []string{"/v1/files/" + bFile, "/v1/files/" + bFile + "/content", "/v1/responses/" + bResp, "/v1/async/" + bJob} {
		if w := objCall(t, srv, "GET", p, objKeyA, "", nil); w.Code != http.StatusNotFound {
			t.Errorf("GET %s as key A after the update: %d, want 404", p, w.Code)
		}
	}
	if ids := listedIDs(t, objCall(t, srv, "GET", "/v1/files", objKeyA, "", nil)); ids[bFile] {
		t.Errorf("key A lists org B's file %s", bFile)
	}

	// Updates that leave the org alone still work.
	if w := put(`{"metadata":{"org_id":"` + objOrgA + `","service":"infra"}}`); w.Code != http.StatusOK {
		t.Errorf("update repeating key A's own org answered %d, want 200; body %s", w.Code, w.Body.String())
	}
	if got := h.keyStore.Get(keyA.ID).Metadata["service"]; got != "infra" {
		t.Errorf("metadata service is %q, want infra", got)
	}
}

// TestCallerOrg_DoesNotRaceAKeyUpdate runs admin key updates alongside
// requests that read the caller's org. It only proves anything under -race.
func TestCallerOrg_DoesNotRaceAKeyUpdate(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	h := srv.handlers
	keyA := h.keyStore.Lookup(objKeyA)
	if keyA == nil {
		t.Fatal("key A not in store")
	}
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		for i := 0; i < 500; i++ {
			h.keyStore.Update(keyA.ID, nil, nil, nil, nil, map[string]string{"note": fmt.Sprint(i)})
		}
	}()
	go func() {
		defer wg.Done()
		for i := 0; i < 250; i++ {
			for _, p := range []string{"/v1/videos", "/v1/files"} {
				if w := objCall(t, srv, "GET", p, objKeyA, "", nil); w.Code != http.StatusOK {
					t.Errorf("GET %s: %d", p, w.Code)
					return
				}
			}
		}
	}()
	wg.Wait()
}

// TestResponses_ProviderIDCollisionKeepsTheOwnersResponse: the response id
// comes from the upstream provider, which an org can configure. When org B's
// provider returns an id org A already stored, A keeps its response and B
// cannot read it. B's own response is stored beside it under
// B's copy of the id, so B reads its own response there, never A's.
func TestResponses_ProviderIDCollisionKeepsTheOwnersResponse(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	h := srv.handlers
	// The mock provider numbers its responses from 1.
	const collidingID = "resp_scope_1"
	h.responsesStore.Put(collidingID, objOrgA, []byte(`{"id":"resp_scope_1","owner":"a"}`))

	if got := createResponseAs(t, srv, objKeyB); got != collidingID {
		t.Fatalf("mock returned %q, want %q", got, collidingID)
	}

	stored := h.responsesStore.Get(collidingID, objOrgA)
	if stored == nil || !strings.Contains(string(stored.Body), `"owner":"a"`) {
		t.Errorf("org A's stored response was replaced: %+v", stored)
	}
	if w := objCall(t, srv, "GET", "/v1/responses/"+collidingID, objKeyA, "", nil); w.Code != http.StatusOK {
		t.Errorf("org A GET its response: %d, want 200", w.Code)
	}
	w := objCall(t, srv, "GET", "/v1/responses/"+collidingID, objKeyB, "", nil)
	if w.Code != http.StatusOK || strings.Contains(w.Body.String(), `"owner":"a"`) {
		t.Errorf("org B GET the colliding id: %d, want 200 with its own response, never org A's; body %s",
			w.Code, w.Body.String())
	}
}
