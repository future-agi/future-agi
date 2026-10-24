package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

const (
	videoOrgA  = "org-video-a"
	videoOrgB  = "org-video-b"
	videoKeyA  = "sk-video-a"
	videoKeyB  = "sk-video-b"
	videoKeyNo = "sk-video-no-org"
	videoBody  = `{"model":"fake-video/clip-1","prompt":"a private prompt"}`
)

// fakeVideoProvider is a registered provider that implements
// providers.VideoProvider, so the video routes count as configured. Nothing
// calls it: the routes store jobs, and no executor runs them in this build.
type fakeVideoProvider struct{}

var errFakeVideo = errors.New("fake video provider: not called in these tests")

func (fakeVideoProvider) ID() string { return "fake-video" }
func (fakeVideoProvider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, errFakeVideo
}
func (fakeVideoProvider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	errs := make(chan error, 1)
	errs <- errFakeVideo
	close(errs)
	return nil, errs
}
func (fakeVideoProvider) ListModels(context.Context) ([]models.ModelObject, error) { return nil, nil }
func (fakeVideoProvider) Close() error                                             { return nil }
func (fakeVideoProvider) SubmitVideo(context.Context, *models.VideoGenerationRequest) (*models.VideoSubmitResponse, error) {
	return nil, errFakeVideo
}
func (fakeVideoProvider) GetVideoStatus(context.Context, string) (*models.VideoStatusResponse, error) {
	return nil, errFakeVideo
}
func (fakeVideoProvider) GetVideoContent(context.Context, string, int) (string, error) {
	return "", errFakeVideo
}
func (fakeVideoProvider) CancelVideo(context.Context, string) error { return errFakeVideo }

var _ providers.VideoProvider = fakeVideoProvider{}

