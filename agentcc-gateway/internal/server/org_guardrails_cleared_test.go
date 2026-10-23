package server

// When an org's last guardrail policy that holds a check is removed, the
// control plane pushes the section below in place of the old checks
// (futureagi/agentcc/tests/test_guardrail_section_emptied.py asserts
// EMPTY_PUSHED, this exact section). This test shows the gateway side: the
// gateway decodes the pushed section through the admin PUT, as
// main.go wires it (the change callback clears the guardrail plugin's per-org
// cache), and stops running the old check. The factory below builds the one
// rule the test pushes, as main.go's newDynamicGuardrailFactory does for
// "keyword-blocklist".

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The org's section while its policy holds a blocking blocklist (the check
// BLOCKLIST_PUSHED in the Django test).
const djangoPushedBlocklistSection = `{"guardrails": {"checks": {"keyword-blocklist": {
  "enabled": true, "action": "block", "confidence_threshold": 0.8,
  "config": {"words": ["t-forbidden"]}}}}}`

// The section Django pushes once no active policy holds a check.
const djangoPushedEmptySection = `{"guardrails": {"checks": {}}}`

// A section with an "enabled" flag and a checks list, which the admin PUT
// refuses (the contract has no "enabled" and wants a checks map).
const sectionWithEnabledFlag = `{"guardrails": {"enabled": false, "checks": []}}`

func TestRemovingTheLastOrgCheckStopsItRunning(t *testing.T) {
	const org = "org-cleared"
	store := tenant.NewStore()
	factory := func(name string, cfg map[string]interface{}) guardrails.Guardrail {
		if name == "keyword-blocklist" {
			return blocklist.New(cfg)
		}
		return nil
	}
	plugin := guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{}, factory, nil, store)
	h := NewOrgConfigHandlers(store, "admin-test-token", nil)
	h.SetOnConfigChange(plugin.InvalidateDynamicCache)

	put := func(body string) *httptest.ResponseRecorder {
		t.Helper()
		req := httptest.NewRequest("PUT", "/-/orgs/"+org+"/config?org_id="+org, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer admin-test-token")
		w := httptest.NewRecorder()
		h.SetOrgConfig(w, req)
		return w
	}
	ask := func(prompt string) *models.APIError {
		t.Helper()
		raw, _ := json.Marshal(prompt)
		return plugin.ProcessRequest(context.Background(), &models.RequestContext{
			RequestID: "req-cleared",
			Metadata:  map[string]string{"org_id": org},
			Timings:   map[string]time.Duration{},
			Request: &models.ChatCompletionRequest{
				Model:    "gpt-4",
				Messages: []models.Message{{Role: "user", Content: raw}},
			},
		}).Error
	}

	if w := put(djangoPushedBlocklistSection); w.Code != http.StatusOK {
		t.Fatalf("PUT of the policy's check answered %d: %s", w.Code, w.Body.String())
	}
	if err := ask("please say t-forbidden"); err == nil || err.Status != http.StatusForbidden {
		t.Fatalf("with the policy's check pushed, the prompt was not blocked: %v", err)
	}

	// That section is refused, and the gateway keeps its old check.
	if w := put(sectionWithEnabledFlag); w.Code != http.StatusBadRequest {
		t.Fatalf("PUT of a section with an enabled flag answered %d, want 400: %s", w.Code, w.Body.String())
	}
	if err := ask("please say t-forbidden"); err == nil || err.Status != http.StatusForbidden {
		t.Fatalf("after the refused section, the org's check should still run: %v", err)
	}

	if w := put(djangoPushedEmptySection); w.Code != http.StatusOK {
		t.Fatalf("PUT of the empty section answered %d: %s", w.Code, w.Body.String())
	}
	cfg := store.Get(org)
	if cfg == nil || cfg.Guardrails == nil || len(cfg.Guardrails.Checks) != 0 {
		t.Fatalf("the empty section did not replace the org's checks: %+v", cfg)
	}
	if err := ask("please say t-forbidden"); err != nil {
		t.Fatalf("after the empty section was pushed, the old check still ran: %d %s", err.Status, err.Message)
	}
}
