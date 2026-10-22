package eligibility

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var testNow = time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC)

func ptr[T any](v T) *T { return &v }

// ep is an OpenRouter endpoint of model m on provider "openrouter".
func ep(tag string, prompt float64, mods ...func(*catalog.Endpoint)) catalog.Endpoint {
	e := catalog.Endpoint{
		ID: catalog.EndpointID(catalog.SourceOpenRouter, tag, "m"), Provider: "openrouter",
		Source: catalog.SourceOpenRouter, UpstreamProvider: strings.SplitN(tag, "/", 2)[0], Tag: tag,
		UpstreamFeeBps: 550, Prices: catalog.Prices{Prompt: prompt, Completion: 2 * prompt},
		DataCollection: catalog.DataCollectionUnknown, ZDR: ptr(false), Regions: catalog.RegionsFromTag(tag),
		SupportedParams: []string{"tools", "max_tokens"},
	}
	for _, f := range mods {
		f(&e)
	}
	return e
}

func zdr(e *catalog.Endpoint)      { e.ZDR = ptr(true); e.DataCollection = catalog.DataCollectionDeny }
func collects(e *catalog.Endpoint) { e.DataCollection = catalog.DataCollectionAllow }
func priceCard(e *catalog.Endpoint) {
	e.Source, e.UpstreamFeeBps = catalog.SourcePriceCard, 0
	e.ID = catalog.EndpointID(catalog.SourcePriceCard, e.Tag, "m")
}
func quant(q string) func(*catalog.Endpoint) { return func(e *catalog.Endpoint) { e.Quantization = q } }

// entry is catalog model m with endpoints.
func entry(eps ...catalog.Endpoint) *catalog.Model {
	return &catalog.Model{ID: "m", Prices: catalog.Prices{Prompt: 1e-6, Completion: 2e-6}, SupportedParameters: []string{"tools"},
		State: catalog.StateActive, Endpoints: eps}
}

// managedIn is a managed OpenRouter candidate "m" for an opted-in org.
func managedIn(org *tenant.OrgConfig, e *catalog.Model) Input {
	if org == nil {
		org = &tenant.OrgConfig{}
	}
	if org.ManagedModels == nil {
		org.ManagedModels = &tenant.ManagedModelsConfig{Enabled: true}
	}
	return Input{Model: "m", ProviderID: "openrouter", Managed: true, Aggregator: true, Org: org, Entry: e, Now: testNow}
}

// byokIn is the org's own key on provider "openai" for model "gpt-4o".
func byokIn(org *tenant.OrgConfig) Input {
	return Input{Model: "gpt-4o", ProviderID: "openai", Org: org, Now: testNow}
}

func withPolicy(dp tenant.DataPolicyConfig) *tenant.OrgConfig {
	return &tenant.OrgConfig{Routing: &tenant.RoutingConfig{DataPolicy: &dp}}
}

func withManaged(m tenant.ManagedModelsConfig) *tenant.OrgConfig {
	return &tenant.OrgConfig{ManagedModels: &m}
}

type groups map[string][]string

func (g groups) Check(model string, keyGroups []string) (string, bool) {
	for _, k := range keyGroups {
		for _, p := range g[k] {
			if globMatch(p, model) {
				return k, true
			}
		}
	}
	return "", false
}

func toolTranscript(id string) *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{Messages: []models.Message{
		{Role: "user", Content: json.RawMessage(`"weather?"`)},
		{Role: "assistant", ToolCalls: []models.ToolCall{{ID: id, Type: "function", Function: models.FunctionCall{Name: "w", Arguments: "{}"}}}},
		{Role: "tool", ToolCallID: id, Content: json.RawMessage(`"sunny"`)},
	}}
}

type checkCase struct {
	name     string
	in       Input
	wantCode string // "" means eligible
	wantEP   string // the best endpoint's tag, when eligible
	wantEff  *models.EffectiveDataPolicy
	wantDep  string
}

