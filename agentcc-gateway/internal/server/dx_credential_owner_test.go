package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Provider names are identical in the org and platform registry. Only the
// selected provider instance (and its actual outgoing credential) identifies
// the owner; neither the name nor the existence of an org config is enough.
func TestDXResponsesCompletionsCredentialOwner(t *testing.T) {
	for _, surface := range []string{"responses", "completions"} {
		for _, stream := range []bool{false, true} {
			for _, owner := range []string{"org-model", "org-map", "platform"} {
				for _, status := range []int{401, 403, 404} {
					t.Run(fmt.Sprintf("%s/stream=%v/%s/%d", surface, stream, owner, status), func(t *testing.T) {
						var calls atomic.Int32
						wantCredential := "synthetic-platform-credential"
						if owner != "platform" {
							wantCredential = "synthetic-org-credential"
						}
						up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
							calls.Add(1)
							if r.Header.Get("Authorization") != "Bearer "+wantCredential {
								t.Errorf("wrong selected credential")
							}
							w.Header().Set("Content-Type", "application/json")
							w.WriteHeader(status)
							fmt.Fprint(w, `{"error":{"message":"synthetic refusal","type":"invalid_request_error","code":"fixture_code"}}`)
						}))
						defer up.Close()
						cfg := config.DefaultConfig()
						off := false
						model := "custom-probe"
						cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic-platform-credential", APIFormat: "openai", Models: []string{model}, AutoDiscover: &off, Local: &off}
						if owner == "org-model" {
							p := cfg.Providers["openai"]
							p.Models = []string{"other-model"}
							cfg.Providers["openai"] = p
						}
						cfg.Auth.Enabled = true
						// Internal keys may select platform credentials without a license
						// service, but deliberately skip org overrides. BYOK keys exercise
						// the actual org selection paths.
						keyType := "byok"
						if owner == "platform" {
							keyType = "internal"
						}
						cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "probe", Key: "sk-agentcc-dx-owner", Owner: "synthetic-user", KeyType: keyType, Metadata: map[string]string{"org_id": "org-probe"}}}
						registry, err := providers.NewRegistry(cfg)
						if err != nil {
							t.Fatal(err)
						}
						defer registry.Close()
						orgs := tenant.NewStore()
						org := &tenant.OrgConfig{}
						if owner != "platform" {
							org.Providers = map[string]*tenant.ProviderConfig{"openai": {Enabled: true, APIKey: wantCredential, APIFormat: "openai"}}
							if owner == "org-model" {
								org.Providers["openai"].Models = []string{model}
							} else {
								// Explicit model_map routing with no provider model list
								// exercises fallback rather than Responses' early match.
								org.ModelMap = map[string]string{model: "openai"}
							}
						}
						orgs.Set("org-probe", org)
						keys := auth.NewKeyStore(cfg.Auth)
						srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), orgs, nil)
						srv.ready.Store(true)
						field := "input"
						if surface == "completions" {
							field = "prompt"
						}
						body := fmt.Sprintf(`{"model":%q,"stream":%v,%q:"hi"}`, model, stream, field)
						w := dxRequest(srv, "POST", "/v1/"+surface, body, map[string]string{"Authorization": "Bearer sk-agentcc-dx-owner"})
						message := fmt.Sprintf(`Provider "openai" rejected the credential this gateway is configured with for it (HTTP %d). This is not a problem with your API key; the gateway's operator must update that provider's key. The provider said: synthetic refusal`, status)
						if owner != "platform" {
							message = fmt.Sprintf(`Your provider "openai" rejected the API key saved for it (HTTP %d). Update that key on the Gateway Providers page; your gateway API key is valid. The provider said: synthetic refusal`, status)
						}
						wantStatus, typ, code := 502, "upstream_error", "provider_auth_failed"
						if status == 404 {
							wantStatus, typ, code = 404, "invalid_request_error", "fixture_code"
							message = `Provider "openai" returned HTTP 404`
							if owner != "platform" {
								message += ` (check that provider's base_url and api_format on the Gateway Providers page)`
							}
							message += `: synthetic refusal`
						}
						want := map[string]any{"error": map[string]any{"message": message, "type": typ, "code": code, "param": nil, "metadata": map[string]any{"provider": "openai", "provider_status": float64(status), "provider_message": "synthetic refusal", "provider_type": "invalid_request_error", "provider_code": "fixture_code"}}}
						if owner == "platform" {
							// Actual registry credentials, not the billing label, require (iii).
							id := w.Header().Get("X-Agentcc-Request-Id")
							if id == "" {
								t.Fatal("missing correlation id")
							}
							if status == 404 {
								typ, code = "upstream_unavailable", "upstream_unavailable"
							}
							want = map[string]any{"error": map[string]any{"message": w4ClosedMessage(status, id), "type": typ, "code": code, "param": nil}}
						}
						var got map[string]any
						_ = json.Unmarshal(w.Body.Bytes(), &got)
						if w.Code != wantStatus || !reflect.DeepEqual(got, want) || w.Header().Get("WWW-Authenticate") != "" || w.Flushed {
							t.Fatalf("HTTP %d body=%s; want %d %#v", w.Code, w.Body.String(), wantStatus, want)
						}
						if calls.Load() != 1 {
							t.Fatalf("upstream calls=%d", calls.Load())
						}
					})
				}
			}
		}
	}
}
