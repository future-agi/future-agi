package server

import (
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// "openai/gpt-5.6-terra" is how a caller names its org's OpenAI model; the
// gateway strips the prefix (case-insensitively) to call it. That is the
// caller's own choice, so a chat-only parameter is refused with the 400 and
// its remedy, not silently dropped as for a model the gateway moved there.
func TestCallerPrefixedGPT56KeepsTheChatOnlyRefusal(t *testing.T) {
	upstream := &openAIUpstream{hits: map[string]int{}}
	ts := httptest.NewServer(upstream)
	defer ts.Close()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "org-user", Key: "sk-agentcc-org-gpt56", Owner: "user", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-gpt56"},
	}}
	// The org provider takes the official host (so gpt-5.6 tools go over the
	// Responses API) and the test dialer from this entry.
	cfg.Providers["openai"] = bridgeProviderConfig(ts, false)
	cfg.Providers["openai"] = func(p config.ProviderConfig) config.ProviderConfig { p.APIFormat = "openai"; return p }(cfg.Providers["openai"])
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	tenantStore := tenant.NewStore()
	tenantStore.Set("org-gpt56", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: "org-key", Enabled: true, Models: []string{"gpt-5.6-terra"}},
	}})
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)

	for _, model := range []string{"openai/gpt-5.6-terra", "OpenAI/gpt-5.6-terra", "gpt-5.6-terra"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", model, stream), func(t *testing.T) {
				before := upstream.count("/v1/responses") + upstream.count("/v1/chat/completions")
				req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions",
					bytes.NewBufferString(chatToolBody(model, stream, chatOnlyExtra)))
				req.Header.Set("Authorization", "Bearer sk-agentcc-org-gpt56")
				req.Header.Set("Content-Type", "application/json")
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				if w.Code != http.StatusBadRequest {
					t.Fatalf("status = %d, want 400 refusing stop/seed: %s", w.Code, w.Body.String())
				}
				if !strings.Contains(w.Body.String(), "stop") {
					t.Errorf("the 400 does not name stop: %s", w.Body.String())
				}
				if n := upstream.count("/v1/responses") + upstream.count("/v1/chat/completions") - before; n != 0 {
					t.Errorf("upstream calls = %d, want none", n)
				}
			})
		}
	}
}

// A chat request the gateway moved onto gpt-5.6 reports what the Responses
// route dropped in the gateway's notes (the request log's translation_drops
// and x-agentcc-translation-drops), as <name>_unsupported.
func TestGatewayMovedChatReportsItsDrops(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{configure: func(cfg *config.Config) {
				cfg.Routing.DefaultStrategy = "least-latency"
				cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{http.StatusBadGateway}}
				cfg.Routing.ModelFallbacks = map[string][]string{"gpt-5.5": {"gpt-5.6-terra"}}
			}})
			upstream.failChatModel = "gpt-5.5"
			w := postBridgeChat(t, srv, chatToolBody("gpt-5.5", stream, chatOnlyExtra+`"n":2,`), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
			}
			got := strings.Split(w.Header().Get("x-agentcc-translation-drops"), ",")
			for _, want := range []string{"stop_unsupported", "seed_unsupported", "n_unsupported"} {
				found := false
				for _, g := range got {
					found = found || g == want
				}
				if !found {
					t.Errorf("x-agentcc-translation-drops = %q, want it to name %s", w.Header().Get("x-agentcc-translation-drops"), want)
				}
			}
		})
	}
}

// A chat request that is not moved reports no drops.
func TestUnmovedChatReportsNoDrops(t *testing.T) {
	srv, _ := newResponsesBridgeServerWith(t, bridgeServerOptions{})
	w := postBridgeChat(t, srv, chatToolBody("gpt-5.5", false, chatOnlyExtra), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-translation-drops"); got != "" {
		t.Errorf("x-agentcc-translation-drops = %q, want none", got)
	}
}
