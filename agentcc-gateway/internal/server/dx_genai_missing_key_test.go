package server

import (
	"net/http"
	"testing"
)

// The native Google GenAI API under
// /v1beta reads its gateway key from the key query parameter, the
// x-goog-api-key header or an "Authorization: Bearer" header, never from
// x-api-key (middleware.GenAIRequestAPIKey). Its missing-key 401, written by
// the key gate in the OpenAI envelope like every route but /v1/messages,
// names those three and not x-api-key. /v1 keeps its own answer.
func TestDXGenAIMissingKeyNamesTheGoogleKeyForms(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	const (
		genaiMissing = `{"error":{"message":"No API key was sent. Send your gateway API key as \"Authorization: Bearer sk-agentcc-...\", in the x-goog-api-key header or in the key query parameter; create one on the Gateway API Keys page.","type":"authentication_error","param":null,"code":"invalid_api_key","metadata":{"reason":"missing_api_key"}}}`
		v1Missing    = `{"error":{"message":"No API key was sent. Send your gateway API key as \"Authorization: Bearer sk-agentcc-...\" (or in the x-api-key header); create one on the Gateway API Keys page.","type":"authentication_error","param":null,"code":"invalid_api_key","metadata":{"reason":"missing_api_key"}}}`
		genaiBody    = `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`
	)
	for _, c := range []struct {
		name, path string
		headers    map[string]string
	}{
		{"generateContent without a key", "/v1beta/models/gemini-x:generateContent", nil},
		{"streamGenerateContent without a key", "/v1beta/models/gemini-x:streamGenerateContent?alt=sse", nil},
		{"countTokens without a key", "/v1beta/models/gemini-x:countTokens", nil},
		// x-api-key is not read on /v1beta: such a request sent no key here.
		{"x-api-key only", "/v1beta/models/gemini-x:generateContent", map[string]string{"x-api-key": "sk-agentcc-0000"}},
		{"Authorization without the Bearer scheme", "/v1beta/models/gemini-x:generateContent", map[string]string{"Authorization": "Basic c3ludGhldGlj"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			w := dxRequest(g.srv, http.MethodPost, c.path, genaiBody, c.headers)
			expectDXAnswer(t, w, http.StatusUnauthorized, genaiMissing)
			if got := w.Header().Get("WWW-Authenticate"); got != `Bearer realm="agentcc-gateway"` {
				t.Fatalf("WWW-Authenticate = %q", got)
			}
		})
	}
	t.Run("/v1 control", func(t *testing.T) {
		w := dxRequest(g.srv, http.MethodPost, "/v1/chat/completions", chatBody("gpt-4o"), nil)
		expectDXAnswer(t, w, http.StatusUnauthorized, v1Missing)
	})
	if n := g.managed.calls() + g.operator.calls(); n != 0 {
		t.Fatalf("upstream called %d times for requests without a key", n)
	}
}
