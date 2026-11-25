package server

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Dev skips empty native output parts in its legacy observation view. R3's
// complete native-output projection must not overwrite that shared view.
func TestRuntimeR5NativeOutputViewHTTP(t *testing.T) {
	for _, scope := range []string{"legacy", "selected"} {
		for _, route := range []struct{ name, path, body, reply string }{
			{"responses", "/v1/responses", `{"model":"gpt-5.5","input":"safe"}`, `{"model":"gpt-5.5","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":""},{"type":"output_text","text":"first"},{"type":"output_text","text":"second"}]}]}`},
			{"anthropic", "/v1/messages", `{"model":"claude-sonnet-5-5","max_tokens":10,"messages":[{"role":"user","content":"safe"}]}`, `{"model":"claude-sonnet-5-5","content":[{"type":"text","text":""},{"type":"text","text":"first"},{"type":"text","text":"second"}]}`},
			{"genai", "/v1beta/models/gemini-3.8-flash:generateContent", `{"contents":[{"role":"user","parts":[{"text":"safe"}]}]}`, `{"candidates":[{"content":{"role":"model","parts":[{"text":""},{"text":"first"},{"text":"second"}]}}]}`},
		} {
			t.Run(scope+"/"+route.name, func(t *testing.T) {
				gr := &tenant.GuardrailConfig{}
				if scope == "selected" {
					runtimeR5Selected(t, gr, map[string]*tenant.GuardrailCheck{"data-leakage-prevention": {Enabled: true, Action: "block", ConfidenceThreshold: .5}})
				}
				capture := &runtimeR5Capture{stage: guardrails.StagePost}
				f := newRuntimeR5Fixture(t, gr, route.reply, capture)
				status, body, calls := f.send(t, "POST", route.path, route.body, map[string]string{"Authorization": "Bearer sk-agentcc-r5-a"})
				if status != 200 || len(calls) != 1 || string(body) != route.reply {
					t.Fatalf("native release changed: status=%d calls=%d body=%s", status, len(calls), body)
				}
				want, _ := json.Marshal(&models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"first\nsecond"`)}}}})
				runtimeR5EqualJSON(t, capture.last(t), want)
			})
		}
	}
}
