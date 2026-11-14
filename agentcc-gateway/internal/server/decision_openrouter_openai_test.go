package server

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	orgOpenRouterKey    = "ORG-OPENROUTER-KEY"
	openRouterJevAnswer = `{"model":"typesafe/jev-1.13-20260917","answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1000000,"output_tokens":3}}`
)

// openRouterChatGateway has config.yaml's OpenRouter chat entry (the preset:
// openai format, base https://openrouter.ai/api, here the test upstream) and
// an org that saved its OpenRouter key on it from the dashboard, with the
// OpenAI format, a chat model and the Jev decision models.
func openRouterChatGateway(t *testing.T, authOff bool) *decisionGateway {
	t.Helper()
	g := newDecisionGateway(t, decisionGatewayOptions{
		authOff: authOff,
		config: func(cfg *config.Config, upstreamURL string) {
			cfg.Providers["openrouter"] = config.ProviderConfig{
				APIFormat: "openai", BaseURL: upstreamURL + "/api", APIKey: fagiHeldKey,
				Models: []string{"meta-llama/llama-3.3-70b-instruct", "typesafe/jev-1.13", "~typesafe/jev-latest"},
			}
		},
		orgs: map[string]*tenant.OrgConfig{"org-a": {Providers: map[string]*tenant.ProviderConfig{
			"openrouter": {
				APIKey: orgOpenRouterKey, APIFormat: "openai", Enabled: true,
				Models: []string{"meta-llama/llama-3.3-70b-instruct", "typesafe/jev-1.13", "~typesafe/jev-latest"},
			},
		}}},
	})
	g.upstream.setReply(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, openRouterJevAnswer)
	})
	return g
}

func openRouterSysBody(model string) string {
	return strings.Replace(decisionSysBody, "typesafe/jev-latest", model, 1)
}

// One OpenRouter provider saved with the OpenAI format serves chat and
// decisions: a decision goes to OpenRouter's decisions endpoint on the org's
// own key, under OpenRouter's routing policy, and is priced.
func TestDecisionOpenRouterOpenAIFormatServesDecisions(t *testing.T) {
	g := openRouterChatGateway(t, false)
	for i, model := range []string{"openrouter/typesafe/jev-1.13", "openrouter/~typesafe/jev-latest"} {
		rr := g.post(t, keyByok, openRouterSysBody(model))
		if rr.Code != http.StatusOK || rr.Body.String() != openRouterJevAnswer {
			t.Fatalf("%s: status %d: %s", model, rr.Code, rr.Body.String())
		}
		if got := rr.Header().Get("x-agentcc-provider"); got != "openrouter" {
			t.Errorf("%s: provider = %q", model, got)
		}
		path, sent := g.upstream.last()
		if path != "POST /api/alpha/decisions" {
			t.Errorf("%s: upstream path = %q, want POST /api/alpha/decisions", model, path)
		}
		if want := strings.TrimPrefix(model, "openrouter/"); g.upstream.sentModel(t) != want {
			t.Errorf("%s: upstream model = %q, want %q", model, g.upstream.sentModel(t), want)
		}
		var policy struct {
			Provider struct {
				AllowFallbacks    bool `json:"allow_fallbacks"`
				RequireParameters bool `json:"require_parameters"`
			} `json:"provider"`
		}
		if err := json.Unmarshal([]byte(sent), &policy); err != nil || !policy.Provider.AllowFallbacks || !policy.Provider.RequireParameters {
			t.Errorf("%s: OpenRouter routing policy not applied: %s", model, sent)
		}
		if got := rr.Header().Get("x-agentcc-cost"); got != "0.042000" {
			t.Errorf("%s: x-agentcc-cost = %q, want 0.042000", model, got)
		}
		expectOnlyKey(t, g, orgOpenRouterKey, i+1)
	}
	if g.mirror.calls() != 0 {
		t.Fatal("decision was mirrored")
	}
}

// A chat model on that provider is not a decision model: it is refused as
// today, and nothing reaches OpenRouter.
func TestDecisionOpenRouterOpenAIFormatRefusesChatModels(t *testing.T) {
	g := openRouterChatGateway(t, false)
	expectError(t, g.post(t, keyByok, openRouterSysBody("openrouter/meta-llama/llama-3.3-70b-instruct")),
		http.StatusForbidden, models.CodeDecisionProviderWrongFormat)
	// OpenAI Decisions are not OpenRouter's: the body format does not match.
	oai := strings.Replace(decisionOAIBody, "openai/gpt-6-luna", "openrouter/typesafe/jev-1.13", 1)
	if rr := g.post(t, keyByok, oai); rr.Code < 400 {
		t.Fatalf("an OpenAI Decisions body to OpenRouter got %d", rr.Code)
	}
	expectNoCalls(t, g)
}

