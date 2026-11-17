package server

import (
	"context"
	"encoding/json"
	"errors"
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

// Shared helpers for the job-route tests (video, scheduled, admission caps):
// keys in two orgs and one with none, a registered video provider that is
// never called, one request through the whole handler chain, and the error
// code of an error body.

const (
	videoOrgA  = "org-video-a"
	videoOrgB  = "org-video-b"
	videoKeyA  = "sk-video-a"
	videoKeyB  = "sk-video-b"
	videoKeyNo = "sk-video-no-org"
	videoBody  = `{"model":"fake-video/clip-1","prompt":"a private prompt"}`
)

// fakeVideoProvider is a registered provider that implements
// providers.VideoProvider. Nothing calls it in these tests.
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
	releaseTestServer(t, srv)
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

// errorCode is the error.code of an error response body.
func errorCode(t *testing.T, w *httptest.ResponseRecorder) string {
	t.Helper()
	var resp models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("not an error body: %s", w.Body)
	}
	return resp.Error.Code
}

// videoStoreOf is the gateway's video job store. Video jobs are stored only
// when video is enabled (video.enabled), so a test that drives a gateway
// without it fails here, saying so, instead of on a nil store.
func videoStoreOf(t testing.TB, h *Handlers) video.Store {
	t.Helper()
	if h.videoStore == nil {
		t.Fatal("this gateway has no video job store: video is not enabled here")
	}
	return h.videoStore
}