func checkCases() []checkCase {
	pay := tenant.ManagedModelsConfig{Enabled: true}
	cases := []checkCase{
		// Rule 1: the key's ACL on the resolved model.
		{name: "key allowed_models excludes the model", in: func() Input {
			in := byokIn(nil)
			in.Key.AllowedModels = "gpt-4o-mini, o3"
			return in
		}(), wantCode: CodeKeyModelDenied},
		{name: "key allowed_models includes the model", in: func() Input {
			in := byokIn(nil)
			in.Key.AllowedModels = "gpt-4o-mini,gpt-4o"
			return in
		}()},
		{name: "key allowed_providers excludes the provider", in: func() Input {
			in := byokIn(nil)
			in.Key.AllowedProviders = "anthropic"
			return in
		}(), wantCode: CodeKeyProviderDenied},
		{name: "internal key is exempt from allowed_providers", in: func() Input {
			in := byokIn(nil)
			in.Key.AllowedProviders, in.Key.Internal = "anthropic", true
			return in
		}()},
		{name: "access groups refuse the model", in: func() Input {
			in := byokIn(nil)
			in.Key.Groups, in.Key.GroupCheck = []string{"basic"}, groups{"basic": {"gpt-4o-mini"}}
			return in
		}(), wantCode: CodeKeyModelDenied},
		{name: "access group glob allows the model", in: func() Input {
			in := byokIn(nil)
			in.Key.Groups, in.Key.GroupCheck = []string{"all"}, groups{"all": {"gpt-*"}}
			return in
		}()},

		// Rule 2: the org's provider lock.
		{name: "provider lock deny glob", in: byokIn(&tenant.OrgConfig{Routing: &tenant.RoutingConfig{
			ProviderLock: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"open*"}}}}), wantCode: CodeOrgProviderDenied},
		{name: "provider lock allow list leaves the provider out", in: byokIn(&tenant.OrgConfig{Routing: &tenant.RoutingConfig{
			ProviderLock: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"anthropic"}}}}), wantCode: CodeOrgProviderDenied},
		{name: "provider lock deny wins over allow", in: byokIn(&tenant.OrgConfig{Routing: &tenant.RoutingConfig{
			ProviderLock: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"openai"}, DenyProviders: []string{"openai"}}}}), wantCode: CodeOrgProviderDenied},
		{name: "disabled provider lock does nothing", in: byokIn(&tenant.OrgConfig{Routing: &tenant.RoutingConfig{
			ProviderLock: &tenant.ProviderLockConfig{Enabled: false, DenyProviders: []string{"openai"}}}})},

		// Rule 3: managed opt-in and allow/deny.
		{name: "managed candidate for an org that has not opted in", in: func() Input {
			in := managedIn(nil, nil)
			in.Org.ManagedModels.Enabled = false
			return in
		}(), wantCode: CodeManagedNotEnabled},
		{name: "managed candidate with no org", in: Input{Model: "m", ProviderID: "openrouter", Managed: true, Now: testNow}, wantCode: CodeManagedNotEnabled},
		{name: "managed deny_vendors", in: func() Input {
			in := managedIn(withManaged(tenant.ManagedModelsConfig{Enabled: true, DenyVendors: []string{"x-ai"}}), nil)
			in.Model = "x-ai/grok-5"
			return in
		}(), wantCode: CodeManagedModelNotAllowed},
		{name: "managed deny_models glob wins over allow_vendors", in: func() Input {
			in := managedIn(withManaged(tenant.ManagedModelsConfig{Enabled: true, AllowVendors: []string{"anthropic"}, DenyModels: []string{"anthropic/claude-opus*"}}), nil)
			in.Model = "anthropic/claude-opus-5"
			return in
		}(), wantCode: CodeManagedModelNotAllowed},
		{name: "managed allow_models leaves the model out", in: func() Input {
			in := managedIn(withManaged(tenant.ManagedModelsConfig{Enabled: true, AllowModels: []string{"openai/*"}}), nil)
			in.Model = "anthropic/claude-sonnet-5.5"
			return in
		}(), wantCode: CodeManagedModelNotAllowed},
		{name: "managed allow_vendors includes the vendor", in: func() Input {
			in := managedIn(withManaged(tenant.ManagedModelsConfig{Enabled: true, AllowVendors: []string{"Anthropic"}}), nil)
			in.Model = "anthropic/claude-sonnet-5.5"
			return in
		}()},
		{name: "a BYOK candidate ignores the managed lists", in: byokIn(withManaged(tenant.ManagedModelsConfig{DenyModels: []string{"*"}}))},

		// Rule 4: catalog state.
		{name: "blocked model", in: func() Input {
			in := byokIn(nil)
			in.Entry = &catalog.Model{ID: "gpt-4o", State: catalog.StateBlocked, StateReason: "catalog.blocked_models"}
			return in
		}(), wantCode: CodeModelBlocked},
		{name: "expired model", in: func() Input {
			in := byokIn(nil)
			in.Entry = &catalog.Model{ID: "gpt-4o", State: catalog.StateDeprecated, ExpirationDate: "2026-10-01"}
			return in
		}(), wantCode: CodeModelExpired},
		{name: "deprecated model, explicit route", in: func() Input {
			in := byokIn(nil)
			in.Entry = &catalog.Model{ID: "gpt-4o", State: catalog.StateDeprecated, ExpirationDate: "2026-10-20"}
			return in
		}(), wantDep: "2026-10-20"},
		{name: "deprecated model, auto candidate", in: func() Input {
			in := byokIn(nil)
			in.Auto = true
			in.Entry = &catalog.Model{ID: "gpt-4o", State: catalog.StateDeprecated, ExpirationDate: "2026-10-20"}
			return in
		}(), wantCode: CodeModelDeprecated},
		{name: "degraded model, auto candidate", in: func() Input {
			in := byokIn(nil)
			in.Auto = true
			in.Entry = &catalog.Model{ID: "gpt-4o", State: catalog.StateDegraded, StateReason: "error rate"}
			return in
		}(), wantCode: CodeModelDegraded},
		{name: "degraded model, explicit route", in: func() Input {
			in := byokIn(nil)
			in.Entry = &catalog.Model{ID: "gpt-4o", State: catalog.StateDegraded}
			return in
		}()},

		// Rule 5: the effective data policy.
		{name: "org ZDR with no ZDR endpoint", in: managedIn(withPolicy(tenant.DataPolicyConfig{ZDR: true}), entry(ep("deepinfra", 1e-6), ep("together", 1e-6))),
			wantCode: CodeDataPolicyZDR},
		{name: "org ZDR keeps only the ZDR endpoint", in: managedIn(withPolicy(tenant.DataPolicyConfig{ZDR: true}), entry(ep("deepinfra", 1e-7), ep("anthropic", 1e-6, zdr))),
			wantEP: "anthropic", wantEff: &models.EffectiveDataPolicy{ZDR: true, DataCollection: "deny"}},
		{name: "org ZDR fails closed on an unknown ZDR flag", in: managedIn(withPolicy(tenant.DataPolicyConfig{ZDR: true}), entry(ep("anthropic", 1e-6, func(e *catalog.Endpoint) { e.ZDR = nil }))),
			wantCode: CodeDataPolicyZDR},
		{name: "org ZDR fails closed with no endpoint data", in: managedIn(withPolicy(tenant.DataPolicyConfig{ZDR: true}), entry()),
			wantCode: CodeDataPolicyZDR},
		{name: "org ZDR on an attested BYOK provider", in: func() Input {
			in := byokIn(withPolicy(tenant.DataPolicyConfig{ZDR: true}))
			in.ZDRAttested = true
			return in
		}()},
		{name: "org ZDR on an unattested BYOK provider", in: byokIn(withPolicy(tenant.DataPolicyConfig{ZDR: true})), wantCode: CodeDataPolicyZDR},
		{name: "managed default denies data collection: a collecting endpoint is dropped", in: managedIn(nil, entry(ep("cheap", 1e-8, collects), ep("anthropic", 1e-6, zdr))),
			wantEP: "anthropic", wantEff: &models.EffectiveDataPolicy{DataCollection: "deny"}},
		{name: "managed allow_data_collection keeps the collecting endpoint", in: managedIn(withManaged(tenant.ManagedModelsConfig{Enabled: true, AllowDataCollection: true}),
			entry(ep("cheap", 1e-8, collects), ep("anthropic", 1e-6, zdr))), wantEP: "cheap", wantEff: &models.EffectiveDataPolicy{DataCollection: "allow"}},
		{name: "managed deny passes an unknown aggregator endpoint, which the pinned policy covers", in: managedIn(nil, entry(ep("deepinfra", 1e-6))), wantEP: "deepinfra"},
		{name: "managed deny refuses an unknown direct endpoint", in: managedIn(nil, entry(ep("anthropic", 1e-6, priceCard))), wantCode: CodeDataPolicyCollection},
		{name: "managed deny on an aggregator with no endpoint data", in: managedIn(nil, entry())},
		{name: "a request cannot loosen the org's data_collection deny", in: func() Input {
			in := managedIn(&tenant.OrgConfig{
				ManagedModels: &tenant.ManagedModelsConfig{Enabled: true, AllowDataCollection: true},
				Routing:       &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{DataCollection: "deny"}},
			}, entry(ep("cheap", 1e-8, collects), ep("anthropic", 1e-6, zdr)))
			in.Prefs = &models.ProviderPrefs{DataCollection: "allow"}
			return in
		}(), wantEP: "anthropic", wantEff: &models.EffectiveDataPolicy{DataCollection: "deny"}},
		{name: "a request cannot loosen the org's ZDR", in: func() Input {
			in := managedIn(withPolicy(tenant.DataPolicyConfig{ZDR: true}), entry(ep("deepinfra", 1e-6)))
			in.Prefs = &models.ProviderPrefs{ZDR: ptr(false)}
			return in
		}(), wantCode: CodeDataPolicyZDR},
		{name: "a request ZDR tightens an unattested BYOK provider", in: func() Input {
			in := byokIn(nil)
			in.Prefs = &models.ProviderPrefs{ZDR: ptr(true)}
			return in
		}(), wantCode: CodeDataPolicyZDR},
		{name: "a request ZDR on a BYOK aggregator is forwarded to it", in: func() Input {
			in := byokIn(nil)
			in.Aggregator = true
			in.Prefs = &models.ProviderPrefs{ZDR: ptr(true)}
			return in
		}()},
		{name: "allowed_regions refuse a managed aggregator endpoint tagged eu", in: managedIn(withPolicy(tenant.DataPolicyConfig{AllowedRegions: []string{"eu"}}),
			entry(ep("google-vertex/eu", 1e-6, zdr))), wantCode: CodeRegionUnpinnable},
		{name: "allowed_regions refuse a managed aggregator with no endpoint data", in: managedIn(withPolicy(tenant.DataPolicyConfig{AllowedRegions: []string{"eu"}}), entry()),
			wantCode: CodeRegionUnpinnable},
		{name: "allowed_regions keep a price-card endpoint in eu", in: managedIn(withManaged(tenant.ManagedModelsConfig{Enabled: true, AllowDataCollection: true}), entry(
			ep("anthropic/eu-west-1", 1e-6, priceCard))), wantEP: "anthropic/eu-west-1"},
		{name: "allowed_regions refuse a price-card endpoint in us", in: func() Input {
			in := managedIn(&tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true, AllowDataCollection: true},
				Routing: &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{AllowedRegions: []string{"eu"}}}}, entry(ep("anthropic/us", 1e-6, priceCard)))
			return in
		}(), wantCode: CodeRegionNotAllowed},
		{name: "allowed_regions keep a BYOK provider whose data region is inside one", in: func() Input {
			in := byokIn(withPolicy(tenant.DataPolicyConfig{AllowedRegions: []string{"eu"}}))
			in.DataRegion = "eu-west-1"
			return in
		}()},
		{name: "allowed_regions fail closed on a BYOK provider with no region", in: byokIn(withPolicy(tenant.DataPolicyConfig{AllowedRegions: []string{"eu"}})),
			wantCode: CodeRegionNotAllowed},
		{name: "a preview model in a blocked env", in: func() Input {
			in := byokIn(withPolicy(tenant.DataPolicyConfig{BlockPreviewInEnvs: []string{"prod"}}))
			in.Entry, in.Env = &catalog.Model{ID: "gpt-4o", Preview: true}, "Prod"
			return in
		}(), wantCode: CodePreviewBlocked},
		{name: "a preview model in another env", in: func() Input {
			in := byokIn(withPolicy(tenant.DataPolicyConfig{BlockPreviewInEnvs: []string{"prod"}}))
			in.Entry, in.Env = &catalog.Model{ID: "gpt-4o", Preview: true}, "staging"
			return in
		}()},

		// Rule 6: provider.only and provider.ignore.
		{name: "provider.only keeps one endpoint", in: func() Input {
			in := managedIn(nil, entry(ep("together", 1e-8), ep("deepinfra/fp8", 1e-6)))
			in.Prefs = &models.ProviderPrefs{Only: []string{"DeepInfra"}}
			return in
		}(), wantEP: "deepinfra/fp8"},
		{name: "provider.ignore drops every endpoint", in: func() Input {
			in := managedIn(nil, entry(ep("together", 1e-8), ep("deepinfra", 1e-6)))
			in.Prefs = &models.ProviderPrefs{Ignore: []string{"together", "deepinfra"}}
			return in
		}(), wantCode: CodeNoProviderMatches},
		{name: "provider.only on a direct BYOK provider it does not name", in: func() Input {
			in := byokIn(nil)
			in.Prefs = &models.ProviderPrefs{Only: []string{"anthropic"}}
			return in
		}(), wantCode: CodeNoProviderMatches},
		{name: "provider.only on a BYOK aggregator is forwarded", in: func() Input {
			in := byokIn(nil)
			in.Aggregator = true
			in.Prefs = &models.ProviderPrefs{Only: []string{"anthropic"}}
			return in
		}()},
		{name: "provider.only on a managed aggregator with no endpoint data", in: func() Input {
			in := managedIn(nil, entry())
			in.Prefs = &models.ProviderPrefs{Only: []string{"anthropic"}}
			return in
		}(), wantCode: CodeNoProviderMatches},
		{name: "provider.only cannot reach a provider the org locks out", in: func() Input {
			in := byokIn(&tenant.OrgConfig{Routing: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"openai"}}}})
			in.Prefs = &models.ProviderPrefs{Only: []string{"openai"}}
			return in
		}(), wantCode: CodeOrgProviderDenied},

		// Rule 7: max_price in USD per million tokens.
		{name: "max_price keeps the endpoint under the cap", in: func() Input {
			in := managedIn(nil, entry(ep("pricey", 2e-6), ep("cheap", 0.5e-6)))
			in.Prefs = &models.ProviderPrefs{MaxPrice: &models.ProviderMaxPrice{Prompt: ptr(1.0)}}
			return in
		}(), wantEP: "cheap"},
		{name: "max_price at exactly the price passes", in: func() Input {
			in := managedIn(nil, entry(ep("exact", 2e-6)))
			in.Prefs = &models.ProviderPrefs{MaxPrice: &models.ProviderMaxPrice{Prompt: ptr(2.0), Completion: ptr(4.0)}}
			return in
		}(), wantEP: "exact"},
		{name: "max_price below every endpoint", in: func() Input {
			in := managedIn(nil, entry(ep("a", 2e-6), ep("b", 3e-6)))
			in.Prefs = &models.ProviderPrefs{MaxPrice: &models.ProviderMaxPrice{Completion: ptr(1.0)}}
			return in
		}(), wantCode: CodePriceCap},
		{name: "max_price excludes a dynamic price", in: func() Input {
			in := managedIn(nil, entry(ep("dyn", 0, func(e *catalog.Endpoint) { e.DynamicPrice = true })))
			in.Prefs = &models.ProviderPrefs{MaxPrice: &models.ProviderMaxPrice{Prompt: ptr(100.0)}}
			return in
		}(), wantCode: CodePriceCap},
		{name: "max_price fails closed on an unknown price", in: func() Input {
			in := byokIn(nil)
			in.Prefs = &models.ProviderPrefs{MaxPrice: &models.ProviderMaxPrice{Prompt: ptr(100.0)}}
			return in
		}(), wantCode: CodePriceCap},

		// Rule 8: quantizations and require_parameters.
		{name: "quantizations keep the fp8 endpoint", in: func() Input {
			in := managedIn(nil, entry(ep("together", 1e-8, quant("fp4")), ep("deepinfra/fp8", 1e-6, quant("fp8")), ep("x", 1e-9)))
			in.Prefs = &models.ProviderPrefs{Quantizations: []string{"FP8"}}
			return in
		}(), wantEP: "deepinfra/fp8"},
		{name: "quantizations with no known quantization", in: func() Input {
			in := managedIn(nil, entry(ep("x", 1e-9)))
			in.Prefs = &models.ProviderPrefs{Quantizations: []string{"fp8"}}
			return in
		}(), wantCode: CodeQuantization},
		{name: "require_parameters drops the endpoint without tools", in: func() Input {
			in := managedIn(nil, entry(ep("notools", 1e-8, func(e *catalog.Endpoint) { e.SupportedParams = []string{"max_tokens"} }), ep("tools", 1e-6)))
			in.Prefs = &models.ProviderPrefs{RequireParameters: ptr(true)}
			in.Request = &models.ChatCompletionRequest{Tools: []models.Tool{{Type: "function"}}, MaxTokens: ptr(10)}
			return in
		}(), wantEP: "tools"},
		{name: "require_parameters with no endpoint supporting them", in: func() Input {
			in := managedIn(nil, entry(ep("a", 1e-6)))
			in.Prefs = &models.ProviderPrefs{RequireParameters: ptr(true)}
			in.Request = &models.ChatCompletionRequest{ResponseFormat: &models.ResponseFormat{Type: "json_schema"}}
			return in
		}(), wantCode: CodeMissingParameter},

		// Rule 9: the billing-mode restriction.
		{name: "byok restriction refuses a managed candidate", in: func() Input {
			in := managedIn(nil, nil)
			in.ModeRestriction = models.BillingModeBYOK
			return in
		}(), wantCode: CodeBillingModeRestricted},
		{name: "managed restriction refuses a BYOK candidate", in: func() Input {
			in := byokIn(nil)
			in.ModeRestriction = models.BillingModeManaged
			return in
		}(), wantCode: CodeBillingModeRestricted},
		{name: "any restriction refuses nothing", in: func() Input {
			in := managedIn(nil, nil)
			in.ModeRestriction = models.BillingModeAny
			return in
		}()},

		// Rule 10: tool history.
		{name: "gemini-3 with unsigned tool history", in: func() Input {
			in := byokIn(nil)
			in.Model, in.Request = "google/gemini-3-pro", toolTranscript("call_abc")
			return in
		}(), wantCode: CodeToolHistoryIncompatible},
		{name: "gemini-3 with signed tool history", in: func() Input {
			in := byokIn(nil)
			in.Model, in.Request = "google/gemini-3-pro", toolTranscript("call_0::sig::c2ln")
			return in
		}()},
		{name: "gemini-3 named without its vendor", in: func() Input {
			in := byokIn(nil)
			in.Model, in.Request = "gemini-3-flash", toolTranscript("call_abc")
			return in
		}(), wantCode: CodeToolHistoryIncompatible},
		{name: "a later gemini generation is in the default family", in: func() Input {
			in := byokIn(nil)
			in.Model, in.Request = "google/gemini-4-pro", toolTranscript("call_abc")
			return in
		}(), wantCode: CodeToolHistoryIncompatible},
		{name: "gemini-3.1 is in the default family", in: func() Input {
			in := byokIn(nil)
			in.Model, in.Request = "google/gemini-3.1-flash", toolTranscript("call_abc")
			return in
		}(), wantCode: CodeToolHistoryIncompatible},
		{name: "gemma is not gemini", in: func() Input {
			in := byokIn(nil)
			in.Model, in.Request = "google/gemma-4-27b", toolTranscript("call_abc")
			return in
		}()},
		{name: "gemini-2.5 is not in the default family", in: func() Input {
			in := byokIn(nil)
			in.Model, in.Request = "google/gemini-2.5-pro", toolTranscript("call_abc")
			return in
		}()},
		{name: "a configured family replaces the default", in: func() Input {
			in := byokIn(nil)
			in.Model, in.Request, in.ThoughtSignatureModels = "google/gemini-2.5-pro", toolTranscript("call_abc"), []string{"google/gemini-2.5*"}
			return in
		}(), wantCode: CodeToolHistoryIncompatible},
	}
	_ = pay
	return cases
}

