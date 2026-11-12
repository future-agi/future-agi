package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
)

// The speech relays (/deepgram/..., /cartesia/...) sit outside /v1/ and take the
// gateway key the way each vendor's own SDK sends it: Deepgram's
// "Authorization: Token <key>", Cartesia's X-API-Key header or api_key query
// parameter, or a Bearer token. KeyAuth guards every non-public route, so it
// must accept the same forms or those SDKs are locked out before the relay's
// own pipeline auth ever runs.
func TestKeyAuthAcceptsSpeechVendorKeyForms(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{
		Name: "test",
		Key:  "valid-key",
	}}})
	handler := KeyAuth(keyStore, true)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	endpoints := speech.Endpoints()
	if len(endpoints) == 0 {
		t.Fatal("expected registered speech relay endpoints")
	}
	forms := []struct {
		name  string
		query string
		set   func(*http.Request, string)
	}{
		{name: "authorization token", set: func(r *http.Request, k string) { r.Header.Set("Authorization", "Token "+k) }},
		{name: "authorization bearer", set: func(r *http.Request, k string) { r.Header.Set("Authorization", "Bearer "+k) }},
		{name: "x-api-key header", set: func(r *http.Request, k string) { r.Header.Set("X-API-Key", k) }},
		{name: "api_key query", query: "api_key="},
	}

	for _, ep := range endpoints {
		path := "/" + ep.Vendor + ep.Path
		for _, form := range forms {
			for _, tc := range []struct {
				key  string
				want int
			}{
				{key: "valid-key", want: http.StatusNoContent},
				{key: "wrong-key", want: http.StatusUnauthorized},
			} {
				t.Run(path+"/"+form.name+"/"+tc.key, func(t *testing.T) {
					target := path
					if form.query != "" {
						target += "?" + form.query + tc.key
					}
					request := httptest.NewRequest(http.MethodGet, target, nil)
					if form.set != nil {
						form.set(request, tc.key)
					}
					response := httptest.NewRecorder()
					handler.ServeHTTP(response, request)
					if response.Code != tc.want {
						t.Fatalf("%s %s: expected %d, got %d", form.name, target, tc.want, response.Code)
					}
				})
			}
		}
	}

	t.Run("no key", func(t *testing.T) {
		request := httptest.NewRequest(http.MethodGet, "/"+endpoints[0].Vendor+endpoints[0].Path, nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusUnauthorized {
			t.Fatalf("expected 401 without a key, got %d", response.Code)
		}
	})
}

// The MCP handler matches the Bearer scheme case-insensitively, so KeyAuth
// in front of /mcp must too, or those clients are locked out with 401.
func TestKeyAuthBearerSchemeIsCaseInsensitive(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{
		Name: "test",
		Key:  "valid-key",
	}}})
	handler := KeyAuth(keyStore, true)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	for _, tc := range []struct {
		header string
		want   int
	}{
		{header: "Bearer valid-key", want: http.StatusNoContent},
		{header: "bearer valid-key", want: http.StatusNoContent},
		{header: "BEARER valid-key", want: http.StatusNoContent},
		{header: "bearer wrong-key", want: http.StatusUnauthorized},
		{header: "Basic valid-key", want: http.StatusUnauthorized},
	} {
		t.Run(tc.header, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, "/mcp", nil)
			request.Header.Set("Authorization", tc.header)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != tc.want {
				t.Fatalf("Authorization %q: expected %d, got %d", tc.header, tc.want, response.Code)
			}
		})
	}
}

// GenAIHandler prefers ?key= and x-goog-api-key over Authorization, so a
// Gemini client whose stack also adds an unrelated Bearer token (for example
// a Google OAuth token) must not be rejected by KeyAuth when its gateway key
// is valid. A request is let through when any candidate key authenticates;
// it is rejected when none does.
func TestKeyAuthAcceptsAnyValidCandidateKey(t *testing.T) {
	keyStore := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{
		Name: "test",
		Key:  "valid-key",
	}}})
	handler := KeyAuth(keyStore, true)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	const genai = "/v1beta/models/gemini-2.0-flash:generateContent"
	for _, tc := range []struct {
		name   string
		target string
		goog   string
		want   int
	}{
		{name: "x-goog-api-key with unrelated bearer", target: genai, goog: "valid-key", want: http.StatusNoContent},
		{name: "key query with unrelated bearer", target: genai + "?key=valid-key", want: http.StatusNoContent},
		{name: "every candidate invalid", target: genai + "?key=wrong-key", goog: "also-wrong", want: http.StatusUnauthorized},
	} {
		t.Run(tc.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, tc.target, nil)
			request.Header.Set("Authorization", "Bearer ya29.synthetic-oauth-token")
			if tc.goog != "" {
				request.Header.Set("x-goog-api-key", tc.goog)
			}
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != tc.want {
				t.Fatalf("%s: expected %d, got %d", tc.name, tc.want, response.Code)
			}
		})
	}
}
