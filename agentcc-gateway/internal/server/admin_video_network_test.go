package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Admin routes that read or change stored video jobs follow the global network
// rules, as the batch result routes do.
func TestAdminVideoJobRoutesHonourGlobalNetworkRules(t *testing.T) {
	for _, route := range []struct{ method, path, body string }{
		{"POST", "/-/admin/video/jobs/video_missing/attach", `{"provider_job_id":"cgt-1"}`},
		{"GET", "/-/admin/video/unsettled", ""},
	} {
		t.Run(route.path, func(t *testing.T) {
			s := asyncNetworkTestServer(t, config.IPACLConfig{Enabled: true, Allow: []string{"192.0.2.10"}}, nil, nil, false)
			send := func(source, credential string) *httptest.ResponseRecorder {
				r := httptest.NewRequest(route.method, route.path, strings.NewReader(route.body))
				r.RemoteAddr = source
				r.Header.Set("Authorization", "Bearer "+credential)
				w := httptest.NewRecorder()
				s.httpServer.Handler.ServeHTTP(w, r)
				return w
			}
			if w := send("192.0.2.11:1234", asyncNetworkKey); w.Code != http.StatusUnauthorized {
				t.Errorf("API key got %d; want 401", w.Code)
			}
			if w := send("192.0.2.11:1234", "synthetic-network-admin"); w.Code != http.StatusForbidden {
				t.Errorf("admin outside the network rules got %d; want 403: %s", w.Code, w.Body.String())
			}
			// Video is not configured in this server, so an allowed admin reaches 501.
			if w := send("192.0.2.10:1234", "synthetic-network-admin"); w.Code != http.StatusNotImplemented {
				t.Errorf("allowed admin got %d; want 501: %s", w.Code, w.Body.String())
			}
		})
	}
}