func TestCheckTable(t *testing.T) {
	cases := checkCases()
	if len(cases) < 40 {
		t.Fatalf("%d cases, want at least 40", len(cases))
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			v := Check(&tc.in)
			if tc.wantCode != "" {
				if v.OK || v.Code != tc.wantCode || v.Reason == "" {
					t.Fatalf("verdict = %+v, want refusal %s with a reason", v, tc.wantCode)
				}
				return
			}
			if !v.OK {
				t.Fatalf("refused %s: %s", v.Code, v.Reason)
			}
			if tc.wantEP != "" && (v.Endpoint == nil || v.Endpoint.Tag != tc.wantEP) {
				t.Fatalf("best endpoint = %+v, want %s", v.Endpoint, tc.wantEP)
			}
			if tc.wantEff != nil && v.Effective != *tc.wantEff {
				t.Fatalf("effective policy = %+v, want %+v", v.Effective, *tc.wantEff)
			}
			if v.Deprecated != tc.wantDep {
				t.Fatalf("deprecated = %q, want %q", v.Deprecated, tc.wantDep)
			}
		})
	}
}

// The rules run in one fixed order, from one registration list.
func TestRuleOrder(t *testing.T) {
	want := []string{"key_acl", "org_provider_lock", "managed_allow_deny", "catalog_state", "data_policy",
		"provider_filter", "max_price", "quantization_parameters", "billing_mode", "tool_history", "server_tools"}
	if got := RuleNames(); strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("rules = %v, want %v", got, want)
	}
	// The first failing rule decides: a key ACL refusal beats every later one.
	in := managedIn(withManaged(tenant.ManagedModelsConfig{Enabled: false}), nil)
	in.Key.AllowedProviders = "anthropic"
	if v := Check(&in); v.Code != CodeKeyProviderDenied {
		t.Fatalf("code = %s, want the first rule's", v.Code)
	}
}

