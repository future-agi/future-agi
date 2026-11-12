package server

import (
	"fmt"
	"testing"
)

func TestDXCountTokensRestrictedEnvelope(t *testing.T) {
	for _, model := range []string{"gpt-4o-mini", "unknown-count-model"} {
		t.Run(model, func(t *testing.T) {
			g := newAsstGateway(t, asstKeyOpts{models: []string{"gpt-4o"}}, false)
			w := g.do("POST", "/v1/messages/count_tokens", fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hi"}]}`, model), "")
			expectDXAnswer(t, w, 403, `{"type":"error","error":{"type":"permission_error","message":"API key does not have access to model: `+model+`. This key is restricted to its allowed models (gpt-4o); call one of them, or use a key without this restriction."}}`)
			if g.up.calls.Load() != 0 {
				t.Fatalf("excluded model dispatched %d calls", g.up.calls.Load())
			}
		})
	}
}
