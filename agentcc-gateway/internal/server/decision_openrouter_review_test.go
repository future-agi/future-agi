package server

import (
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A chat ID whose bare name is a priced decision model (openai/gpt-6-luna) is
// an OpenRouter chat model, not a decision: it is neither routed nor listed
// as one.
func TestDecisionOpenRouterChatIDWithADecisionModelName(t *testing.T) {
	g := openRouterChatGateway(t, false)
	org := g.tenants.Get("org-a")
	org.Providers["openrouter"].Models = append(org.Providers["openrouter"].Models, "openai/gpt-6-luna")
	g.tenants.Set("org-a", org)
	e := expectError(t, g.post(t, keyByok, openRouterSysBody("openrouter/openai/gpt-6-luna")),
		http.StatusForbidden, models.CodeDecisionProviderWrongFormat)
	expectNoCalls(t, g)
	// The message says what is wrong without advising a format switch that
	// would break the connection's chat traffic.
	if strings.Contains(e.Message, "choose System One") || !strings.Contains(e.Message, "not a decision model on OpenRouter connection") {
		t.Errorf("message = %q", e.Message)
	}
	rr := g.do(t, http.MethodGet, "/v1/decisions/models", keyByok, "")
	if strings.Contains(rr.Body.String(), "gpt-6-luna") {
		t.Errorf("listing has the chat model: %s", rr.Body.String())
	}
}

// An org OpenRouter entry that states no base_url is built on config.yaml's,
// and is judged an OpenRouter connection by that URL, as the registry path is.
func TestOrgOpenRouterJudgedByTheEffectiveBaseURL(t *testing.T) {
	cache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{
		"team-router": {APIFormat: "openai", BaseURL: "https://openrouter.ai/api/v1"},
	}, false)
	h := &Handlers{orgProviderCache: cache}
	pcfg := &tenant.ProviderConfig{APIKey: "synthetic", APIFormat: "openai", Enabled: true}
	if got := cache.BaseURL("team-router", pcfg); got != "https://openrouter.ai/api/v1" {
		t.Fatalf("effective base_url = %q", got)
	}
	if !h.isOrgOpenRouterChat("team-router", pcfg) {
		t.Error("an org entry layered onto an OpenRouter base_url is not judged OpenRouter")
	}
	own := &tenant.ProviderConfig{APIKey: "synthetic", APIFormat: "openai", Enabled: true, BaseURL: "https://api.example.test/v1"}
	if h.isOrgOpenRouterChat("team-router", own) {
		t.Error("an org entry with its own non-OpenRouter base_url is judged OpenRouter")
	}
}
