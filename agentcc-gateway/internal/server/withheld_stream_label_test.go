package server

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A streamed answer a check withholds is logged as the withheld answer it
// is: metadata answer_withheld "true", with the tokens and cost the provider
// reported. A held stream sends none of a withheld answer, whether an output
// check blocks it once it has ended (withholdAnswer) or the streaming checker
// does (markStreamBlocked). A stream the checker stops mid-way, before the
// provider reported its usage, is counted as any stream cut before its usage
// chunk is (estimatedStreamUsage), so its tokens are not asserted here.
func TestStreamedWithheldAnswerIsMarkedInTheLog(t *testing.T) {
	for _, tc := range []struct {
		name   string
		setup  postBlockSetup
		tokens bool // the provider's usage reached the gateway before the block
	}{
		{"an output check blocks the held stream", postBlockSetup{staticRule: leakageCheck, capture: true, priced: true}, true},
		{"the stream checker blocks it at the end", postBlockSetup{staticRule: leakageCheck, priced: true,
			streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 1 << 20, FailureAction: "stop"}}, true},
		{"the stream checker stops it mid-stream", postBlockSetup{staticRule: leakageCheck, priced: true,
			streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 5, FailureAction: "stop"}}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			setup := tc.setup
			setup.upstream = serveLeakThenUsageChunk
			g := newPostBlockGateway(t, setup)
			code, body := g.do(t, "/v1/chat/completions",
				fmt.Sprintf(`{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":%q}]}`, postBlockPrompt))
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %s", code, body)
			}
			e := g.entry(t)
			assertBlockLogged(t, e, "")
			if got := loggedMetadata(e)["answer_withheld"]; got != "true" {
				t.Errorf("log metadata answer_withheld = %v, want \"true\"", got)
			}
			if !tc.tokens {
				return
			}
			prompt, _ := e["prompt_tokens"].(float64)
			completion, _ := e["completion_tokens"].(float64)
			total, _ := e["total_tokens"].(float64)
			if prompt != 5 || completion != 9 || total != 14 {
				t.Errorf("log tokens = %v/%v/%v, want the provider's 5/9/14", e["prompt_tokens"], e["completion_tokens"], e["total_tokens"])
			}
			if cost, _ := e["cost"].(float64); cost <= 0 {
				t.Errorf("log cost = %v, want the stream's priced cost", e["cost"])
			}
		})
	}
}