// GET /v1/decisions/models agrees with routing: the OpenRouter provider lists
// its Jev models, priced, and never its chat models.
func TestDecisionModelsListingOpenRouterOpenAIFormat(t *testing.T) {
	g := openRouterChatGateway(t, false)
	rr := g.do(t, http.MethodGet, "/v1/decisions/models", keyByok, "")
	var resp struct {
		Data []decisionModel `json:"data"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); rr.Code != http.StatusOK || err != nil {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	jev := &decisionModelPricing{InputPerMTok: 0.042}
	want := []decisionModel{
		{ID: "openrouter/typesafe/jev-1.13", Object: "model", Provider: "openrouter", Format: "systemone", Pricing: jev},
		{ID: "openrouter/~typesafe/jev-latest", Object: "model", Provider: "openrouter", Format: "systemone", Pricing: jev},
	}
	if gotJSON, wantJSON := mustJSON(resp.Data), mustJSON(want); gotJSON != wantJSON {
		t.Fatalf("models =\n%s\nwant\n%s", gotJSON, wantJSON)
	}
	expectNoCalls(t, g)
}

// With auth off, config.yaml's OpenRouter chat entry serves decisions on the
// operator's key, who is the customer there.
func TestDecisionOpenRouterOpenAIFormatAuthOff(t *testing.T) {
	g := openRouterChatGateway(t, true)
	rr := g.post(t, "", openRouterSysBody("openrouter/typesafe/jev-1.13"))
	if rr.Code != http.StatusOK || rr.Body.String() != openRouterJevAnswer {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if path, _ := g.upstream.last(); path != "POST /api/alpha/decisions" {
		t.Errorf("upstream path = %q", path)
	}
	if g.upstream.callsWith("Bearer "+fagiHeldKey) != 1 {
		t.Fatalf("calls by key: %v", g.upstream.authorizations())
	}
	expectError(t, g.post(t, "", openRouterSysBody("openrouter/meta-llama/llama-3.3-70b-instruct")),
		http.StatusForbidden, models.CodeDecisionProviderWrongFormat)

	list := g.do(t, http.MethodGet, "/v1/decisions/models", "", "")
	var resp struct {
		Data []decisionModel `json:"data"`
	}
	if err := json.Unmarshal(list.Body.Bytes(), &resp); list.Code != http.StatusOK || err != nil {
		t.Fatalf("status %d: %s", list.Code, list.Body.String())
	}
	var ids []string
	for _, m := range resp.Data {
		if m.Provider == "openrouter" {
			ids = append(ids, m.ID)
		}
	}
	if strings.Join(ids, ",") != "openrouter/typesafe/jev-1.13,openrouter/~typesafe/jev-latest" {
		t.Errorf("auth-off OpenRouter listing = %v", ids)
	}
	if g.upstream.calls() != 1 {
		t.Fatalf("upstream calls = %d, want 1", g.upstream.calls())
	}
}

// The decision provider is cached with the org's others, so a config change
// that evicts the org drops it too.
func TestDecisionOpenRouterOpenAIFormatIsEvictedWithTheOrg(t *testing.T) {
	g := openRouterChatGateway(t, false)
	if rr := g.post(t, keyByok, openRouterSysBody("openrouter/typesafe/jev-1.13")); rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if n := g.srv.OrgProviderCache.Count(); n == 0 {
		t.Fatal("the decision provider was not cached")
	}
	org := g.tenants.Get("org-a")
	org.Providers["openrouter"].APIKey = "ORG-OPENROUTER-KEY-ROTATED"
	g.tenants.Set("org-a", org)
	g.srv.OrgProviderCache.Evict("org-a")
	if n := g.srv.OrgProviderCache.Count(); n != 0 {
		t.Fatalf("%d providers left after Evict", n)
	}
	if rr := g.post(t, keyByok, openRouterSysBody("openrouter/typesafe/jev-1.13")); rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if g.upstream.callsWith("Bearer ORG-OPENROUTER-KEY-ROTATED") != 1 {
		t.Fatalf("calls by key: %v", g.upstream.authorizations())
	}
}

// TypeSafe and Perplexity saved with the OpenAI format are still refused:
// only OpenRouter serves chat and decisions on one key.
func TestDecisionOnlyOpenRouterServesDecisionsFromTheOpenAIFormat(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{
		config: func(cfg *config.Config, upstreamURL string) {
			for _, id := range []string{"typesafe-chat", "perplexity-chat"} {
				cfg.Providers[id] = config.ProviderConfig{APIFormat: "openai", BaseURL: upstreamURL, Models: []string{"typesafe/jev-1.13"}}
			}
		},
		orgs: map[string]*tenant.OrgConfig{"org-a": {Providers: map[string]*tenant.ProviderConfig{
			"typesafe-chat":   {APIKey: "K1", APIFormat: "openai", Enabled: true, Models: []string{"typesafe/jev-1.13"}},
			"perplexity-chat": {APIKey: "K2", APIFormat: "openai", Enabled: true, Models: []string{"typesafe/jev-1.13"}},
		}}},
	})
	for _, id := range []string{"typesafe-chat", "perplexity-chat"} {
		expectError(t, g.post(t, keyByok, openRouterSysBody(id+"/typesafe/jev-1.13")), http.StatusForbidden, models.CodeDecisionProviderWrongFormat)
	}
	expectNoCalls(t, g)
}