func TestStatusFor(t *testing.T) {
	for code, want := range map[string]int{CodeModelExpired: http.StatusGone, CodeNoProviderMatches: http.StatusNotFound, CodePriceCap: http.StatusForbidden} {
		if got := StatusFor(code); got != want {
			t.Errorf("StatusFor(%s) = %d, want %d", code, got, want)
		}
	}
}

func TestEffectivePolicyTightensOnly(t *testing.T) {
	org := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{ZDR: true, DataCollection: "deny"}}}
	if got := EffectivePolicy(org, &models.ProviderPrefs{ZDR: ptr(false), DataCollection: "allow"}, false); !got.ZDR || got.DataCollection != "deny" {
		t.Errorf("request loosened the org policy: %+v", got)
	}
	if got := EffectivePolicy(nil, &models.ProviderPrefs{ZDR: ptr(true), DataCollection: "deny"}, false); !got.ZDR || got.DataCollection != "deny" {
		t.Errorf("request did not tighten: %+v", got)
	}
	if got := EffectivePolicy(nil, nil, false); got.ZDR || got.DataCollection != "allow" {
		t.Errorf("no policy = %+v, want allow", got)
	}
	if got := EffectivePolicy(nil, nil, true); got.DataCollection != "deny" {
		t.Errorf("managed with no org = %+v, want deny", got)
	}
	if got := EffectivePolicy(withManaged(tenant.ManagedModelsConfig{Enabled: true, AllowDataCollection: true}), nil, true); got.DataCollection != "allow" {
		t.Errorf("managed opt-in = %+v, want allow", got)
	}
}

