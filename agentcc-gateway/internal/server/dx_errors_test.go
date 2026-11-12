package server

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// DX-ERRORS-1: the answers a developer gets for the mistakes the one-key DX
// walk of 10-28 made, pinned body for body. Each was a misleading or
// unhelpful answer before: one 403 "not available for this API key" for a
// typo, for Future AGI credits being off and for a key restriction alike; a
// provider's 401 passed through as the gateway's own; an Anthropic 402 typed
// api_error; a bare 404 for a base_url missing or doubling /v1; Go type
// names in a request-validation error; and one 401 message for every key
// problem.

// dxBody is the answer's body as the client reads it, without the encoder's
// trailing newline.
func dxBody(w *httptest.ResponseRecorder) string {
	return strings.TrimRight(w.Body.String(), "\n")
}

// expectDXAnswer fails unless w is status with exactly body.
func expectDXAnswer(t *testing.T, w *httptest.ResponseRecorder, status int, body string) {
	t.Helper()
	if w.Code != status || dxBody(w) != body {
		t.Fatalf("answer:\n  got  %d %s\n  want %d %s", w.Code, dxBody(w), status, body)
	}
}

// dxRequest sends body to path on srv with the given headers.
func dxRequest(srv *Server, method, path, body string, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// A model nothing serves is a 404 model_not_found offering the close
// catalogue id, not a 403 that blames the key.
func TestDXUnknownModelIsNotFoundWithSuggestions(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	w := g.chat(t, chatBody("qwen3.7-flsh"), nil)
	expectDXAnswer(t, w, http.StatusNotFound, `{"error":{"message":"model \"qwen3.7-flsh\" was not found: no provider your organization can use serves it. Did you mean \"qwen3.7-flash\"? GET /v1/models lists the models this key can call.","type":"not_found","param":null,"code":"model_not_found","metadata":{"suggestions":["qwen3.7-flash"]}}}`)
	if n := g.managed.calls() + g.operator.calls(); n != 0 {
		t.Fatalf("upstream called %d times for an unknown model", n)
	}

	// Nothing close: no suggestion, no metadata.
	w = g.chat(t, chatBody("my-model"), nil)
	expectDXAnswer(t, w, http.StatusNotFound, `{"error":{"message":"model \"my-model\" was not found: no provider your organization can use serves it. GET /v1/models lists the models this key can call.","type":"not_found","param":null,"code":"model_not_found"}}`)
}

// A Future AGI model while the org's Future AGI credits are off says so and
// where to turn them on, by its catalogue id and by its bare name.
func TestDXFutureAGIModelWithCreditsOff(t *testing.T) {
	g := newManagedTestGateway(t, &tenant.OrgConfig{})
	for _, model := range []string{"anthropic/claude-sonnet-5.5", "claude-sonnet-5.5"} {
		w := g.chat(t, chatBody(model), nil)
		expectDXAnswer(t, w, http.StatusForbidden, `{"error":{"message":"model \"`+model+`\" is a Future AGI model, and Future AGI credits are off for your organization. Turn on Future AGI credits on the Gateway Models page, or add your own provider key on the Gateway Providers page.","type":"permission_error","param":null,"code":"future_agi_credits_off"}}`)
	}
	if n := g.managed.calls(); n != 0 {
		t.Fatalf("managed upstream called %d times with credits off", n)
	}
}

// A model the key's allowed_models leave out is 403 model_not_permitted
// naming the restriction, whether or not anything serves the model.
func TestDXKeyRestrictionNamesAllowedModels(t *testing.T) {
	g := newAsstGateway(t, asstKeyOpts{models: []string{"gpt-4o"}}, false)
	for _, model := range []string{"gpt-4o-mini", "gpt-4o-minii"} {
		w := g.do(http.MethodPost, "/v1/chat/completions", chatBody(model), "")
		expectDXAnswer(t, w, http.StatusForbidden, `{"error":{"message":"API key does not have access to model: `+model+`. This key is restricted to its allowed models (gpt-4o); call one of them, or use a key without this restriction.","type":"permission_error","param":null,"code":"model_not_permitted","metadata":{"reason":"key_model_denied"}}}`)
	}
}

// dxBYOKOrg is an org whose own key serves gpt-4o on the gateway's
// "operator" provider (its loopback upstream), with retries off.
func dxBYOKOrg() *tenant.OrgConfig {
	return &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true},
		},
		Routing: &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: false}},
	}
}

// A BYOK provider that rejects the org's saved key is a 502
// provider_auth_failed naming that provider, never a 401 with the gateway's
// own Bearer challenge.
func TestDXProviderAuthFailureIsNotAGatewayAuthFailure(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		g := newManagedTestGateway(t, dxBYOKOrg())
		g.operator.status.Store(int32(status))
		w := g.chat(t, chatBody("gpt-4o"), nil)
		code := "401"
		if status == http.StatusForbidden {
			code = "403"
		}
		expectDXAnswer(t, w, http.StatusBadGateway, `{"error":{"message":"Your provider \"operator\" rejected the API key saved for it (HTTP `+code+`). Update that key on the Gateway Providers page; your gateway API key is valid. The provider said: upstream down","type":"upstream_error","param":null,"code":"provider_auth_failed","metadata":{"provider":"operator","provider_message":"upstream down","provider_status":`+code+`,"provider_type":"server_error"}}}`)
		if got := w.Header().Get("WWW-Authenticate"); got != "" {
			t.Fatalf("a provider's %d carries the gateway's challenge %q", status, got)
		}
	}
}

