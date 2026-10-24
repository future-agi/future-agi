package server

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A guardrail that refuses the answer a provider gave is not the provider's
// failure. The provider health that adaptive routing and model "auto" read
// counts the call as a success, so an org's own output checks never steer
// its traffic away from a healthy provider. A provider that fails still
// counts as failing.
func TestPostBlock_ARefusedAnswerIsNotAProviderError(t *testing.T) {
	chat := func(model string, stream bool) string {
		return fmt.Sprintf(`{"model":%q,"stream":%t,"messages":[{"role":"user","content":%q}]}`, model, stream, postBlockPrompt)
	}
	stopAtChunk := &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 5, FailureAction: "stop"}
	cases := []struct {
		name       string
		setup      postBlockSetup
		path, body string
		provider   string
		wantCode   int
		wantErrors int64
	}{
		{"chat, org check", postBlockSetup{orgCheck: true}, "/v1/chat/completions", chat("gpt-4o", false), "openai", http.StatusForbidden, 0},
		{"chat stream, org check at the end", postBlockSetup{orgCheck: true, capture: true}, "/v1/chat/completions", chat("gpt-4o", true), "openai", http.StatusOK, 0},
		{"chat stream, streaming checker", postBlockSetup{staticRule: leakageCheck, streaming: stopAtChunk}, "/v1/chat/completions", chat("gpt-4o", true), "openai", http.StatusOK, 0},
		{"completions, org check", postBlockSetup{orgCheck: true}, "/v1/completions", completionsBody("gpt-4o", false), "openai", http.StatusForbidden, 0},
		{"control: chat, the provider fails", postBlockSetup{}, "/v1/chat/completions", chat(postBlockBroken, false), "broken", 0, 1},
		{"control: completions, the provider fails", postBlockSetup{}, "/v1/completions", completionsBody(postBlockBroken, false), "broken", 0, 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			code, body := g.do(t, tc.path, tc.body)
			if tc.wantCode != 0 && code != tc.wantCode {
				t.Fatalf("status %d, want %d; body %s", code, tc.wantCode, body)
			}
			if tc.wantCode == 0 && code < 400 {
				t.Fatalf("status %d, want the provider's failure; body %s", code, body)
			}
			h := g.srv.handlers.healthMonitor.GetHealth(tc.provider)
			if h.RequestCount != 1 || h.ErrorCount != tc.wantErrors {
				t.Errorf("provider %s health: %d requests, %d errors (last error %q); want 1 request, %d errors",
					tc.provider, h.RequestCount, h.ErrorCount, h.LastError, tc.wantErrors)
			}
		})
	}
}
