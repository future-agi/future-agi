package server

// The gateway's own video settlement keys (video_settlement, video_wire) are
// written only by the video settlement path. Request metadata may not set
// them, from the x-agentcc-metadata header or the body's metadata, so a chat
// answer's spend is counted in the org budget whatever metadata it carries.
// The requests go through the real server, key sync and org config push, as
// r7b's budget console test sends them.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func (g *r7bGateway) chatBody(t *testing.T, body string, hdr map[string]string) r7bAnswer {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+r7bCust)
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	var out struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	return r7bAnswer{code: w.Code, errCode: out.Error.Code, retryAfter: w.Header().Get("Retry-After")}
}

func TestChatSpendCountsInTheOrgBudgetWhateverTheRequestMetadataSays(t *testing.T) {
	const plain = `{"model":"org-model","messages":[{"role":"user","content":"hello"}]}`
	const settlement = `{"video_settlement":"direct","video_wire":"openai"}`
	cases := []struct {
		name string
		body string
		hdr  map[string]string
	}{
		{"x-agentcc-metadata header", plain, map[string]string{"x-agentcc-metadata": settlement}},
		{"the body's metadata", `{"model":"org-model","messages":[{"role":"user","content":"hello"}],"metadata":` + settlement + `}`, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := r7bStart(t)
			g.push(t, r7bFixture(t, "org_block"))
			first := g.chatBody(t, tc.body, tc.hdr)
			second := g.chatBody(t, tc.body, tc.hdr)
			if first.code != http.StatusOK || second.errCode != "org_budget_exceeded" {
				t.Fatalf("got %v, want [OK, 429 org_budget_exceeded]: the first answer's spend was not counted in the org budget",
					r7bCodes([]r7bAnswer{first, second}))
			}
			if n := g.calls.Load(); n != 1 {
				t.Fatalf("upstream called %d times, want 1", n)
			}
		})
	}
}
