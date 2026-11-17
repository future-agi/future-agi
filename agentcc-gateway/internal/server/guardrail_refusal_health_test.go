package server

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A guardrail that refuses the answer a provider gave is not the provider's
// failure. The provider health that adaptive routing and model "auto" read
// counts the call as the success it was, as it did before the refusal was
// recorded on the request; with it counted as an error, an org's own
// output checks would steer its traffic away from a healthy provider. A
// provider that fails still counts as failing.
//
// The shared health records the gateway's own provider instances only, once
// per upstream attempt (recordProviderOutcome): a call on an organization's
// own provider key (the org-check cases, which run on the org's key) adds
// nothing to it, neither a success nor an error. The streaming-checker case
// and the controls run on the gateway's own instance, with an internal key.
func TestPostBlock_ARefusedAnswerIsNotAProviderError(t *testing.T) {
	chat := func(model string, stream bool) string {
		return fmt.Sprintf(`{"model":%q,"stream":%t,"messages":[{"role":"user","content":%q}]}`, model, stream, postBlockPrompt)
	}
	stopAtChunk := &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 5, FailureAction: "stop"}
	cases := []struct {
		name         string
		setup        postBlockSetup
		key          string
		path, body   string
		provider     string
		wantCode     int
		wantRequests int64
		wantErrors   int64
	}{
		{"chat, org check", postBlockSetup{orgCheck: true}, postBlockKey, "/v1/chat/completions", chat("gpt-4o", false), "openai", http.StatusForbidden, 0, 0},
		{"chat stream, org check at the end", postBlockSetup{orgCheck: true, capture: true}, postBlockKey, "/v1/chat/completions", chat("gpt-4o", true), "openai", http.StatusOK, 0, 0},
		{"chat stream, streaming checker", postBlockSetup{staticRule: leakageCheck, streaming: stopAtChunk}, postBlockOpsKey, "/v1/chat/completions", chat("gpt-4o", true), "openai", http.StatusOK, 1, 0},
		{"completions, org check", postBlockSetup{orgCheck: true}, postBlockKey, "/v1/completions", completionsBody("gpt-4o", false), "openai", http.StatusForbidden, 0, 0},
		{"control: chat, the provider fails", postBlockSetup{}, postBlockOpsKey, "/v1/chat/completions", chat(postBlockBroken, false), "broken", 0, 1, 1},
		{"control: completions, the provider fails", postBlockSetup{}, postBlockOpsKey, "/v1/completions", completionsBody(postBlockBroken, false), "broken", 0, 1, 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			code, body := g.doAs(t, tc.key, tc.path, tc.body, nil)
			if tc.wantCode != 0 && code != tc.wantCode {
				t.Fatalf("status %d, want %d; body %s", code, tc.wantCode, body)
			}
			if tc.wantCode == 0 && code < 400 {
				t.Fatalf("status %d, want the provider's failure; body %s", code, body)
			}
			h := g.srv.handlers.healthMonitor.GetHealth(tc.provider)
			if h.RequestCount != tc.wantRequests || h.ErrorCount != tc.wantErrors {
				t.Errorf("provider %s health: %d requests, %d errors (last error %q); want %d requests, %d errors",
					tc.provider, h.RequestCount, h.ErrorCount, h.LastError, tc.wantRequests, tc.wantErrors)
			}
		})
	}
}
