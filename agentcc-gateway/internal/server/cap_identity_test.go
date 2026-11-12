package server

import (
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestCapStableKeyRenamePreservesPolicy(t *testing.T) {
	s := newJobRoutesServer(t, true)
	cfg := capRouteConfig(t, s, true)
	original := s.keyStore.Authenticate(videoKeyA)
	before, err := capbudget.ResolveAdmission(s.keyStore.Identity(original), cfg, time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	name := "renamed-display-name"
	if _, err := s.keyStore.Update(original.ID, &name, nil, nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	renamed := s.keyStore.Authenticate(videoKeyA)
	after, err := capbudget.ResolveAdmission(s.keyStore.Identity(renamed), cfg, time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if before == nil || after == nil || before.Key() != after.Key() || before.Org() != after.Org() || renamed.Name != name {
		t.Fatal("key rename changed authoritative policy identity")
	}
}

func TestCapHeaderAndMetadataSpoofing(t *testing.T) {
	limit := 0.05
	cfg := &tenant.OrgConfig{Meta: &tenant.ConfigMeta{Version: 1}, Budgets: &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: true, PolicyRevision: 1, NoMaxTokens: "clamp", Agents: map[string]*tenant.AdmissionCapConfig{"bound": {Limit: &limit, ActivatedAt: "2026-10-27T00:00:00Z"}}}, Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{KeyBindings: map[string]string{"stable": "bound"}}}}
	for _, tc := range []struct {
		name, body, header string
		denied             bool
	}{
		{"missing-alias", agentBody, "", false},
		{"matching-header", agentBody, "bound", false},
		{"conflicting-header", agentBody, "other", true},
		{"metadata-spoof", `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}],"metadata":{"agent_id":"other"}}`, "", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			call := newAgentCall(t, tc.body, "x-agentcc-agent-id", tc.header)
			parseErr := call.parse()
			admission, e := capbudget.ResolveAdmission(auth.Identity{OrgID: "org", KeyID: "stable"}, cfg, time.Now().Add(time.Minute))
			if e != nil {
				t.Fatal(e)
			}
			call.rc.Metadata["org_id"] = "org"
			request := httptest.NewRequest("POST", "/v1/chat/completions", nil)
			request = request.WithContext(capbudget.WithAdmission(request.Context(), admission))
			refused := parseErr != nil || bindCapChat(request, call.rc, cfg) != nil
			// Plain metadata is not an authoritative alias. Whether the parser ignores
			// or rejects it, it must never change the cap's administrative identity.
			if tc.name == "metadata-spoof" {
				if admission.Agent() != "bound" {
					t.Fatal("metadata selected cap")
				}
				return
			}
			if refused != tc.denied {
				t.Fatalf("refused=%v parse=%v", refused, parseErr)
			}
		})
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	if e := bindCapChat(httptest.NewRequest("POST", "/v1/chat/completions", nil), rc, cfg); e == nil {
		t.Fatal("direct handler manufactured trusted admission")
	}
}
