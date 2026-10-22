package server

import (
	"net/http"
	"testing"
)

// TestScheduledRoutesAll501: config validation refuses
// routing.scheduled.enabled, so every
// /v1/scheduled route answers 501 endpoint_not_configured, through the
// gateway's whole handler chain with a valid key.
func TestScheduledRoutesAll501(t *testing.T) {
	srv := newJobRoutesServer(t, false)
	if srv.cfg.Routing.Scheduled.Enabled || srv.handlers.scheduledStore != nil {
		t.Fatal("the default config enables scheduled completions")
	}
	routes := []struct{ method, path, body string }{
		{"POST", "/v1/scheduled", `{"delay":"1m","request":{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}}`},
		{"GET", "/v1/scheduled", ""},
		{"GET", "/v1/scheduled/sched-any", ""},
		{"DELETE", "/v1/scheduled/sched-any", ""},
	}
	for _, r := range routes {
		w := jobRoutesCall(srv, videoKeyA, r.method, r.path, r.body)
		if w.Code != http.StatusNotImplemented || errorCode(t, w) != "endpoint_not_configured" {
			t.Errorf("%s %s: status %d, body %s; want 501 endpoint_not_configured", r.method, r.path, w.Code, w.Body)
		}
	}
}
