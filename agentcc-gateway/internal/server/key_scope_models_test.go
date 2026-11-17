package server

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Managed models of newManagedTestGateway's catalogue (testCatalogModels).
const (
	agentKeyManagedModel  = "anthropic/claude-sonnet-5.5"
	agentKeyManagedModel2 = "openai/gpt-6-sol"
)

// agentKeyOrg is an org opted in to managed models whose own key on the
// operator provider serves the given models (BYOK).
func agentKeyOrg(models []string) *tenant.OrgConfig {
	cfg := managedOptIn()
	cfg.Providers = map[string]*tenant.ProviderConfig{
		"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: models, Enabled: true},
	}
	return cfg
}

// CN22c rule 2: an EE agent key's models are the org's allowed models (its
// managed_models allow and deny lists) plus its own providers (BYOK),
// resolved on every request. The key is minted with no model or provider
// list of its own (Django's mint sends none), so a change to the org's
// config applies to the next request, with no new key.
func TestScopedKeyModelsFollowTheOrgConfigPerRequest(t *testing.T) {
	g := newManagedTestGateway(t, agentKeyOrg([]string{"gpt-4o"}))
	g.grant(t, "org-m", 1_000_000)
	key, raw := g.srv.handlers.keyStore.Create("Omega EE agent key: Engineering", "omega-ee", nil, nil,
		map[string]string{"org_id": "org-m", "scopes": "mcp,models"})
	if len(key.AllowedModels) != 0 || len(key.AllowedProviders) != 0 {
		t.Fatalf("the key carries its own model or provider list: %v %v", key.AllowedModels, key.AllowedProviders)
	}
	chat := func(model string) int {
		w := g.chat(t, fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hi"}]}`, model),
			map[string]string{"Authorization": "Bearer " + raw})
		return w.Code
	}

	// The org's own provider (BYOK), sent with the org's key, and an allowed
	// managed model.
	if code := chat("gpt-4o"); code != http.StatusOK {
		t.Fatalf("the org's BYOK model: %d, want 200", code)
	}
	if got := g.operator.lastAuth(); got != "Bearer org-operator-key" {
		t.Fatalf("the org's BYOK model was sent with %q, want the org's own key", got)
	}
	spent := g.account(t, "org-m").SpentMicros
	if code := chat(agentKeyManagedModel); code != http.StatusOK {
		t.Fatalf("an allowed managed model: %d, want 200", code)
	}
	// Debited to the org's account like any managed call.
	if after := g.account(t, "org-m"); after.SpentMicros <= spent || after.HeldMicros != 0 {
		t.Fatalf("the agent key's managed call left the account at spent %d (was %d), held %d",
			after.SpentMicros, spent, after.HeldMicros)
	}

	// An admin narrows the org: the managed allow-list drops the model, and
	// the org's provider no longer lists gpt-4o. The same key, unchanged, is
	// refused on the next request.
	narrowed := agentKeyOrg([]string{"gpt-4o-mini"})
	narrowed.ManagedModels.AllowModels = []string{agentKeyManagedModel2}
	g.tenants.Set("org-m", narrowed)
	if code := chat(agentKeyManagedModel); code == http.StatusOK {
		t.Fatal("a managed model the org's allow-list dropped was served")
	}
	if code := chat("gpt-4o"); code == http.StatusOK {
		t.Fatal("a model the org's own provider no longer lists was served")
	}
	if code := chat(agentKeyManagedModel2); code != http.StatusOK {
		t.Fatalf("the managed model the allow-list keeps: %d, want 200", code)
	}

	// The org turns managed models off: only its own providers remain.
	byokOnly := agentKeyOrg([]string{"gpt-4o"})
	byokOnly.ManagedModels = &tenant.ManagedModelsConfig{Enabled: false}
	g.tenants.Set("org-m", byokOnly)
	if code := chat(agentKeyManagedModel2); code == http.StatusOK {
		t.Fatal("a managed model was served to an org that turned managed models off")
	}
	if code := chat("gpt-4o"); code != http.StatusOK {
		t.Fatalf("the org's BYOK model after managed went off: %d, want 200", code)
	}
}
