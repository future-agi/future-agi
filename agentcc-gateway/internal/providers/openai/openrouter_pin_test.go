package openai

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// endpointSet builds an endpoint set from catalog ids
// "<source>:<upstream>[/<tag>]:<model>".
func endpointSet(ids ...string) []models.EndpointRef {
	var set []models.EndpointRef
	for _, id := range ids {
		parts := strings.SplitN(id, ":", 3)
		upstream, _, _ := strings.Cut(parts[1], "/")
		set = append(set, models.EndpointRef{ID: id, UpstreamProvider: upstream})
	}
	return set
}

func decodeObject(t *testing.T, raw json.RawMessage) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("decoding %s: %v", raw, err)
	}
	return m
}

// TestManagedOpenRouterEndpointSet: the managed provider object pins order
// and only to the same ranked set of at most 5 upstream slugs, turns
// OpenRouter's own fallbacks off, asks for required parameters when the
// request has tools, denies data collection unless the policy allows it,
// and the request always asks for usage.
func TestManagedOpenRouterEndpointSet(t *testing.T) {
	set := endpointSet(
		"openrouter:anthropic:anthropic/claude-sonnet-5.5",
		"openrouter:amazon-bedrock:anthropic/claude-sonnet-5.5",
		"openrouter:google-vertex/global:anthropic/claude-sonnet-5.5",
		"openrouter:anthropic:anthropic/claude-sonnet-5.5", // a duplicate slug
		"openrouter:deepinfra/fp8:anthropic/claude-sonnet-5.5",
		"openrouter:together:anthropic/claude-sonnet-5.5",
		"openrouter:fireworks:anthropic/claude-sonnet-5.5", // the sixth distinct slug
	)
	req := &models.ChatCompletionRequest{Model: "anthropic/claude-sonnet-5.5", Tools: []models.Tool{{Type: "function"}}}
	raw, err := PinManaged(req, set, models.EffectiveDataPolicy{ZDR: true})
	if err != nil {
		t.Fatal(err)
	}
	obj := decodeObject(t, raw)
	wantSet := []any{"anthropic", "amazon-bedrock", "google-vertex/global", "deepinfra/fp8", "together"}
	if !reflect.DeepEqual(obj["order"], wantSet) || !reflect.DeepEqual(obj["only"], wantSet) {
		t.Errorf("order %v only %v, want both %v", obj["order"], obj["only"], wantSet)
	}
	if obj["allow_fallbacks"] != false || obj["require_parameters"] != true || obj["data_collection"] != "deny" || obj["zdr"] != true {
		t.Errorf("provider object = %s", raw)
	}
	if string(req.Extra["provider"]) != string(raw) || string(req.Extra["usage"]) != `{"include":true}` {
		t.Errorf("request extras = provider %s usage %s", req.Extra["provider"], req.Extra["usage"])
	}

	// No tools or response format: require_parameters is not sent; an
	// allowing policy is honoured; an empty set pins nothing but still turns
	// fallbacks off.
	plain := &models.ChatCompletionRequest{Model: "m"}
	raw, _ = PinManaged(plain, nil, models.EffectiveDataPolicy{DataCollection: "allow"})
	obj = decodeObject(t, raw)
	if _, ok := obj["require_parameters"]; ok || obj["data_collection"] != "allow" || obj["allow_fallbacks"] != false || obj["zdr"] != false {
		t.Errorf("plain provider object = %s", raw)
	}
	if _, ok := obj["order"]; ok {
		t.Errorf("an empty endpoint set must not send order: %s", raw)
	}
	withFormat := &models.ChatCompletionRequest{Model: "m", ResponseFormat: &models.ResponseFormat{Type: "json_object"}}
	raw, _ = PinManaged(withFormat, nil, models.EffectiveDataPolicy{})
	if decodeObject(t, raw)["require_parameters"] != true {
		t.Errorf("a response_format must require parameters: %s", raw)
	}
}

// TestSlugMatchingNotGatewayIDs: the pinned set names upstream slugs from
// the endpoint ids, never the gateway provider that routes to OpenRouter.
func TestSlugMatchingNotGatewayIDs(t *testing.T) {
	for _, c := range []struct {
		ref  models.EndpointRef
		want string
	}{
		{models.EndpointRef{ID: "openrouter:deepinfra/turbo:meta/llama", UpstreamProvider: "deepinfra"}, "deepinfra/turbo"},
		{models.EndpointRef{ID: "openrouter:anthropic:anthropic/claude", UpstreamProvider: "anthropic"}, "anthropic"},
		{models.EndpointRef{ID: "managed-or-1", UpstreamProvider: "amazon-bedrock"}, "amazon-bedrock"},
		{models.EndpointRef{ID: "openrouter:other:m", UpstreamProvider: "google-vertex"}, "google-vertex"},
	} {
		if got := endpointSlug(c.ref); got != c.want {
			t.Errorf("endpointSlug(%+v) = %q, want %q", c.ref, got, c.want)
		}
	}
}

// TestByokOpenRouterPassthrough: a BYOK OpenRouter upstream gets the
// caller's validated provider object (in OpenRouter's units and canonical
// sort), plugins, transforms and route; ClearRouting takes them back out.
func TestByokOpenRouterPassthrough(t *testing.T) {
	three := 3.0
	no := false
	opts := &models.RoutingOptions{
		Provider: &models.ProviderPrefs{Only: []string{"anthropic"}, AllowFallbacks: &no,
			Sort: &models.ProviderSort{By: "price"}, MaxPrice: &models.ProviderMaxPrice{Prompt: &three}},
		Plugins:    json.RawMessage(`[{"id":"web"}]`),
		Transforms: json.RawMessage(`["middle-out"]`),
		Route:      "fallback",
		Models:     []string{"never-forwarded"},
	}
	req := &models.ChatCompletionRequest{Model: "m"}
	raw, err := EmitBYOK(req, opts)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != `{"only":["anthropic"],"allow_fallbacks":false,"sort":"price","max_price":{"prompt":3}}` {
		t.Errorf("provider object = %s", raw)
	}
	body, _ := json.Marshal(req)
	got := decodeObject(t, body)
	if got["plugins"] == nil || got["transforms"] == nil || got["route"] != "fallback" || got["models"] != nil {
		t.Errorf("outbound body = %s", body)
	}
	ClearRouting(req, false)
	if len(req.Extra) != 0 {
		t.Errorf("ClearRouting left %v", req.Extra)
	}
	callerUsage := &models.ChatCompletionRequest{Extra: map[string]json.RawMessage{"usage": json.RawMessage(`{"include":true}`)}}
	ClearRouting(callerUsage, false)
	if callerUsage.Extra["usage"] == nil {
		t.Error("ClearRouting removed a usage field the gateway did not pin")
	}
	ClearRouting(callerUsage, true)
	if callerUsage.Extra["usage"] != nil {
		t.Error("ClearRouting kept the gateway's pinned usage")
	}
}

func TestIsOpenRouter(t *testing.T) {
	for url, want := range map[string]bool{
		"https://openrouter.ai/api":     true,
		"https://eu.openrouter.ai/api":  true,
		"https://openrouter.ai.evil.io": false,
		"http://127.0.0.1:9000/api":     false,
		"https://api.openai.com":        false,
	} {
		p, err := New("p", config.ProviderConfig{BaseURL: url, APIFormat: "openai"})
		if err != nil {
			t.Fatal(err)
		}
		if got := p.IsOpenRouter(); got != want {
			t.Errorf("IsOpenRouter(%s) = %v, want %v", url, got, want)
		}
	}
}
