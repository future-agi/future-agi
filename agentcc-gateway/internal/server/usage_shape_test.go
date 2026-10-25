package server

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An OpenRouter-shaped body, as the openai adapter decodes it: usage carries
// the upstream's cost, cost_details and is_byok, and the top level names the
// upstream provider.
const openRouterShapedBody = `{"id":"gen-1","object":"chat.completion","created":1,"model":"anthropic/claude","provider":"Anthropic",
"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],
"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12,"cost":0.0054,"is_byok":true,
"cost_details":{"upstream_inference_cost":0.0054,"upstream_inference_prompt_cost":0.004,"upstream_inference_completions_cost":0.0014}}}`

func decodeOpenRouterShaped(t *testing.T, body string) *models.ChatCompletionResponse {
	t.Helper()
	var resp models.ChatCompletionResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Usage == nil || resp.Usage.CostDetails == nil || resp.Usage.IsByok == nil || resp.UpstreamProvider == "" {
		t.Fatalf("fixture did not decode the upstream charge fields: %+v", resp)
	}
	return &resp
}

func assertNoUpstreamCharge(t *testing.T, what string, v any) {
	t.Helper()
	out, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	for _, leak := range []string{"upstream_inference", "cost_details", "is_byok", "0.0054"} {
		if strings.Contains(string(out), leak) {
			t.Errorf("%s carries the upstream's %q: %s", what, leak, out)
		}
	}
}

// A cache hit serves a stored response: none of the original request's
// upstream charge, nor the upstream that served it, reaches the client, and
// the stored object is not modified.
func TestUsageShapeCacheHitWithholdsUpstreamCharge(t *testing.T) {
	for name, body := range map[string]string{
		"with cost": openRouterShapedBody,
		// The cache-hit branch must not depend on cost being present.
		"without cost": strings.Replace(openRouterShapedBody, `"cost":0.0054,`, "", 1),
	} {
		t.Run(name, func(t *testing.T) {
			stored := decodeOpenRouterShaped(t, body)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Response = stored
			rc.Flags.CacheHit = true

			presentUsageCost(rc)

			assertNoUpstreamCharge(t, "cache-hit body", rc.Response)
			if rc.Response.UpstreamProvider != "" {
				t.Errorf("cache-hit body names the original upstream %q", rc.Response.UpstreamProvider)
			}
			if rc.Response.Usage.TotalTokens != 12 {
				t.Errorf("token counts changed: %+v", rc.Response.Usage)
			}
			if stored.Usage.CostDetails == nil || stored.Usage.IsByok == nil || stored.UpstreamProvider == "" {
				t.Error("presentUsageCost modified the cache's stored response")
			}
		})
	}
}

// A managed response shows the amount billed, never FutureAGI's upstream
// cost_details or is_byok, on the body and on the usage a stream's final or
// finish chunk carries (billedUsage). A stream never forwards the provider's
// own usage (TestChatStreamUsage_ProviderUsageOnAChoiceChunkGoesOutOnce).
func TestUsageShapeManagedWithholdsUpstreamCharge(t *testing.T) {
	resp := decodeOpenRouterShaped(t, openRouterShapedBody)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Response = resp
	rc.Metadata["billing_mode"] = billingManaged
	rc.Metadata["cost"] = "0.00567"

	presentUsageCost(rc)

	if rc.Response.Usage.Cost == nil || *rc.Response.Usage.Cost != 0.00567 {
		t.Fatalf("managed body cost = %v, want the billed 0.00567", rc.Response.Usage.Cost)
	}
	if rc.Response.Usage.CostDetails != nil || rc.Response.Usage.IsByok != nil {
		t.Errorf("managed body carries the upstream's cost_details or is_byok: %+v", rc.Response.Usage)
	}
	assertNoUpstreamCharge(t, "managed evidence usage", billedUsage(rc, resp.Usage))

	// is_byok or cost_details alone, with no cost yet, are withheld too.
	byokOnly := &models.Usage{TotalTokens: 3, IsByok: resp.Usage.IsByok}
	assertNoUpstreamCharge(t, "managed usage with is_byok only", billedUsage(rc, byokOnly))
	if byokOnly.IsByok == nil {
		t.Error("billedUsage modified the usage it was handed")
	}
}

// A BYOK response is the customer's own upstream charge: it passes through.
func TestUsageShapeByokKeepsUpstreamCharge(t *testing.T) {
	resp := decodeOpenRouterShaped(t, openRouterShapedBody)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Response = resp
	rc.Metadata["billing_mode"] = billingBYOK

	presentUsageCost(rc)

	if rc.Response != resp || rc.Response.Usage.CostDetails == nil || rc.Response.Usage.IsByok == nil ||
		rc.Response.UpstreamProvider != "Anthropic" {
		t.Errorf("BYOK response changed: %+v", rc.Response)
	}
	if got := billedUsage(rc, resp.Usage); got != resp.Usage {
		t.Error("BYOK stream usage changed")
	}
}
