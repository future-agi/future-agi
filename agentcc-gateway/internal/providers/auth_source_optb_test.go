package providers

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const optbNode = "optb-node-auth-fixture"
const optbTenant = "optb-tenant-auth-fixture"

func optbHeaders() map[string]string {
	return map[string]string{"Authorization": optbNode, "X-Api-Key": optbNode,
		"api-key": optbNode, "x-goog-api-key": optbNode, "Proxy-Authorization": optbNode,
		"Cookie": optbNode, "X-Custom-Credential": optbNode,
		"anthropic-version": "2023-06-01", "anthropic-beta": "fixture-beta", "User-Agent": "fixture-client"}
}

func TestOptBAuthSource(t *testing.T) {
	for _, key := range []string{"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"} {
		t.Setenv(key, "")
	}
	for _, tc := range []struct{ format, header, value, response string }{
		{"anthropic", "X-Api-Key", optbTenant, `{"id":"fixture","type":"message","role":"assistant","model":"fixture","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`},
		{"openai", "Authorization", "Bearer " + optbTenant, `{"id":"fixture","object":"chat.completion","model":"fixture","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`},
		{"azure", "Api-Key", optbTenant, `{"id":"fixture","object":"chat.completion","model":"fixture","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`},
		{"gemini", "X-Goog-Api-Key", optbTenant, `{"candidates":[{"content":{"role":"model","parts":[{"text":"ok"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":1,"totalTokenCount":2}}`},
		{"cohere", "Authorization", "Bearer " + optbTenant, `{"id":"fixture","message":{"role":"assistant","content":[{"type":"text","text":"ok"}]},"finish_reason":"COMPLETE","usage":{"tokens":{"input_tokens":1,"output_tokens":1}}}`},
	} {
		t.Run(tc.format, func(t *testing.T) {
			for _, withConfig := range []bool{false, true} {
				name := "key_only"
				var tenantCfg *tenant.ProviderConfig
				if withConfig {
					name = "tenant_config"
					tenantCfg = &tenant.ProviderConfig{APIKey: optbTenant}
				}
				t.Run(name, func(t *testing.T) {
					wire := make(chan http.Header, 1)
					upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						_, _ = io.Copy(io.Discard, r.Body)
						wire <- r.Header.Clone()
						w.Header().Set("Content-Type", "application/json")
						_, _ = io.WriteString(w, tc.response)
					}))
					defer upstream.Close()
					base := config.ProviderConfig{APIFormat: tc.format, BaseURL: upstream.URL, APIKey: "node-key-fixture", Headers: optbHeaders()}
					cache := NewOrgProviderCache(map[string]config.ProviderConfig{tc.format: base}, false)
					defer cache.EvictAll()
					p, err := cache.GetOrCreateWithTenantConfig("fixture-org", tc.format, optbTenant, tenantCfg)
					if err != nil {
						t.Fatal(err)
					}
					ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
					defer cancel()
					resp, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"fixture"`)}}})
					if err != nil || resp == nil {
						t.Fatalf("loopback call failed: %v", err)
					}
					h := <-wire
					if got := h.Values(tc.header); len(got) != 1 || got[0] != tc.value {
						t.Error("AUTHSRC: upstream authentication was not solely the tenant key")
					}
					for _, values := range h {
						for _, v := range values {
							if strings.Contains(v, optbNode) {
								t.Error("AUTHSRC: inherited node auth header reached upstream")
							}
						}
					}
					if !reflect.DeepEqual(base.Headers, optbHeaders()) {
						t.Error("operator headers mutated")
					}
				})
			}
		})
	}
}

func TestOptBReviewedHeadersAndBYOKConfigParity(t *testing.T) {
	base := config.ProviderConfig{BaseURL: "https://fixture.invalid", APIFormat: "openai", APIKey: "node-fixture", Headers: optbHeaders(), DefaultTimeout: time.Second, MaxConcurrent: 7}
	got := resolveOrgConfig(base, optbTenant, nil)
	want := base
	want.APIKey = optbTenant
	want.Headers = map[string]string{"anthropic-version": "2023-06-01", "anthropic-beta": "fixture-beta", "User-Agent": "fixture-client"}
	if !reflect.DeepEqual(got, want) {
		t.Error("only reviewed non-credential headers may survive; all other BYOK settings must match deployed")
	}
	got.Headers["User-Agent"] = "changed"
	if base.Headers["User-Agent"] != "fixture-client" {
		t.Error("reviewed headers alias the operator map")
	}
}