// Other provider failures name the provider, carry a well-formed
// provider_<status> code in place of the provider's empty one, and keep the
// provider's own message under metadata.provider_message.
func TestDXProviderFailureNamesTheProvider(t *testing.T) {
	cases := []struct {
		upstream, status int
		want             string
	}{
		{http.StatusInternalServerError, http.StatusBadGateway,
			`{"error":{"message":"Provider \"operator\" returned HTTP 500: upstream down","type":"upstream_error","param":null,"code":"provider_500","metadata":{"provider":"operator","provider_message":"upstream down","provider_status":500,"provider_type":"server_error"}}}`},
		{http.StatusNotFound, http.StatusNotFound,
			`{"error":{"message":"Provider \"operator\" returned HTTP 404 (check that provider's base_url and api_format on the Gateway Providers page): upstream down","type":"server_error","param":null,"code":"provider_404","metadata":{"provider":"operator","provider_message":"upstream down","provider_status":404,"provider_type":"server_error"}}}`},
		{http.StatusTooManyRequests, http.StatusTooManyRequests,
			`{"error":{"message":"Provider \"operator\" returned HTTP 429: upstream down","type":"server_error","param":null,"code":"provider_429","metadata":{"provider":"operator","provider_message":"upstream down","provider_status":429,"provider_type":"server_error"}}}`},
	}
	for _, c := range cases {
		g := newManagedTestGateway(t, dxBYOKOrg())
		g.operator.status.Store(int32(c.upstream))
		expectDXAnswer(t, g.chat(t, chatBody("gpt-4o"), nil), c.status, c.want)
	}
}

// The gateway's 401s tell apart no key, a Future AGI API key, a key of
// another format and a gateway key it does not accept (unknown or revoked
// alike), and say what to do.
func TestDXUnauthorizedTellsKeyProblemsApart(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	const (
		missing   = `{"error":{"message":"No API key was sent. Send your gateway API key as \"Authorization: Bearer sk-agentcc-...\" (or in the x-api-key header); create one on the Gateway API Keys page.","type":"authentication_error","param":null,"code":"invalid_api_key","metadata":{"reason":"missing_api_key"}}}`
		futureAGI = `{"error":{"message":"This looks like a Future AGI API key (from the Build Keys page), which the gateway does not accept. Use a gateway API key instead: gateway keys start with sk-agentcc- and are created on the Gateway API Keys page.","type":"authentication_error","param":null,"code":"invalid_api_key","metadata":{"reason":"future_agi_api_key"}}}`
		other     = `{"error":{"message":"This API key is not recognized, and it is not a gateway key: gateway keys start with sk-agentcc- and are created on the Gateway API Keys page. A key a model vendor issued is added on the Gateway Providers page, not sent to the gateway.","type":"authentication_error","param":null,"code":"invalid_api_key","metadata":{"reason":"not_a_gateway_key"}}}`
		unknown   = `{"error":{"message":"This gateway API key is not recognized: it may be mistyped, revoked or deleted. Check it on the Gateway API Keys page, or create a new one there.","type":"authentication_error","param":null,"code":"invalid_api_key","metadata":{"reason":"unknown_api_key"}}}`
	)
	cases := []struct {
		name, auth, want string
	}{
		{"no key", "", missing},
		{"a Future AGI API key", "Bearer 0123456789abcdef0123456789abcdef", futureAGI},
		{"an OpenRouter key", "Bearer sk-or-v1-not-a-real-key", other},
		{"an unknown gateway key", "Bearer sk-agentcc-0000", unknown},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			headers := map[string]string{}
			if c.auth != "" {
				headers["Authorization"] = c.auth
			}
			w := dxRequest(g.srv, http.MethodPost, "/v1/chat/completions", chatBody("gpt-4o"), headers)
			expectDXAnswer(t, w, http.StatusUnauthorized, c.want)
			if got := w.Header().Get("WWW-Authenticate"); got != `Bearer realm="agentcc-gateway"` {
				t.Fatalf("WWW-Authenticate = %q", got)
			}
		})
	}

	// A revoked key is answered as an unknown one: the answer never says a
	// key of the gateway's format exists.
	for _, k := range g.srv.keyStore.List() {
		g.srv.keyStore.Revoke(k.ID)
	}
	w := dxRequest(g.srv, http.MethodPost, "/v1/chat/completions", chatBody("gpt-4o"), map[string]string{"Authorization": "Bearer " + managedOrgKey})
	expectDXAnswer(t, w, http.StatusUnauthorized, unknown)
}