// newJobRoutesServer is a gateway with key auth on and three keys: one per
// org and one with no org, for the video and scheduled route tests.
// withProvider registers a video provider.
func newJobRoutesServer(t *testing.T, withProvider bool) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "video-a", Key: videoKeyA, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": videoOrgA}},
		{Name: "video-b", Key: videoKeyB, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": videoOrgB}},
		{Name: "video-no-org", Key: videoKeyNo, Owner: "t", KeyType: "byok"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	if withProvider {
		registry.RegisterProvider("fake-video", fakeVideoProvider{}, []string{"clip-1"})
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	srv.ready.Store(true)
	return srv
}

// jobRoutesCall serves one request through the gateway's whole handler chain
// (key auth included) with the given key.
func jobRoutesCall(srv *Server, key, method, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// submitVideo submits a job as key and returns its id.
func submitVideo(t *testing.T, srv *Server, key string) string {
	t.Helper()
	w := jobRoutesCall(srv, key, "POST", "/v1/videos", videoBody)
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit as %s: status %d, body %s", key, w.Code, w.Body)
	}
	var resp struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || resp.ID == "" {
		t.Fatalf("submit as %s: no job id in %s", key, w.Body)
	}
	return resp.ID
}

// listedVideoIDs lists the jobs key sees.
func listedVideoIDs(t *testing.T, srv *Server, key string) []string {
	t.Helper()
	w := jobRoutesCall(srv, key, "GET", "/v1/videos", "")
	if w.Code != http.StatusOK {
		t.Fatalf("list as %s: status %d, body %s", key, w.Code, w.Body)
	}
	var resp struct {
		Data  []struct{ ID string } `json:"data"`
		Total int                   `json:"total"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("list as %s: %v (%s)", key, err, w.Body)
	}
	ids := make([]string, 0, len(resp.Data))
	for _, d := range resp.Data {
		ids = append(ids, d.ID)
	}
	if resp.Total != len(ids) {
		t.Fatalf("list as %s: total %d but %d rows", key, resp.Total, len(ids))
	}
	return ids
}

// errorCode is the error.code of an error response body.
func errorCode(t *testing.T, w *httptest.ResponseRecorder) string {
	t.Helper()
	var resp models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("not an error body: %s", w.Body)
	}
	return resp.Error.Code
}

var videoRoutes = []struct{ method, path, body string }{
	{"POST", "/v1/videos", videoBody},
	{"GET", "/v1/videos", ""},
	{"GET", "/v1/videos/video-any", ""},
	{"DELETE", "/v1/videos/video-any", ""},
}

// TestVideosUnconfigured501: with no video provider registered, every
// /v1/videos route answers 501 endpoint_not_configured, and a submit stores
// nothing. A gateway with no video store answers the same.
func TestVideosUnconfigured501(t *testing.T) {
	srv := newJobRoutesServer(t, false)
	for _, r := range videoRoutes {
		w := jobRoutesCall(srv, videoKeyA, r.method, r.path, r.body)
		if w.Code != http.StatusNotImplemented || errorCode(t, w) != "endpoint_not_configured" {
			t.Errorf("%s %s: status %d, body %s; want 501 endpoint_not_configured", r.method, r.path, w.Code, w.Body)
		}
	}
	if jobs, _ := srv.handlers.videoStore.GetActiveJobs(); len(jobs) != 0 {
		t.Fatalf("the unconfigured submit stored %d jobs", len(jobs))
	}
	for _, org := range []string{videoOrgA, ""} {
		if _, total, _ := srv.handlers.videoStore.ListByOrg(org, video.VideoListFilters{Limit: 100}); total != 0 {
			t.Fatalf("the store holds %d jobs for org %q", total, org)
		}
	}

	noStore := newJobRoutesServer(t, true)
	noStore.handlers.videoStore = nil
	for _, r := range videoRoutes {
		w := jobRoutesCall(noStore, videoKeyA, r.method, r.path, r.body)
		if w.Code != http.StatusNotImplemented || errorCode(t, w) != "endpoint_not_configured" {
			t.Errorf("no store, %s %s: status %d, body %s", r.method, r.path, w.Code, w.Body)
		}
	}
}

// TestVideosOrgScoped: a submitted job carries the caller's org and key id;
// each org lists, reads and deletes only its own jobs; and a key with no org
// is the no-org tenant, not a wildcard.
func TestVideosOrgScoped(t *testing.T) {
	srv := newJobRoutesServer(t, true)
	jobA := submitVideo(t, srv, videoKeyA)
	jobB := submitVideo(t, srv, videoKeyB)

	stored, err := srv.handlers.videoStore.Get(jobA)
	if err != nil {
		t.Fatalf("job %s not stored: %v", jobA, err)
	}
	keyA := srv.keyStore.Lookup(videoKeyA)
	if stored.OrgID != videoOrgA || stored.KeyID != keyA.ID {
		t.Fatalf("job %s stored with org %q key %q, want %q and %q", jobA, stored.OrgID, stored.KeyID, videoOrgA, keyA.ID)
	}

	if got := listedVideoIDs(t, srv, videoKeyA); len(got) != 1 || got[0] != jobA {
		t.Errorf("org A lists %v, want only %s", got, jobA)
	}
	if got := listedVideoIDs(t, srv, videoKeyB); len(got) != 1 || got[0] != jobB {
		t.Errorf("org B lists %v, want only %s", got, jobB)
	}
	if got := listedVideoIDs(t, srv, videoKeyNo); len(got) != 0 {
		t.Errorf("a key with no org lists %v, want nothing", got)
	}

	if w := jobRoutesCall(srv, videoKeyA, "GET", "/v1/videos/"+jobB, ""); w.Code != http.StatusNotFound {
		t.Errorf("org A read org B's job: status %d, body %s", w.Code, w.Body)
	}
	if w := jobRoutesCall(srv, videoKeyNo, "GET", "/v1/videos/"+jobB, ""); w.Code != http.StatusNotFound {
		t.Errorf("a key with no org read org B's job: status %d, body %s", w.Code, w.Body)
	}
	if w := jobRoutesCall(srv, videoKeyA, "DELETE", "/v1/videos/"+jobB, ""); w.Code != http.StatusNotFound {
		t.Errorf("org A deleted org B's job: status %d, body %s", w.Code, w.Body)
	}
	if w := jobRoutesCall(srv, videoKeyB, "GET", "/v1/videos/"+jobB, ""); w.Code != http.StatusOK ||
		!strings.Contains(w.Body.String(), `"status":"queued"`) {
		t.Errorf("org B's job changed after org A's delete: status %d, body %s", w.Code, w.Body)
	}

	if w := jobRoutesCall(srv, videoKeyA, "GET", "/v1/videos/"+jobA, ""); w.Code != http.StatusOK ||
		!strings.Contains(w.Body.String(), "a private prompt") {
		t.Errorf("org A cannot read its own job: status %d, body %s", w.Code, w.Body)
	}
	if w := jobRoutesCall(srv, videoKeyA, "DELETE", "/v1/videos/"+jobA, ""); w.Code != http.StatusOK {
		t.Errorf("org A cannot delete its own job: status %d, body %s", w.Code, w.Body)
	}
	if _, err := srv.handlers.videoStore.Get(jobA); err == nil {
		t.Error("org A's delete left its job in the store")
	}

	jobNo := submitVideo(t, srv, videoKeyNo)
	if got := listedVideoIDs(t, srv, videoKeyB); len(got) != 1 || got[0] != jobB {
		t.Errorf("org B lists %v after a no-org submit, want only %s", got, jobB)
	}
	if w := jobRoutesCall(srv, videoKeyB, "GET", "/v1/videos/"+jobNo, ""); w.Code != http.StatusNotFound {
		t.Errorf("org B read the no-org key's job: status %d, body %s", w.Code, w.Body)
	}
}

// TestVideosForeignIdSame404: reading or deleting another org's job answers
// exactly what an unknown id answers, so a job id reveals nothing.
func TestVideosForeignIdSame404(t *testing.T) {
	srv := newJobRoutesServer(t, true)
	jobB := submitVideo(t, srv, videoKeyB)
	for _, method := range []string{"GET", "DELETE"} {
		foreign := jobRoutesCall(srv, videoKeyA, method, "/v1/videos/"+jobB, "")
		unknown := jobRoutesCall(srv, videoKeyA, method, "/v1/videos/video-does-not-exist", "")
		if foreign.Code != http.StatusNotFound || unknown.Code != http.StatusNotFound {
			t.Fatalf("%s: foreign %d, unknown %d; want 404 for both", method, foreign.Code, unknown.Code)
		}
		if foreign.Body.String() != unknown.Body.String() {
			t.Errorf("%s: foreign body %q differs from unknown body %q", method, foreign.Body, unknown.Body)
		}
	}
	if _, err := srv.handlers.videoStore.Get(jobB); err != nil {
		t.Fatalf("org B's job is gone after org A's delete: %v", err)
	}
}

// TestVideoCallerFailsClosed: with auth on, a request whose key is unknown or
// revoked by the time the handler runs (after KeyAuth let it in)
// gets 401 and stores nothing; it never falls into the no-org tenant. With
// auth off there is one tenant, and its jobs carry no org.
func TestVideoCallerFailsClosed(t *testing.T) {
	srv := newJobRoutesServer(t, true)
	h := srv.handlers
	direct := func(key string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/v1/videos", strings.NewReader(videoBody))
		req.Header.Set("Authorization", "Bearer "+key)
		w := httptest.NewRecorder()
		h.SubmitVideo(w, req)
		return w
	}
	if w := direct("sk-video-unknown"); w.Code != http.StatusUnauthorized {
		t.Errorf("unknown key: status %d, body %s; want 401", w.Code, w.Body)
	}
	if !srv.keyStore.Revoke(srv.keyStore.Lookup(videoKeyB).ID) {
		t.Fatal("revoking org B's key failed")
	}
	if w := direct(videoKeyB); w.Code != http.StatusUnauthorized {
		t.Errorf("revoked key: status %d, body %s; want 401", w.Code, w.Body)
	}
	if jobs, _ := h.videoStore.GetActiveJobs(); len(jobs) != 0 {
		t.Fatalf("refused submits stored %d jobs", len(jobs))
	}

	// Auth off, as the gateway builds it: no key store in the handlers and no
	// auth plugin in the pipeline (cmd/agentcc adds it only with auth on).
	// SubmitVideo runs the pipeline's auth and guardrail plugins on the
	// prompt before it stores the job.
	h.keyStore = nil
	h.engine = pipeline.NewEngine()
	if w := direct(""); w.Code != http.StatusAccepted {
		t.Fatalf("auth off: status %d, body %s; want 202", w.Code, w.Body)
	}
	jobs, total, _ := h.videoStore.ListByOrg("", video.VideoListFilters{Limit: 10})
	if total != 1 || jobs[0].OrgID != "" || jobs[0].KeyID != "" {
		t.Fatalf("auth off: the no-org tenant holds %d jobs (%+v), want one with no org or key", total, jobs)
	}
}