func TestGlobMatch(t *testing.T) {
	for _, tc := range []struct {
		pattern, s string
		want       bool
	}{
		{"anthropic/*", "anthropic/claude-sonnet-5.5", true},
		{"anthropic/*", "openai/gpt-4o", false},
		{"google/gemini-3*", "google/gemini-3-pro-preview", true},
		{"google/gemini-3*", "google/gemini-2.5-pro", false},
		{"*mini*", "openai/gpt-4o-mini-2024", true},
		{"*", "anything", true},
		{"X-AI", "x-ai", true},
		{"a*b*c", "aXbYc", true},
		{"a*b*c", "aXcYb", false},
	} {
		if got := globMatch(tc.pattern, tc.s); got != tc.want {
			t.Errorf("globMatch(%q, %q) = %v, want %v", tc.pattern, tc.s, got, tc.want)
		}
	}
}

// A candidate with no endpoints allocates nothing to check: the explicit
// route on the chat hot path pays no allocation for eligibility.
func TestCheckAllocatesNothingWithoutEndpoints(t *testing.T) {
	in := byokIn(&tenant.OrgConfig{Routing: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"openai"}}}})
	in.Key.AllowedModels, in.Key.AllowedProviders = "gpt-4o", "openai"
	in.Request = &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user"}}}
	if n := testing.AllocsPerRun(100, func() {
		if v := Check(&in); !v.OK {
			t.Fatalf("refused: %+v", v)
		}
	}); n != 0 {
		t.Fatalf("Check allocated %v times per run", n)
	}
}

func TestGeminiGeneration(t *testing.T) {
	for _, tc := range []struct {
		model string
		want  int
	}{
		{"google/gemini-3-pro", 3},
		{"google/gemini-3.1-flash-lite", 3},
		{"Google/Gemini-4-Ultra", 4},
		{"gemini-12-pro", 12},
		{"google/gemini-2.5-pro", 2},
		{"google/gemini-exp-1206", 0},
		{"google/gemma-3-27b", 0},
		{"openrouter/gemini-3-pro", 0},
		{"google/gemini-", 0},
	} {
		if got := geminiGeneration(tc.model); got != tc.want {
			t.Errorf("geminiGeneration(%q) = %d, want %d", tc.model, got, tc.want)
		}
	}
}

func TestCSVContains(t *testing.T) {
	for _, tc := range []struct {
		list, item string
		want       bool
	}{
		{"openai,anthropic", "anthropic", true},
		{"openai, anthropic", "anthropic", true},
		{"openai", "open", false},
		{"", "openai", false},
		{"openai,", "openai", true},
	} {
		if got := CSVContains(tc.list, tc.item); got != tc.want {
			t.Errorf("CSVContains(%q, %q) = %v, want %v", tc.list, tc.item, got, tc.want)
		}
	}
}
