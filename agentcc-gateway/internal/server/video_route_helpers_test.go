package server

// The line's video route tests on video as an operator configures it
// (withDevVideo): the gateway serves /v1/videos with a Redis job store and a
// video provider, and a video request carries an Idempotency-Key and a model
// that provider serves.

import (
	"bytes"
	"fmt"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

// videoServedBody is a video request the served provider accepts.
const videoServedBody = `{"model":"` + videoTestModel + `","prompt":"a cat"}`

var videoIdempotency atomic.Int64

// nextVideoIdempotencyKey is a fresh Idempotency-Key: each submit in a test
// is its own job.
func nextVideoIdempotencyKey() string {
	return fmt.Sprintf("video-route-%d", videoIdempotency.Add(1))
}

// servingVideo gives g's gateway the video set-up the first time a test
// sends it a /v1/videos request, and returns headers with an
// Idempotency-Key added unless the test set one.
func (g *rf17Gateway) servingVideo(t *testing.T, headers map[string]string) map[string]string {
	t.Helper()
	if g.srv.handlers.videoStore == nil {
		withDevVideo(t, g.srv)
	}
	out := map[string]string{"Idempotency-Key": nextVideoIdempotencyKey()}
	for k, v := range headers {
		out[k] = v
	}
	return out
}

// newMediaVideoServer is newMediaScopeServer serving video.
func newMediaVideoServer(t *testing.T, authEnabled bool) *Server {
	t.Helper()
	srv := newMediaScopeServer(t, authEnabled)
	withDevVideo(t, srv)
	return srv
}

// newJobRoutesVideoServer is newJobRoutesServer serving video.
func newJobRoutesVideoServer(t *testing.T) *Server {
	t.Helper()
	srv := newJobRoutesServer(t, true)
	withDevVideo(t, srv)
	return srv
}

// videoPost sends a video submit with key (and an Idempotency-Key) through
// the whole handler chain.
func videoPost(t *testing.T, srv *Server, key, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("POST", "/v1/videos", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Idempotency-Key", nextVideoIdempotencyKey())
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}
