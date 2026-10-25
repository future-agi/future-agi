package server

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// refusingCredits stands in for the credits reserve (pre-plugin 510): it
// refuses every request with the 402 for credits held by requests in flight.
type refusingCredits struct{}

func (refusingCredits) Name() string  { return "refusing-credits" }
func (refusingCredits) Priority() int { return 510 }
func (refusingCredits) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultError(models.ErrPaymentRequired(models.ErrTypePaymentRequired, "managed_credits_insufficient",
		"credits are held by requests in flight",
		models.PaymentMeta{LimitSource: models.LimitSourceInFlight, RemedyHint: "retry", Scope: models.ScopeOrg, RetryAfterSec: 2}))
}
func (refusingCredits) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// A 402 a pre-plugin raises reaches the client with its error.metadata and
// Retry-After, on the non-stream and the stream path, and nothing is sent
// upstream.
func TestPaymentRefusalReachesClient(t *testing.T) {
	for _, tc := range []struct{ name, body string }{
		{"non-stream", hookChatBody},
		{"stream", `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := startHookUpstream(t, 0)
			cfg := config.DefaultConfig()
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "hook", Key: "sk-hook", Owner: "t", KeyType: "internal"}}
			cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			keyStore := auth.NewKeyStore(cfg.Auth)
			engine := pipeline.NewEngine(authplugin.New(keyStore, true), refusingCredits{})
			srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)

			w := (&hookOrderTest{srv: srv}).chat(t, tc.body)
			if w.Code != http.StatusPaymentRequired || w.Header().Get("Retry-After") != "2" {
				t.Fatalf("status %d, Retry-After %q, body %s", w.Code, w.Header().Get("Retry-After"), w.Body.String())
			}
			var body struct {
				Error struct {
					Code     string         `json:"code"`
					Metadata map[string]any `json:"metadata"`
				} `json:"error"`
			}
			if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
				t.Fatalf("body %q: %v", w.Body.String(), err)
			}
			meta := body.Error.Metadata
			if body.Error.Code != "managed_credits_insufficient" || meta["limit_source"] != models.LimitSourceInFlight ||
				meta["scope"] != models.ScopeOrg {
				t.Errorf("402 body lost its metadata: %s", w.Body.String())
			}
			if v, ok := meta["resets_at"]; !ok || v != nil {
				t.Errorf("resets_at must be present and null: %s", w.Body.String())
			}
			if n := up.calls.Load(); n != 0 {
				t.Errorf("a refused request reached the upstream %d times", n)
			}
		})
	}
}
