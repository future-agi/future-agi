package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func capRouteConfig(t *testing.T, s *Server, on bool) *tenant.OrgConfig {
	t.Helper()
	key := s.keyStore.Authenticate(videoKeyA)
	if key == nil {
		t.Fatal("synthetic route key unavailable")
	}
	limit := 0.05
	return &tenant.OrgConfig{Meta: &tenant.ConfigMeta{Version: 1},
		Budgets: &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: on, PolicyRevision: 1, NoMaxTokens: "clamp",
			KeyIDs: map[string]*tenant.AdmissionCapConfig{key.ID: {Limit: &limit, Period: "daily"}}}}
}
func TestG8CapUnsupportedVideoBoundary(t *testing.T) {
	s := newJobRoutesServer(t, true)
	s.TenantStore.Set(videoOrgA, capRouteConfig(t, s, true))
	w := jobRoutesCall(s, videoKeyA, "POST", "/v1/videos", videoBody)
	jobs, err := s.handlers.videoStore.GetActiveJobs()
	if err != nil {
		t.Fatal(err)
	}
	if w.Code != http.StatusPaymentRequired || errorCode(t, w) != "call_cap_unenforceable" || len(jobs) != 0 {
		t.Fatalf("status=%d jobs=%d body=%s", w.Code, len(jobs), w.Body)
	}
}
func TestCapDefaultOffVideoControl(t *testing.T) {
	s := newJobRoutesServer(t, true)
	s.TenantStore.Set(videoOrgA, capRouteConfig(t, s, false))
	w := jobRoutesCall(s, videoKeyA, "POST", "/v1/videos", videoBody)
	jobs, err := s.handlers.videoStore.GetActiveJobs()
	if err != nil {
		t.Fatal(err)
	}
	if w.Code != http.StatusAccepted || len(jobs) != 1 {
		t.Fatalf("status=%d jobs=%d body=%s", w.Code, len(jobs), w.Body)
	}
}
func TestCapAsyncChatRefusedBeforeQueue(t *testing.T) {
	for _, body := range []string{
		`{"model":"fake-video/clip-1","messages":[{"role":"user","content":"synthetic"}],"stream":true}`,
		`{"model":"fake-video/clip-1","messages":[{"role":"user","content":"synthetic"}],"background":true}`,
	} {
		t.Run(body, func(t *testing.T) {
			s := newJobRoutesServer(t, true)
			s.TenantStore.Set(videoOrgA, capRouteConfig(t, s, true))
			w := jobRoutesCall(s, videoKeyA, "POST", "/v1/chat/completions", body)
			if w.Code != http.StatusPaymentRequired || errorCode(t, w) != "call_cap_unenforceable" {
				t.Fatalf("status=%d body=%s", w.Code, w.Body)
			}
		})
	}
}
