package server

import (
	"net/http"
	"strings"
	"sync"
	"testing"
)

// On the native Google API a credential header the client sends never leaves
// the gateway: the provider gets the organization's own Google key, and the
// client's plain X-Goog-* headers as sent.

// googleUpstreamHeaders records the headers of every request the provider's
// upstream gets on the native Google API.
type googleUpstreamHeaders struct {
	mu   sync.Mutex
	seen []http.Header
}

func (g *googleUpstreamHeaders) record(r *http.Request) {
	g.mu.Lock()
	g.seen = append(g.seen, r.Header.Clone())
	g.mu.Unlock()
}

func (g *googleUpstreamHeaders) only(t *testing.T) http.Header {
	t.Helper()
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.seen) != 1 {
		t.Fatalf("the upstream got %d native Google requests, want 1", len(g.seen))
	}
	return g.seen[0]
}

const plantedGoogleIAMToken = "planted-goog-iam-token-2b3c"

func TestGenAINative_SendsTheProviderNoCallerCredential(t *testing.T) {
	for _, tc := range []struct {
		name, action, body string
	}{
		{"generateContent", "generateContent", credGWGenAIBody()},
		{"streamGenerateContent", "streamGenerateContent", credGWGenAIBody()},
		{"countTokens", "countTokens", credGWGenAIBody()},
		{"embedContent", "embedContent", `{"content":{"parts":[{"text":"` + credGWPrompt + `"}]}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream := &googleUpstreamHeaders{}
			g := newCredGW(t, upstream.record)
			req := credGWRequest("/v1beta/models/"+credGWGemini+":"+tc.action, tc.body)
			req.Header.Set("x-goog-api-key", credGWKey)
			req.Header.Set("X-Goog-Iam-Authorization-Token", plantedGoogleIAMToken)
			req.Header.Set("X-Goog-Api-Client", "genai-js/1.0.0")
			req.Header.Set("X-Goog-User-Project", "proj-credgw")

			code, body := g.serve(t, req)
			if code != http.StatusOK {
				t.Fatalf("status %d, want 200; body %s", code, body)
			}

			got := upstream.only(t)
			if key := got.Get("x-goog-api-key"); key != credGWOrgKey {
				t.Errorf("the provider was sent x-goog-api-key %q, want the organization's own key", key)
			}
			for name, values := range got {
				for _, v := range values {
					if strings.Contains(v, credGWKey) || strings.Contains(v, plantedGoogleIAMToken) {
						t.Errorf("the provider was sent the client's credential in %s", name)
					}
				}
			}
			if v := got.Get("X-Goog-Api-Client"); v != "genai-js/1.0.0" {
				t.Errorf("X-Goog-Api-Client = %q, want the client's header forwarded as sent", v)
			}
			if v := got.Get("X-Goog-User-Project"); v != "proj-credgw" {
				t.Errorf("X-Goog-User-Project = %q, want the client's header forwarded as sent", v)
			}
		})
	}
}
