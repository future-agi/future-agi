package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Real adapters, synthetic loopback upstreams, and an org-owned credential.
// The non-streaming native adapters return status/body with a nil Go error.
func dxNativeGateway(t *testing.T, format string, status int) *Server {
	t.Helper()
	return dxNativeGatewayReply(t, format, status, `{"error":{"message":"synthetic upstream refusal"}}`)
}

func dxNativeGatewayReply(t *testing.T, format string, status int, body string) *Server {
	t.Helper()
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		fmt.Fprint(w, body)
	}))
	t.Cleanup(up.Close)
	cfg := config.DefaultConfig()
	off := false
	cfg.Providers[dxNativeID(format)] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic-operator-credential", APIFormat: format,
		Models: []string{"claude-probe", "gemini-probe", "gpt-probe"}, AutoDiscover: &off, Local: &off}
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "probe", Key: "sk-agentcc-dx-native", Owner: "synthetic-user", KeyType: "byok", Metadata: map[string]string{"org_id": "org-probe"}}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = registry.Close() })
	orgs := tenant.NewStore()
	orgs.Set("org-probe", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{dxNativeID(format): {
		Enabled: true, APIKey: "synthetic-org-credential", APIFormat: format, Models: []string{"claude-probe", "gemini-probe", "gpt-probe"},
	}}})
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), orgs, nil)
	srv.ready.Store(true)
	return srv
}

func dxNativeID(format string) string {
	if format == "openai" {
		return "openai"
	}
	return "native"
}

func TestDXR2NativeProviderFailures(t *testing.T) {
	for _, route := range []struct{ format, path, body string }{
		{"anthropic", "/v1/messages", `{"model":"claude-probe","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}`},
		{"anthropic", "/v1/messages/count_tokens", `{"model":"claude-probe","messages":[{"role":"user","content":"hi"}]}`},
		{"gemini", "/v1beta/models/gemini-probe:generateContent", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`},
		{"gemini", "/v1beta/models/gemini-probe:countTokens", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`},
		{"gemini", "/v1beta/models/gemini-probe:embedContent", `{"content":{"parts":[{"text":"hi"}]}}`},
		{"openai", "/v1/assistants", `{"model":"gpt-probe"}`},
		{"openai", "/v1/vector_stores", `{"name":"synthetic"}`},
	} {
		for _, status := range []int{401, 403, 404, 429, 500} {
			t.Run(fmt.Sprintf("%s/%d", route.path, status), func(t *testing.T) {
				srv := dxNativeGateway(t, route.format, status)
				w := dxRequest(srv, "POST", route.path, route.body, map[string]string{"Authorization": "Bearer sk-agentcc-dx-native"})
				name := dxNativeID(route.format)
				wantStatus, code := status, fmt.Sprintf("provider_%d", status)
				message := fmt.Sprintf("Provider %q returned HTTP %d: synthetic upstream refusal", name, status)
				if status == 404 {
					message = fmt.Sprintf(`Provider %q returned HTTP 404 (check that provider's base_url and api_format on the Gateway Providers page): synthetic upstream refusal`, name)
				}
				if status == 401 || status == 403 {
					wantStatus, code = 502, "provider_auth_failed"
					message = fmt.Sprintf(`Your provider %q rejected the API key saved for it (HTTP %d). Update that key on the Gateway Providers page; your gateway API key is valid. The provider said: synthetic upstream refusal`, name, status)
				}
				if route.format == "openai" && status == 500 {
					wantStatus = 502 // canonical adapter's existing mapping
				}
				meta := map[string]any{"provider": name, "provider_status": float64(status), "provider_message": "synthetic upstream refusal"}
				var want map[string]any
				switch route.format {
				case "anthropic":
					typ := "api_error"
					if status == 404 {
						typ = "not_found_error"
					}
					if status == 429 {
						typ = "rate_limit_error"
					}
					want = map[string]any{"type": "error", "error": map[string]any{"type": typ, "message": message, "code": code, "metadata": meta}}
				case "gemini":
					googleStatus := "INTERNAL"
					if status == 404 {
						googleStatus = "NOT_FOUND"
					}
					if status == 429 {
						googleStatus = "RESOURCE_EXHAUSTED"
					}
					want = map[string]any{"error": map[string]any{"code": float64(wantStatus), "message": message, "status": googleStatus,
						"details": []any{map[string]any{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": code, "domain": "agentcc-gateway", "metadata": map[string]any{"provider": name, "provider_status": fmt.Sprint(status), "provider_message": "synthetic upstream refusal"}}}}}
				default:
					want = map[string]any{"error": map[string]any{"type": "upstream_error", "message": message, "code": code, "param": nil, "metadata": meta}}
				}
				var got map[string]any
				if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
					t.Fatal(err)
				}
				if w.Code != wantStatus || !reflect.DeepEqual(got, want) {
					t.Fatalf("got %d %s; want %d %#v", w.Code, w.Body.String(), wantStatus, want)
				}
				if challenge := w.Header().Get("WWW-Authenticate"); challenge != "" {
					t.Fatalf("upstream failure has gateway challenge %q", challenge)
				}
			})
		}
	}
}

func TestDXR2ProviderLockUsesModelClassifier(t *testing.T) {
	for _, tc := range []struct {
		name, model         string
		enabled, restricted bool
		status              int
		code                string
	}{
		{"unknown", "qwen3.7-flsh", true, false, 404, "model_not_found"},
		{"credits-off", "anthropic/claude-sonnet-5.5", false, false, 403, "future_agi_credits_off"},
		{"restricted", "qwen3.7-flsh", false, true, 403, "model_not_permitted"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			org := &tenant.OrgConfig{}
			if tc.enabled {
				org = managedOptIn()
			}
			g := newManagedTestGateway(t, org)
			if tc.restricted {
				for _, key := range g.srv.keyStore.List() {
					if _, err := g.srv.keyStore.Update(key.ID, nil, nil, []string{"openai/gpt-6-sol"}, nil, nil); err != nil {
						t.Fatal(err)
					}
				}
			}
			unlocked := g.chat(t, chatBody(tc.model), nil)
			if unlocked.Code != tc.status || errorObject(t, unlocked)["code"] != tc.code {
				t.Fatalf("unlocked baseline: %d %s", unlocked.Code, unlocked.Body.String())
			}
			locked := g.chat(t, chatBody(tc.model), map[string]string{"x-agentcc-provider-lock": "openrouter"})
			expectDXAnswer(t, locked, tc.status, dxBody(unlocked))
			if g.managed.calls()+g.operator.calls() != 0 {
				t.Fatal("refusal called a provider")
			}
		})
	}
}

func TestDXR2AssistantsJSONValidation(t *testing.T) {
	g := newAsstGateway(t, asstKeyOpts{}, false)
	for _, tc := range []struct{ body, reason string }{
		{`42`, "the body must be a JSON object, not a number."},
		{`[]`, "the body must be a JSON object, not an array."},
		{`{"model":42}`, `"model" must be a string, not a number.`},
		{`{"stream":"yes"}`, `"stream" must be true or false, not a string.`},
	} {
		t.Run(tc.body, func(t *testing.T) {
			w := g.do("POST", "/v1/assistants", tc.body, "")
			message, _ := json.Marshal("Invalid JSON in request body: " + tc.reason)
			expectDXAnswer(t, w, 400, `{"error":{"message":`+string(message)+`,"type":"invalid_request_error","param":null,"code":"invalid_json"}}`)
		})
	}
}