// The Messages API answers its 401 and its 402 in the Anthropic error shape,
// the 402 typed billing_error, so Anthropic SDKs read both.
func TestDXMessagesErrorsAreAnthropicShaped(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	w := dxRequest(g.srv, http.MethodPost, "/v1/messages", managedMessagesBody("anthropic/claude-sonnet-5.5", false, ""),
		map[string]string{"x-api-key": "sk-agentcc-0000", "anthropic-version": "2023-06-01"})
	expectDXAnswer(t, w, http.StatusUnauthorized, `{"type":"error","error":{"type":"authentication_error","message":"This gateway API key is not recognized: it may be mistyped, revoked or deleted. Check it on the Gateway API Keys page, or create a new one there."}}`)
	if got := w.Header().Get("WWW-Authenticate"); got != `Bearer realm="agentcc-gateway"` {
		t.Fatalf("WWW-Authenticate = %q", got)
	}

	g.grant(t, "org-m", 0) // an account that was never given credit
	w = postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("anthropic/claude-sonnet-5.5", false, ""))
	expectDXAnswer(t, w, http.StatusPaymentRequired, `{"type":"error","error":{"type":"billing_error","message":"Your organization has no Future AGI credit. Add credit on the Settings Billing page, or use your own provider keys on the Gateway Providers page."}}`)
}

// The chat 402 says Future AGI credit, never "managed", does not say "left"
// to an org that never had credit, and names where to add it.
func TestDXCreditRefusalSaysFutureAGICredit(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.grant(t, "org-m", 0)
	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
	resp := errorObject(t, w)
	want := "Your organization has no Future AGI credit. Add credit on the Settings Billing page, or use your own provider keys on the Gateway Providers page."
	if w.Code != http.StatusPaymentRequired || resp["code"] != "managed_credits_exhausted" || resp["message"] != want {
		t.Fatalf("status %d, code %v, message %q; want 402 managed_credits_exhausted %q", w.Code, resp["code"], resp["message"], want)
	}
}

// A base_url missing /v1 or doubling it gets a 404 that says how to set it.
func TestDXUnknownEndpointHintsAtBaseURL(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	auth := map[string]string{"Authorization": "Bearer " + managedOrgKey}
	expectDXAnswer(t, dxRequest(g.srv, http.MethodPost, "/chat/completions", chatBody("gpt-4o"), nil), http.StatusNotFound,
		`{"error":{"message":"Unknown endpoint: /chat/completions. Did you mean /v1/chat/completions? Set your OpenAI SDK's base_url to the gateway URL ending in /v1.","type":"not_found","param":null,"code":"not_found"}}`)
	expectDXAnswer(t, dxRequest(g.srv, http.MethodPost, "/v1/v1/messages", managedMessagesBody("anthropic/claude-sonnet-5.5", false, ""), auth), http.StatusNotFound,
		`{"error":{"message":"Unknown endpoint: /v1/v1/messages. The path has /v1 twice: the Anthropic SDK adds /v1 itself, so set its base_url to the gateway URL without /v1.","type":"not_found","param":null,"code":"not_found"}}`)
	expectDXAnswer(t, dxRequest(g.srv, http.MethodPost, "/v1/v1/chat/completions", chatBody("gpt-4o"), auth), http.StatusNotFound,
		`{"error":{"message":"Unknown endpoint: /v1/v1/chat/completions. The path has /v1 twice: did you mean /v1/chat/completions? Set your SDK's base_url so it ends in /v1 only once.","type":"not_found","param":null,"code":"not_found"}}`)
	// A path no route is near keeps the plain answer.
	expectDXAnswer(t, dxRequest(g.srv, http.MethodPost, "/v1/nothing-here", "{}", auth), http.StatusNotFound,
		`{"error":{"message":"Unknown endpoint: /v1/nothing-here.","type":"not_found","param":null,"code":"not_found"}}`)
}

// A request body of the wrong shape is described in JSON terms, never with
// a Go type name.
func TestDXInvalidJSONNamesNoGoTypes(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	expectDXAnswer(t, g.chat(t, `42`, nil), http.StatusBadRequest,
		`{"error":{"message":"Invalid JSON in request body: the body must be a JSON object, not a number.","type":"invalid_request_error","param":null,"code":"invalid_json"}}`)
	expectDXAnswer(t, g.chat(t, `{"model":"gpt-4o","messages":"hi"}`, nil), http.StatusBadRequest,
		`{"error":{"message":"Invalid JSON in request body: \"messages\" must be an array, not a string.","type":"invalid_request_error","param":null,"code":"invalid_json"}}`)
	// A syntax error keeps encoding/json's description, which names only JSON.
	expectDXAnswer(t, g.chat(t, `{"model": "gpt-4o", "messages": [`, nil), http.StatusBadRequest,
		`{"error":{"message":"Invalid JSON in request body: unexpected end of JSON input","type":"invalid_request_error","param":null,"code":"invalid_json"}}`)
}
