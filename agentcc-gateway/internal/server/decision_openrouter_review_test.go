package server

import (
	"context"
	"net"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
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

// postOnlyGuardrail is an org check that runs on responses only.
type postOnlyGuardrail struct{}

func (postOnlyGuardrail) Name() string            { return "post-only" }
func (postOnlyGuardrail) Stage() guardrails.Stage { return guardrails.StagePost }
func (postOnlyGuardrail) Check(context.Context, *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: true}
}

// An org whose only enabled check runs on responses has no input guardrail:
// no decision text is extracted, so a deep but valid body is not refused.
func TestDecisionPostOnlyOrgCheckDoesNotExtractInput(t *testing.T) {
	org := defaultOrgA()
	org.Guardrails = &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"post-only": {Enabled: true, Action: "block"}}}
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
	reg := map[string]guardrails.Guardrail{"post-only": postOnlyGuardrail{}}
	plugin := guardrails.NewPlugin(nil, reg, nil, nil, g.tenants)
	g.srv.SetOrgInputGuardrails(plugin.HasOrgInputChecks)
	if rr := g.post(t, keyByok, nestedCriteria(maxDecisionTextDepth+6)); rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 1)

	// An enabled input check still gets the depth refusal.
	org.Guardrails.Checks["pii-detection"] = &tenant.GuardrailCheck{Enabled: true, Action: "block"}
	reg["pii-detection"] = pii.New(map[string]interface{}{})
	g.tenants.Set("org-a", org)
	plugin = guardrails.NewPlugin(nil, reg, nil, nil, g.tenants)
	g.srv.SetOrgInputGuardrails(plugin.HasOrgInputChecks)
	expectError(t, g.post(t, keyByok, nestedCriteria(maxDecisionTextDepth+6)), http.StatusBadRequest, "decision_too_deep")
}

// With auth off, a config.yaml OpenRouter connection under another name
// (judged by its openrouter.ai base_url) neither lists nor routes a chat ID
// as an OpenAI decision.
func TestRegistryOpenRouterChatIDIsNotAnOpenAIDecision(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{authOff: true, config: func(cfg *config.Config, upstreamURL string) {
		u, _ := url.Parse(upstreamURL)
		cfg.Providers["team-router"] = config.ProviderConfig{
			APIFormat: "openai", BaseURL: "http://openrouter.ai/api/v1", APIKey: fagiHeldKey,
			Models: []string{"openai/gpt-6-luna", "typesafe/jev-1.13"},
			DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
				var d net.Dialer
				return d.DialContext(ctx, network, u.Host)
			},
		}
	}})
	rr := g.do(t, http.MethodGet, "/v1/decisions/models", "", "")
	if strings.Contains(rr.Body.String(), "team-router/openai/gpt-6-luna") {
		t.Errorf("listing has the chat model: %s", rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "team-router/typesafe/jev-1.13") {
		t.Errorf("listing lacks the Jev model: %s", rr.Body.String())
	}
	oai := strings.Replace(decisionOAIBody, "openai/gpt-6-luna", "team-router/openai/gpt-6-luna", 1)
	if rr := g.post(t, "", oai); rr.Code < 400 {
		t.Fatalf("an OpenAI decision on an OpenRouter chat model got %d: %s", rr.Code, rr.Body.String())
	}
	expectNoCalls(t, g)
}
