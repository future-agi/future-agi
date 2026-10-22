package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// The request log never stores a credential the caller sent: the gateway
// key in whichever form the client sends it (x-goog-api-key and ?key=
// included), and every other credential header a caller sends (a proxy's
// credentials, a cloud session token, a provider or guardrail key).

// credentialHeaderForm is one way a client authenticates to the gateway with
// its key.
type credentialHeaderForm struct {
	name  string
	apply func(r *http.Request, key string)
}

var (
	bearerForm = credentialHeaderForm{"Authorization: Bearer", func(r *http.Request, key string) {
		r.Header.Set("Authorization", "Bearer "+key)
	}}
	xAPIKeyForm = credentialHeaderForm{"x-api-key", func(r *http.Request, key string) {
		r.Header.Set("x-api-key", key)
	}}
	googAPIKeyForm = credentialHeaderForm{"x-goog-api-key", func(r *http.Request, key string) {
		r.Header.Set("x-goog-api-key", key)
	}}
	queryKeyForm = credentialHeaderForm{"?key=", func(r *http.Request, key string) {
		q := r.URL.Query()
		q.Set("key", key)
		r.URL.RawQuery = q.Encode()
	}}
)

// plantedCredentialHeaders are credential headers a client may send besides
// its gateway key, each with a value the request log must never hold.
var plantedCredentialHeaders = []struct{ name, value string }{
	{"Proxy-Authorization", "Basic cGxhbnRlZC1wcm94eS1jcmVkZW50aWFs"},
	{"X-Amz-Security-Token", "planted-amz-security-token-1f2e"},
	{"X-Vault-Token", "hvs.planted-vault-token-3d4c"},
	{"X-Secret-Key", "planted-secret-key-5b6a"},
	{"Ocp-Apim-Subscription-Key", "planted-apim-subscription-7f8e"},
	{"Lasso-Api-Key", "planted-lasso-key-9d0c"},
	{"Grayswan-Api-Key", "planted-grayswan-key-1b2a"},
	{"Apikey", "planted-apikey-3f4e"},
	{"X-Auth-Token", "planted-auth-token-5d6c"},
	{"X-Client-Secret", "planted-client-secret-7b8a"},
	{"Cookie", "session=planted-cookie-9f0e"},
}

// plantedGoogleKey is a Google key a client sends to a route other than
// /v1beta, where x-goog-api-key is not how it authenticates.
const plantedGoogleKey = "planted-goog-api-key-0d1c"

// sendWithCredentials sends body to path authenticated with the gateway key
// in form, carrying every planted credential header too, and returns the
// status the client got.
func (g *postBlockGateway) sendWithCredentials(t *testing.T, path, body string, form credentialHeaderForm, genai bool) (int, string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	req.Header.Set("X-Goog-Api-Client", "genai-js/1.0.0")
	for _, h := range plantedCredentialHeaders {
		req.Header.Set(h.name, h.value)
	}
	if !genai {
		// On /v1beta x-goog-api-key is the gateway key, read before
		// Authorization: a second one there would replace the key.
		req.Header.Set("X-Goog-Api-Key", plantedGoogleKey)
	}
	form.apply(req, postBlockKey)
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

func TestRequestLog_NeverStoresACredentialHeader(t *testing.T) {
	type route struct {
		name, path, body string
		genai            bool
	}
	chat := `{"model":"gpt-4o","messages":[{"role":"user","content":"` + postBlockPrompt + `"}]}`
	routes := []route{
		{"chat completions", "/v1/chat/completions", chat, false},
		{"completions", "/v1/completions", completionsBody("gpt-4o", false), false},
		{"messages, translated", "/v1/messages", messagesBody("gpt-4o", false), false},
		{"messages, pass-through", "/v1/messages", messagesBody(postBlockClaude, false), false},
		{"responses", "/v1/responses", responsesBody(false), false},
		{"generateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":generateContent", genaiBody(), true},
		{"generateContent, translated", "/v1beta/models/gpt-4o:generateContent", genaiBody(), true},
	}
	for _, rt := range routes {
		forms := []credentialHeaderForm{bearerForm, xAPIKeyForm}
		if rt.genai {
			// The Google SDKs' header and query parameter, and a bearer token.
			forms = []credentialHeaderForm{googAPIKeyForm, queryKeyForm, bearerForm}
		}
		for _, form := range forms {
			t.Run(rt.name+" / "+form.name, func(t *testing.T) {
				// The request log keeps request headers here: include_bodies, and
				// content mode full.
				g := newPostBlockGateway(t, postBlockSetup{capture: true})
				code, body := g.sendWithCredentials(t, rt.path, rt.body, form, rt.genai)
				if code != http.StatusOK {
					t.Fatalf("status %d, want 200 (the key in %s authenticates); body %s", code, form.name, body)
				}
				e := g.entry(t)
				headers, ok := e["request_headers"].(map[string]any)
				if !ok || len(headers) == 0 {
					t.Fatalf("the log entry kept no request headers (%v); this test needs them kept", e["request_headers"])
				}
				raw, err := json.Marshal(e)
				if err != nil {
					t.Fatal(err)
				}
				entry := string(raw)
				if strings.Contains(entry, postBlockKey) {
					t.Errorf("the log entry holds the gateway key the client sent in %s: request_headers %v", form.name, headers)
				}
				for _, h := range plantedCredentialHeaders {
					if strings.Contains(entry, h.value) {
						t.Errorf("the log entry holds the value the client sent in %s", h.name)
					}
				}
				if !rt.genai && strings.Contains(entry, plantedGoogleKey) {
					t.Error("the log entry holds the value the client sent in X-Goog-Api-Key")
				}
				if got := headers["X-Goog-Api-Client"]; got == nil {
					t.Errorf("a plain header was dropped: X-Goog-Api-Client %v", got)
				}
				if rt.genai && form.name == googAPIKeyForm.name {
					// The handler takes its copy of the headers once the key
					// is read, without the header the key came in.
					if v, held := headers["X-Goog-Api-Key"]; held {
						t.Errorf("the request's copy of its headers holds X-Goog-Api-Key (%v), the header its key came in", v)
					}
				}
			})
		}
	}
}
