package server

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// TestNewOrgDefaultConfigAtZeroIsAddCredits pins what a new organization's
// first key meets where the deployment offers Future AGI models: Django's
// first org config for it carries only managed_models {"enabled": true}
// (agentcc/services/managed_models_default.py), and with no credit yet its
// call is the existing 402 managed_credits_exhausted saying "Add credit",
// before any upstream call, on chat and on /v1/messages. It pins the shape
// Django pushes: that config alone turns Future AGI models on.
func TestNewOrgDefaultConfigAtZeroIsAddCredits(t *testing.T) {
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(`{"managed_models":{"enabled":true}}`), &cfg); err != nil {
		t.Fatal(err)
	}
	if !cfg.ManagedModelsEnabled() {
		t.Fatal("the pushed default does not turn managed models on")
	}
	g := newManagedTestGateway(t, &cfg)
	g.grant(t, "org-m", 0) // the account the first push creates, with nothing granted

	for name, w := range map[string]func() int{
		"chat": func() int {
			r := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
			if !strings.Contains(r.Body.String(), "managed_credits_exhausted") || !strings.Contains(r.Body.String(), "Add credit on the Settings Billing page") {
				t.Errorf("chat body %s, want managed_credits_exhausted with Add credit on the Settings Billing page", r.Body)
			}
			return r.Code
		},
		"messages": func() int {
			r := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("anthropic/claude-sonnet-5.5", false, ""))
			if !strings.Contains(r.Body.String(), "Add credit on the Settings Billing page") {
				t.Errorf("/v1/messages body %s, want Add credit on the Settings Billing page", r.Body)
			}
			return r.Code
		},
	} {
		if code := w(); code != http.StatusPaymentRequired {
			t.Errorf("%s: status %d, want 402", name, code)
		}
	}
	if g.managed.calls() != 0 {
		t.Fatal("an org with no credit reached the managed provider")
	}
}
