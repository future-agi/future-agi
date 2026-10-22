package server

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

// The request log never stores a credential the caller sent in a header: a
// Google GenAI SDK client's gateway key in x-goog-api-key, and every other
// credential header a caller sends (a proxy's credentials, a cloud session
// token, a provider or guardrail key).

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
// its gateway key, each with a value the request log must never hold: the
// named list, the name rule and further edge cases.
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
	{"X-OpenAI-Key", "planted-openai-key-a1b2"},
	{"X-Anthropic-Key", "planted-anthropic-key-c3d4"},
	{"Helicone-Auth", "Bearer planted-helicone-e5f6"},
	{"X-Portkey-Virtual-Key", "planted-portkey-virtual-0a1b"},
}

// plantedGoogleKey is a Google key a client sends to a route other than
// /v1beta, where x-goog-api-key is not how it authenticates.
const plantedGoogleKey = "planted-goog-api-key-0d1c"

// plainHeaders are headers a client sends that hold no credential; the
// request log keeps them as sent (the rule does not over-match).
var plainHeaders = map[string]string{
	"X-Goog-Api-Client":   "genai-js/1.0.0",
	"X-Token-Count":       "42",
	"Idempotency-Key":     "idem-7e8f",
	"X-Agentcc-Cache-Key": "cache-9a0b",
}

func TestRequestLog_NeverStoresACredentialHeader(t *testing.T) {
	type route struct {
		name, path, body string
		genai            bool
	}
	routes := []route{
		{"chat completions", "/v1/chat/completions", credGWChatBody(), false},
		{"messages, translated", "/v1/messages", credGWMessagesBody(), false},
		{"generateContent, pass-through", "/v1beta/models/" + credGWGemini + ":generateContent", credGWGenAIBody(), true},
		{"generateContent, translated", "/v1beta/models/gpt-4o:generateContent", credGWGenAIBody(), true},
	}
	for _, rt := range routes {
		forms := []credentialHeaderForm{bearerForm, xAPIKeyForm}
		if rt.genai {
			// The Google SDKs' header and query parameter, and a bearer token.
			forms = []credentialHeaderForm{googAPIKeyForm, queryKeyForm, bearerForm}
		}
		for _, form := range forms {
			t.Run(rt.name+" / "+form.name, func(t *testing.T) {
				g := newCredGW(t, nil)
				req := credGWRequest(rt.path, rt.body)
				req.Header.Set("anthropic-version", "2023-06-01")
				for _, h := range plantedCredentialHeaders {
					req.Header.Set(h.name, h.value)
				}
				for name, value := range plainHeaders {
					req.Header.Set(name, value)
				}
				if !rt.genai {
					// On /v1beta x-goog-api-key is the gateway key, read
					// before Authorization: a second one there would replace
					// the key.
					req.Header.Set("X-Goog-Api-Key", plantedGoogleKey)
				}
				form.apply(req, credGWKey)

				code, body := g.serve(t, req)
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
				if strings.Contains(entry, credGWKey) {
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
				for name, value := range plainHeaders {
					got, _ := headers[name].([]any)
					if len(got) != 1 || got[0] != value {
						t.Errorf("a plain header was not kept as sent: %s = %v, want [%s]", name, headers[name], value)
					}
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
