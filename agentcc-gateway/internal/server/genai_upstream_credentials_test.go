package server

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// On the native Google API the provider gets the organization's own key and
// the client's plain X-Goog-* headers; no credential header the client
// sends leaves the gateway.

// googleUpstreamHeaders records the headers of every request the provider's
// upstream gets on the native Google API, and lets the leak upstream answer.
type googleUpstreamHeaders struct {
	mu   sync.Mutex
	seen []http.Header
}

func (g *googleUpstreamHeaders) record(_ http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/v1beta/") {
		g.mu.Lock()
		g.seen = append(g.seen, r.Header.Clone())
		g.mu.Unlock()
	}
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
		{"generateContent", "generateContent", genaiBody()},
		{"streamGenerateContent", "streamGenerateContent", genaiBody()},
		{"countTokens", "countTokens", genaiBody()},
		{"embedContent", "embedContent", `{"content":{"parts":[{"text":"` + postBlockPrompt + `"}]}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream := &googleUpstreamHeaders{}
			g := newPostBlockGateway(t, postBlockSetup{upstream: upstream.record})
			req := httptest.NewRequest(http.MethodPost, "/v1beta/models/"+postBlockGemini+":"+tc.action,
				bytes.NewReader([]byte(tc.body)))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("x-goog-api-key", postBlockKey)
			req.Header.Set("X-Goog-Iam-Authorization-Token", plantedGoogleIAMToken)
			req.Header.Set("X-Goog-Api-Client", "genai-js/1.0.0")
			ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
			defer cancel()
			w := httptest.NewRecorder()
			g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
			if w.Code != http.StatusOK {
				b, _ := io.ReadAll(w.Result().Body)
				t.Fatalf("status %d, want 200; body %s", w.Code, b)
			}

			got := upstream.only(t)
			if key := got.Get("x-goog-api-key"); key != "org-key" {
				t.Errorf("the provider was sent x-goog-api-key %q, want the organization's own key", key)
			}
			for name, values := range got {
				for _, v := range values {
					if strings.Contains(v, postBlockKey) || strings.Contains(v, plantedGoogleIAMToken) {
						t.Errorf("the provider was sent the client's credential in %s", name)
					}
				}
			}
			if v := got.Get("X-Goog-Api-Client"); v != "genai-js/1.0.0" {
				t.Errorf("X-Goog-Api-Client = %q, want the client's header forwarded", v)
			}
		})
	}
}
