package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Perplexity's Agent API names third-party models "anthropic/claude-sonnet-5-5"
// and "openai/gpt-5.5": the same spelling as the gateway's "<provider>/<model>".
// Each case resolves 50 times, because the providers sit in a map and a choice
// that depends on its order comes out differently between runs.
func TestOrgProviderForPrefersTheNamedProvider(t *testing.T) {
	base := map[string]config.ProviderConfig{}
	for _, id := range []string{"anthropic", "perplexity", "perplexity-sonar", "sonar-eu"} {
		base[id] = config.ProviderConfig{BaseURL: "http://127.0.0.1:9", APIFormat: "openai"}
	}
	h := &Handlers{orgProviderCache: providers.NewOrgProviderCache(base, false)}
	provider := func(models ...string) *tenant.ProviderConfig {
		return &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, Models: models}
	}
	withAnthropic := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"anthropic":  provider("claude-sonnet-5-5"),
		"perplexity": provider("anthropic/claude-sonnet-5-5", "perplexity/sonar"),
	}}
	perplexityOnly := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"perplexity": provider("anthropic/claude-sonnet-5-5"),
	}}
	twoSonars := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"sonar-eu":         provider("sonar"),
		"perplexity-sonar": provider("sonar"),
	}}

	for _, tc := range []struct {
		name         string
		org          *tenant.OrgConfig
		model        string
		wantProvider string
		wantUpstream string
	}{
		{"the provider prefix names the org's own provider", withAnthropic, "anthropic/claude-sonnet-5-5", "anthropic", "claude-sonnet-5-5"},
		{"the vendor's model on Perplexity", withAnthropic, "perplexity/anthropic/claude-sonnet-5-5", "perplexity", "anthropic/claude-sonnet-5-5"},
		{"a prefixed name only Perplexity lists", withAnthropic, "perplexity/sonar", "perplexity", "perplexity/sonar"},
		{"a bare name", withAnthropic, "claude-sonnet-5-5", "anthropic", "claude-sonnet-5-5"},
		{"no provider of that name", perplexityOnly, "anthropic/claude-sonnet-5-5", "perplexity", "anthropic/claude-sonnet-5-5"},
		{"a tie goes to the first provider ID", twoSonars, "sonar", "perplexity-sonar", "sonar"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for range 50 {
				p, providerID, upstream, err := h.orgProviderFor("org-1", tc.org, tc.model)
				if err != nil || p == nil {
					t.Fatalf("orgProviderFor(%q) = %v, %v", tc.model, p, err)
				}
				if providerID != tc.wantProvider || upstream != tc.wantUpstream {
					t.Fatalf("orgProviderFor(%q) = %s sending %q, want %s sending %q",
						tc.model, providerID, upstream, tc.wantProvider, tc.wantUpstream)
				}
			}
		})
	}
}

// modelRecorder is an OpenAI-format upstream that records the model each
// request named.
type modelRecorder struct {
	mu     sync.Mutex
	models []string
}

func (u *modelRecorder) received() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.models...)
}

func (u *modelRecorder) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Model string `json:"model"`
	}
	json.NewDecoder(r.Body).Decode(&req)
	switch r.URL.Path {
	case "/v1/chat/completions":
		io.WriteString(w, `{"id":"chatcmpl-1","object":"chat.completion","created":1761580000,"model":"`+req.Model+`","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}`)
	case "/v1/responses":
		io.WriteString(w, `{"id":"resp_1","object":"response","created_at":1761580000,"status":"completed","model":"`+req.Model+`","output":[{"id":"msg_1","type":"message","role":"assistant","status":"completed","content":[{"type":"output_text","text":"ok","annotations":[]}]}],"usage":{"input_tokens":3,"output_tokens":1,"total_tokens":4}}`)
	default:
		http.NotFound(w, r)
		return
	}
	u.mu.Lock()
	u.models = append(u.models, req.Model)
	u.mu.Unlock()
}

// An org with its own OpenAI provider and a Perplexity provider that lists
// "openai/gpt-5.5": each name reaches one upstream, every time, under the
// name that upstream knows.
func TestOrgModelPrefixReachesOneUpstreamUnderItsOwnName(t *testing.T) {
	openaiUp, perplexityUp := &modelRecorder{}, &modelRecorder{}
	openaiSrv, perplexitySrv := httptest.NewServer(openaiUp), httptest.NewServer(perplexityUp)
	defer openaiSrv.Close()
	defer perplexitySrv.Close()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "org-user", Key: "sk-agentcc-org-prefix", Owner: "user", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-prefix"},
	}}
	// The org's providers take their base URLs from these entries.
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: openaiSrv.URL, APIKey: "operator-key", APIFormat: "openai"}
	cfg.Providers["perplexity"] = config.ProviderConfig{BaseURL: perplexitySrv.URL, APIKey: "operator-key", APIFormat: "openai"}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	tenantStore := tenant.NewStore()
	tenantStore.Set("org-prefix", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai":     {APIKey: "org-key", Enabled: true, Models: []string{"gpt-5.5"}},
		"perplexity": {APIKey: "org-key", Enabled: true, Models: []string{"openai/gpt-5.5"}},
	}})
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)

	routes := []struct{ path, body string }{
		{"/v1/chat/completions", `{"model":%q,"messages":[{"role":"user","content":"hi"}]}`},
		{"/v1/responses", `{"model":%q,"input":"hi"}`},
		{"/v1/messages", `{"model":%q,"max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`},
	}
	for _, tc := range []struct {
		model    string
		upstream *modelRecorder
		want     string
	}{
		{"openai/gpt-5.5", openaiUp, "gpt-5.5"},
		{"perplexity/openai/gpt-5.5", perplexityUp, "openai/gpt-5.5"},
	} {
		for _, route := range routes {
			before := map[*modelRecorder]int{openaiUp: len(openaiUp.received()), perplexityUp: len(perplexityUp.received())}
			for range 20 {
				req := httptest.NewRequest(http.MethodPost, route.path, bytes.NewBufferString(fmt.Sprintf(route.body, tc.model)))
				req.Header.Set("Authorization", "Bearer sk-agentcc-org-prefix")
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("anthropic-version", "2023-06-01")
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				if w.Code != http.StatusOK {
					t.Fatalf("%s %s: status %d: %s", route.path, tc.model, w.Code, w.Body.String())
				}
			}
			for up, n := range before {
				got := up.received()[n:]
				if up != tc.upstream {
					if len(got) != 0 {
						t.Errorf("%s %s: the other upstream received %v", route.path, tc.model, got)
					}
					continue
				}
				if len(got) != 20 {
					t.Errorf("%s %s: upstream received %d of 20 requests", route.path, tc.model, len(got))
				}
				for _, m := range got {
					if m != tc.want {
						t.Errorf("%s %s: upstream received model %q, want %q", route.path, tc.model, m, tc.want)
						break
					}
				}
			}
		}
	}
}
