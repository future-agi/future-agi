package reqopts

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// unmarkSupported undoes MarkSupported, so a test leaves the registry as it
// found it.
func unmarkSupported(field string) {
	support.mu.Lock()
	defer support.mu.Unlock()
	delete(support.fields, field)
}

func markForTest(t *testing.T, fields ...string) {
	t.Helper()
	for _, f := range fields {
		if Supported(f) {
			continue
		}
		MarkSupported(f)
		t.Cleanup(func() { unmarkSupported(f) })
	}
}

func request(t *testing.T, body string) *models.ChatCompletionRequest {
	t.Helper()
	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(body), &req); err != nil {
		t.Fatalf("decoding %s: %v", body, err)
	}
	return &req
}

func withMessages(fields string) string {
	return `{"model":"m","messages":[{"role":"user","content":"hi"}],` + fields + `}`
}

func fp(v float64) *float64 { return &v }
func bp(v bool) *bool       { return &v }

// TestParseEveryField covers each routing field, valid and invalid.
func TestParseEveryField(t *testing.T) {
	cases := []struct {
		name      string
		fields    string
		wantCode  string
		wantParam string
		check     func(t *testing.T, o models.RoutingOptions)
	}{
		{name: "provider full", fields: `"provider":{"order":["anthropic","deepinfra/fp8"],"only":["Anthropic"],"ignore":["azure"],
			"allow_fallbacks":false,"require_parameters":true,"data_collection":"deny","zdr":true,"quantizations":["FP8","int4"],
			"sort":{"by":"cost","partition":"none"},"max_price":{"prompt":3,"completion":15,"request":0.01,"image":0.02},
			"preferred_max_latency":{"p50":1.5,"p90":3},"preferred_min_throughput":40}`,
			check: func(t *testing.T, o models.RoutingOptions) {
				p := o.Provider
				want := &models.ProviderPrefs{
					Order: []string{"anthropic", "deepinfra/fp8"}, Only: []string{"Anthropic"}, Ignore: []string{"azure"},
					AllowFallbacks: bp(false), RequireParameters: bp(true), DataCollection: "deny", ZDR: bp(true),
					Quantizations: []string{"fp8", "int4"}, Sort: &models.ProviderSort{By: "price", Partition: "none"},
					MaxPrice:               &models.ProviderMaxPrice{Prompt: fp(3), Completion: fp(15), Request: fp(0.01), Image: fp(0.02)},
					PreferredMaxLatency:    &models.PercentileBound{P50: fp(1.5), P90: fp(3)},
					PreferredMinThroughput: &models.PercentileBound{Value: fp(40)},
				}
				if !reflect.DeepEqual(p, want) {
					t.Errorf("provider = %+v\nwant %+v", p, want)
				}
			}},
		{name: "provider sort bare alias", fields: `"provider":{"sort":"tps"}`, check: func(t *testing.T, o models.RoutingOptions) {
			if o.Provider.Sort.By != "throughput" {
				t.Errorf("sort = %q, want throughput", o.Provider.Sort.By)
			}
		}},
		{name: "provider not an object", fields: `"provider":"anthropic"`, wantCode: CodeInvalidProviderPreference, wantParam: "provider"},
		{name: "provider unknown key", fields: `"provider":{"order":["a"],"fastest":true}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.fastest"},
		{name: "provider data_collection", fields: `"provider":{"data_collection":"maybe"}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.data_collection"},
		{name: "provider quantization", fields: `"provider":{"quantizations":["fp3"]}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.quantizations"},
		{name: "provider sort value", fields: `"provider":{"sort":"fastest"}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.sort"},
		{name: "provider sort partition", fields: `"provider":{"sort":{"by":"price","partition":"region"}}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.sort.partition"},
		{name: "provider sort object unknown key", fields: `"provider":{"sort":{"by":"price","dir":"asc"}}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider"},
		{name: "provider max_price negative", fields: `"provider":{"max_price":{"completion":-1}}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.max_price.completion"},
		{name: "provider max_price unknown unit", fields: `"provider":{"max_price":{"tokens":1}}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider"},
		{name: "provider latency negative", fields: `"provider":{"preferred_max_latency":-2}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.preferred_max_latency"},
		{name: "provider latency empty object", fields: `"provider":{"preferred_max_latency":{}}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.preferred_max_latency"},
		{name: "provider empty slug", fields: `"provider":{"only":["openai",""]}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.only"},
		{name: "provider order not a list", fields: `"provider":{"order":"openai"}`, wantCode: CodeInvalidProviderPreference, wantParam: "provider.order"},
		{name: "gateway zdr not a bool", fields: `"providerOptions":{"gateway":{"zeroDataRetention":"yes"}}`, wantCode: CodeInvalidProviderPreference, wantParam: "providerOptions.gateway.zeroDataRetention"},

		{name: "gateway options", fields: `"providerOptions":{"gateway":{"order":["bedrock"],"only":["anthropic"],"sort":"ttft","models":["a","b","a"],
			"tags":["t1"],"user":"u-1","sessionId":"s-1","zeroDataRetention":true},"openai":{"store":true}}`,
			check: func(t *testing.T, o models.RoutingOptions) {
				want := &models.GatewayOptions{Order: []string{"bedrock"}, Only: []string{"anthropic"}, Sort: "latency",
					Models: []string{"a", "b"}, Tags: []string{"t1"}, User: "u-1", SessionID: "s-1", ZeroDataRetention: bp(true)}
				if !reflect.DeepEqual(o.Gateway, want) {
					t.Errorf("gateway = %+v\nwant %+v", o.Gateway, want)
				}
			}},
		{name: "gateway sort cost", fields: `"providerOptions":{"gateway":{"sort":"cost"}}`, check: func(t *testing.T, o models.RoutingOptions) {
			if o.Gateway.Sort != "price" {
				t.Errorf("sort = %q, want price", o.Gateway.Sort)
			}
		}},
		{name: "provider-specific options only", fields: `"providerOptions":{"anthropic":{"thinking":{}}}`, check: func(t *testing.T, o models.RoutingOptions) {
			if o.Gateway != nil {
				t.Errorf("gateway = %+v, want none", o.Gateway)
			}
		}},
		{name: "providerOptions not an object", fields: `"providerOptions":[1]`, wantCode: CodeInvalidProviderPreference, wantParam: "providerOptions"},
		{name: "gateway unknown key", fields: `"providerOptions":{"gateway":{"byok":{}}}`, wantCode: CodeInvalidProviderPreference, wantParam: "providerOptions.gateway.byok"},
		{name: "gateway sort value", fields: `"providerOptions":{"gateway":{"sort":"fast"}}`, wantCode: CodeInvalidProviderPreference, wantParam: "providerOptions.gateway.sort"},
		{name: "gateway too many models", fields: `"providerOptions":{"gateway":{"models":["a","b","c","d","e","f"]}}`, wantCode: CodeInvalidRoutingField, wantParam: "providerOptions.gateway.models"},

		{name: "models deduplicated", fields: `"models":["a","b","a","c"]`, check: func(t *testing.T, o models.RoutingOptions) {
			if !reflect.DeepEqual(o.Models, []string{"a", "b", "c"}) {
				t.Errorf("models = %v", o.Models)
			}
		}},
		{name: "five models after dedupe", fields: `"models":["a","b","c","d","e","a"]`, check: func(t *testing.T, o models.RoutingOptions) {
			if len(o.Models) != 5 {
				t.Errorf("models = %v", o.Models)
			}
		}},
		{name: "six models", fields: `"models":["a","b","c","d","e","f"]`, wantCode: CodeInvalidRoutingField, wantParam: "models"},
		{name: "models not a list", fields: `"models":"a"`, wantCode: CodeInvalidRoutingField, wantParam: "models"},
		{name: "models empty id", fields: `"models":["a"," "]`, wantCode: CodeInvalidRoutingField, wantParam: "models"},
		{name: "fallbacks", fields: `"fallbacks":[{"model":"x"},{"model":"y"}]`, check: func(t *testing.T, o models.RoutingOptions) {
			if !reflect.DeepEqual(o.Models, []string{"x", "y"}) {
				t.Errorf("models = %v", o.Models)
			}
		}},
		{name: "four fallbacks", fields: `"fallbacks":[{"model":"a"},{"model":"b"},{"model":"c"},{"model":"d"}]`, wantCode: CodeInvalidRoutingField, wantParam: "fallbacks"},
		{name: "fallback unknown key", fields: `"fallbacks":[{"model":"a","weight":2}]`, wantCode: CodeInvalidRoutingField, wantParam: "fallbacks"},
		{name: "models and fallbacks", fields: `"models":["a"],"fallbacks":[{"model":"b"}]`, wantCode: CodeInvalidRoutingField, wantParam: "fallbacks"},

		{name: "route", fields: `"route":"fallback"`, check: func(t *testing.T, o models.RoutingOptions) {
			if o.Route != "fallback" {
				t.Errorf("route = %q", o.Route)
			}
		}},
		{name: "route not a string", fields: `"route":1`, wantCode: CodeInvalidRoutingField, wantParam: "route"},
		{name: "plugins", fields: `"plugins":[{"id":"web"}]`, check: func(t *testing.T, o models.RoutingOptions) {
			if string(o.Plugins) != `[{"id":"web"}]` {
				t.Errorf("plugins = %s", o.Plugins)
			}
		}},
		{name: "plugins not a list", fields: `"plugins":{"id":"web"}`, wantCode: CodeInvalidRoutingField, wantParam: "plugins"},
		{name: "transforms", fields: `"transforms":["middle-out"]`, check: func(t *testing.T, o models.RoutingOptions) {
			if string(o.Transforms) != `["middle-out"]` {
				t.Errorf("transforms = %s", o.Transforms)
			}
		}},
		{name: "transforms not strings", fields: `"transforms":[1]`, wantCode: CodeInvalidRoutingField, wantParam: "transforms"},
		{name: "preset", fields: `"preset":"cheap"`, check: func(t *testing.T, o models.RoutingOptions) {
			if o.Preset != "cheap" {
				t.Errorf("preset = %q", o.Preset)
			}
		}},
		{name: "preset not a string", fields: `"preset":{"name":"x"}`, wantCode: CodeInvalidRoutingField, wantParam: "preset"},
		{name: "session_id kept", fields: `"session_id":"sess-9"`, check: func(t *testing.T, o models.RoutingOptions) {
			if o.SessionID != "sess-9" {
				t.Errorf("session_id = %q", o.SessionID)
			}
		}},
		{name: "session_id not a string", fields: `"session_id":9`, wantCode: CodeInvalidRoutingField, wantParam: "session_id"},
		{name: "caching", fields: `"caching":"auto"`, check: func(t *testing.T, o models.RoutingOptions) {
			if string(o.Caching) != `"auto"` {
				t.Errorf("caching = %s", o.Caching)
			}
		}},
		{name: "nulls are absent", fields: `"provider":null,"models":null,"plugins":null,"preset":null`, check: func(t *testing.T, o models.RoutingOptions) {
			if o.Provider != nil || o.Models != nil || o.Plugins != nil || o.Preset != "" {
				t.Errorf("options = %+v, want none", o)
			}
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := request(t, withMessages(tc.fields))
			res, apiErr := Parse(req)
			if tc.wantCode != "" {
				if apiErr == nil {
					t.Fatalf("Parse accepted %s", tc.fields)
				}
				if apiErr.Status != 400 || apiErr.Code != tc.wantCode || apiErr.Param == nil || *apiErr.Param != tc.wantParam {
					t.Fatalf("error = %d %s param %v (%s), want 400 %s param %s", apiErr.Status, apiErr.Code, deref(apiErr.Param), apiErr.Message, tc.wantCode, tc.wantParam)
				}
				return
			}
			if apiErr != nil {
				t.Fatalf("Parse refused %s: %s %s", tc.fields, apiErr.Code, apiErr.Message)
			}
			for _, k := range routingKeys {
				if _, left := req.Extra[k]; left {
					t.Errorf("%s left in the request body", k)
				}
			}
			tc.check(t, res.Options)
		})
	}
}

func deref(p *string) string {
	if p == nil {
		return "<nil>"
	}
	return *p
}

func TestSortAliases(t *testing.T) {
	for in, want := range map[string]string{
		"price": "price", "cost": "price", "throughput": "throughput", "tps": "throughput",
		"latency": "latency", "ttft": "latency", " TTFT ": "latency",
	} {
		if got, ok := CanonicalSort(in); !ok || got != want {
			t.Errorf("CanonicalSort(%q) = %q, %v; want %q", in, got, ok, want)
		}
	}
	if _, ok := CanonicalSort("fastest"); ok {
		t.Error("CanonicalSort accepted fastest")
	}
}

// TestMaxPriceUnits: max_price keeps OpenRouter's units (USD per million
// tokens for prompt and completion, USD per unit for request and image) on
// the way in and back out, and PerTokenMaxPrice converts to PriceCard units.
func TestMaxPriceUnits(t *testing.T) {
	req := request(t, withMessages(`"provider":{"max_price":{"prompt":3,"completion":15,"request":0.01,"image":0.5}}`))
	res, apiErr := Parse(req)
	if apiErr != nil {
		t.Fatal(apiErr.Message)
	}
	prompt, completion, perRequest, image := PerTokenMaxPrice(res.Options.Provider.MaxPrice)
	if *prompt != 3e-6 || *completion != 15e-6 || *perRequest != 0.01 || *image != 0.5 {
		t.Errorf("per token = %v %v %v %v, want 3e-6 15e-6 0.01 0.5", *prompt, *completion, *perRequest, *image)
	}
	out, _ := json.Marshal(ProviderObject(res.Options.Provider))
	if !strings.Contains(string(out), `"max_price":{"prompt":3,"completion":15,"request":0.01,"image":0.5}`) {
		t.Errorf("re-emitted %s, want max_price in OpenRouter units", out)
	}
	if p, c, r, i := PerTokenMaxPrice(nil); p != nil || c != nil || r != nil || i != nil {
		t.Error("PerTokenMaxPrice(nil) must mean no cap")
	}
}

func TestMoreThanFiveModels400(t *testing.T) {
	_, apiErr := Parse(request(t, withMessages(`"models":["a","b","c","d","e","f"]`)))
	if apiErr == nil || apiErr.Status != 400 || apiErr.Code != CodeInvalidRoutingField {
		t.Fatalf("six models: %+v, want 400 %s", apiErr, CodeInvalidRoutingField)
	}
}

// TestSupportRegistryGate: an interpreted field is refused until it is
// registered with MarkSupported, and the refusal names the first
// unregistered field.
func TestSupportRegistryGate(t *testing.T) {
	req := request(t, withMessages(`"provider":{"only":["anthropic"],"max_price":{"prompt":1}},"models":["a"],"preset":"p","caching":"auto",
		"plugins":[{"id":"web"}],"route":"fallback","transforms":["middle-out"],"session_id":"s"`))
	res, apiErr := Parse(req)
	if apiErr != nil {
		t.Fatal(apiErr.Message)
	}
	want := []string{FieldProviderOnly, FieldProviderMaxPrice, FieldModels, FieldPreset, FieldCaching}
	if !reflect.DeepEqual(res.Interpreted, want) {
		t.Fatalf("interpreted = %v, want %v (plugins, route, transforms and session_id are not gated)", res.Interpreted, want)
	}
	for i, f := range want {
		if Supported(f) {
			t.Fatalf("%s is registered before the test marks it", f)
		}
		apiErr := CheckSupported(res.Interpreted)
		if apiErr == nil || apiErr.Code != CodeNotYetSupported || *apiErr.Param != f {
			t.Fatalf("step %d: %+v, want %s naming %s", i, apiErr, CodeNotYetSupported, f)
		}
		markForTest(t, f)
	}
	if apiErr := CheckSupported(res.Interpreted); apiErr != nil {
		t.Fatalf("every field registered, still refused: %s", apiErr.Message)
	}
	if CheckSupported(nil) != nil {
		t.Fatal("a request with no interpreted field was refused")
	}
}

func TestFallbacksGatedAsFallbacks(t *testing.T) {
	res, apiErr := Parse(request(t, withMessages(`"fallbacks":[{"model":"b"}]`)))
	if apiErr != nil || !reflect.DeepEqual(res.Interpreted, []string{FieldFallbacks}) {
		t.Fatalf("interpreted = %v (%v), want [fallbacks]", res.Interpreted, apiErr)
	}
}

func TestHasRoutingFieldsNoneIsFree(t *testing.T) {
	req := request(t, withMessages(`"reasoning_effort":"low"`))
	allocs := testing.AllocsPerRun(100, func() { _ = HasRoutingFields(req.Extra) })
	if HasRoutingFields(req.Extra) || allocs != 0 {
		t.Fatalf("HasRoutingFields = %v with %v allocs, want false and 0", HasRoutingFields(req.Extra), allocs)
	}
	if !HasRoutingFields(request(t, withMessages(`"extra_body":{}`)).Extra) {
		t.Fatal("a literal extra_body must count")
	}
}
