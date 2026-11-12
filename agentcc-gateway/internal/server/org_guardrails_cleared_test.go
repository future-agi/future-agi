package server

// Release round 6B, fix (a). When an org's last guardrail policy that holds a
// check is removed, the control plane now pushes the section below in place of
// the old checks (futureagi/agentcc/tests/test_guardrail_section_emptied.py
// asserts EMPTY_PUSHED, this exact section). Before, its push failed the
// gateway contract and the gateway kept the old checks. This test shows the
// other half: the gateway decodes the pushed section through the admin PUT,
// as main.go wires it (the change callback clears the guardrail plugin's
// per-org cache), and stops running the old check.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/orgfactory"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The org's section while its policy holds a blocking blocklist (the check
// BLOCKLIST_PUSHED in the Django test).
const djangoPushedBlocklistSection = `{"guardrails": {"checks": {"keyword-blocklist": {
  "enabled": true, "action": "block", "confidence_threshold": 0.8,
  "config": {"words": ["r6b-forbidden"]}}}}}`

// The section Django pushes once no active policy holds a check.
const djangoPushedEmptySection = `{"guardrails": {"checks": {}}}`

func TestRemovingTheLastOrgCheckStopsItRunning(t *testing.T) {
	const org = "org-r6b-cleared"
	store := tenant.NewStore()
	plugin := guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{}, orgfactory.New(nil), nil, store)
	h := NewOrgConfigHandlers(store, "admin-test-token", nil)
	h.SetOnConfigChange(plugin.InvalidateDynamicCache)

	put := func(body string) {
		t.Helper()
		req := httptest.NewRequest("PUT", "/-/orgs/"+org+"/config?org_id="+org, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer admin-test-token")
		w := httptest.NewRecorder()
		h.SetOrgConfig(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("PUT answered %d: %s", w.Code, w.Body.String())
		}
	}
	ask := func(prompt string) *models.APIError {
		t.Helper()
		raw, _ := json.Marshal(prompt)
		return plugin.ProcessRequest(context.Background(), &models.RequestContext{
			RequestID: "r6b-cleared",
			Metadata:  map[string]string{"org_id": org},
			Timings:   map[string]time.Duration{},
			Request: &models.ChatCompletionRequest{
				Model:    "gpt-4",
				Messages: []models.Message{{Role: "user", Content: raw}},
			},
		}).Error
	}

	put(djangoPushedBlocklistSection)
	if err := ask("please say r6b-forbidden"); err == nil || err.Status != http.StatusForbidden {
		t.Fatalf("with the policy's check pushed, the prompt was not blocked: %v", err)
	}

	put(djangoPushedEmptySection)
	cfg := store.Get(org)
	if cfg == nil || cfg.Guardrails == nil || len(cfg.Guardrails.Checks) != 0 {
		t.Fatalf("the empty section did not replace the org's checks: %+v", cfg)
	}
	if err := ask("please say r6b-forbidden"); err != nil {
		t.Fatalf("after the empty section was pushed, the old check still ran: %d %s", err.Status, err.Message)
	}
}
