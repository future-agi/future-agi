package cost

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Catalogue prices for the reported-cost tests, chosen so the catalogue
// price of every fixture differs from the cost the vendor states.
const (
	reportedTestInput  = 1.0 / 1_000_000 // USD per input token
	reportedTestOutput = 2.0 / 1_000_000 // USD per output token
)

func reportedTestDB(overrides map[string]modeldb.ModelOverride) *modeldb.ModelDB {
	priced := func(id, provider string) *modeldb.ModelInfo {
		return &modeldb.ModelInfo{ID: id, Provider: provider, Mode: modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{InputPerToken: reportedTestInput, OutputPerToken: reportedTestOutput}}
	}
	return modeldb.New(map[string]*modeldb.ModelInfo{
		"perplexity/sonar":        priced("perplexity/sonar", "perplexity"),
		"openrouter/openai/gpt-x": priced("openrouter/openai/gpt-x", "openrouter"),
		"xai/grok-x":              priced("xai/grok-x", "xai"),
	}, overrides)
}

// reportedPlugin is a cost plugin whose every request is served by a provider
// trusted as vendor ("" for none).
func reportedPlugin(db *modeldb.ModelDB, store *tenant.Store, aliasFactors map[string]float64, vendor string) *Plugin {
	p := New(true, func() *modeldb.ModelDB { return db }, store, aliasFactors)
	p.SetReportedCostVendors(func(*models.RequestContext) string { return vendor })
	return p
}

// reportedRC is a chat request served by provider for model, whose response
// usage is the JSON object usage, parsed the way a provider response is.
func reportedRC(t *testing.T, provider, model, usage string) *models.RequestContext {
	t.Helper()
	var u models.Usage
	if err := json.Unmarshal([]byte(usage), &u); err != nil {
		t.Fatalf("parsing usage: %v", err)
	}
	return &models.RequestContext{
		Model:    model,
		Provider: provider,
		Response: &models.ChatCompletionResponse{Usage: &u},
		Metadata: map[string]string{},
		Timings:  map[string]time.Duration{},
	}
}

func catalogueCost(prompt, completion int) string {
	return fmt.Sprintf("%.6f", float64(prompt)*reportedTestInput+float64(completion)*reportedTestOutput)
}

func TestReportedCost_EachVendorIsRecorded(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		vendor, model, usage, wantCost, wantUnit string
		prompt, completion                       int
	}{
		{"openrouter", "openai/gpt-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00042}`, "0.000420", UnitOpenRouterCredits, 1000, 100},
		// Shape and values from the docs.perplexity.ai example with web_search and fetch_url.
		{"perplexity", "sonar", `{"input_tokens":12088,"output_tokens":2743,"cost":{"cache_read_cost":0.00059,"currency":"USD","input_cost":0.00919,"output_cost":0.02743,"tool_calls_cost":0.0055,"total_cost":0.04271}}`, "0.042710", UnitUSD, 12088, 2743},
		// Value from docs.x.ai cost tracking.
		{"xai", "grok-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost_in_usd_ticks":37756000}`, "0.003776", UnitUSDTicks, 1000, 100},
	} {
		t.Run(tc.vendor, func(t *testing.T) {
			t.Parallel()
			rc := reportedRC(t, tc.vendor, tc.model, tc.usage)
			reportedPlugin(reportedTestDB(nil), nil, nil, tc.vendor).ProcessResponse(context.Background(), rc)

			want := map[string]string{
				"cost":               tc.wantCost,
				"cost_source":        CostSourceProviderReported,
				MetadataReportedUnit: tc.wantUnit,
				MetadataCatalogue:    catalogueCost(tc.prompt, tc.completion),
			}
			for k, v := range want {
				if rc.Metadata[k] != v {
					t.Errorf("Metadata[%s] = %q, want %q", k, rc.Metadata[k], v)
				}
			}
			if want[MetadataCatalogue] == tc.wantCost {
				t.Fatal("fixture: the catalogue price equals the reported cost, so the test cannot tell them apart")
			}
			for _, k := range []string{MetadataIsBYOK, MetadataReportedZero} {
				if v, ok := rc.Metadata[k]; ok {
					t.Errorf("Metadata[%s] = %q, want unset", k, v)
				}
			}
		})
	}
}

// TH-10225: what Perplexity's Agent/Responses API states for a call with no
// tool, with web_search and at each reasoning effort. Every one is recorded
// at exactly total_cost, which already includes the search fee. Shapes from
// docs.perplexity.ai; values synthetic.
func TestReportedCost_PerplexityResponsesFixtures(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name  string
		usage string
		total float64
	}{
		{"no tool", `{"input_tokens":4718,"output_tokens":450,"total_tokens":5168,"cost":{"currency":"USD","input_cost":0.004718,"output_cost":0.00045,"total_cost":0.005168}}`, 0.005168},
		{"web_search", `{"input_tokens":4718,"output_tokens":450,"total_tokens":5168,"cost":{"currency":"USD","input_cost":0.01887,"output_cost":0.009,"tool_calls_cost":0.0025,"total_cost":0.03037},"tool_calls_details":{"search_web":{"invocation":1}}}`, 0.03037},
		{"effort low", `{"input_tokens":3100,"output_tokens":620,"cost":{"currency":"USD","input_cost":0.0031,"output_cost":0.0093,"tool_calls_cost":0.005,"cache_read_cost":null,"total_cost":0.0174},"tool_calls_details":{"search_web":{"invocation":1}}}`, 0.0174},
		{"effort medium", `{"input_tokens":9800,"output_tokens":1450,"cost":{"currency":"USD","input_cost":0.0098,"output_cost":0.02175,"tool_calls_cost":0.008,"cache_read_cost":0.00021,"total_cost":0.03976},"tool_calls_details":{"search_web":{"invocation":2}}}`, 0.03976},
		{"effort high", `{"input_tokens":21000,"output_tokens":3900,"cost":{"currency":"USD","input_cost":0.021,"output_cost":0.0585,"tool_calls_cost":0.014,"cache_read_cost":0.00088,"cache_creation_cost":null,"total_cost":0.09438},"tool_calls_details":{"search_web":{"invocation":3},"fetch_url":{"invocation":1}}}`, 0.09438},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			rc := reportedRC(t, "perplexity", "sonar", tc.usage)
			reportedPlugin(reportedTestDB(nil), nil, nil, "perplexity").ProcessResponse(context.Background(), rc)
			if got, want := rc.Metadata["cost"], fmt.Sprintf("%.6f", tc.total); got != want {
				t.Errorf("cost = %q, want total_cost %q", got, want)
			}
			if got := rc.Metadata["cost_source"]; got != CostSourceProviderReported {
				t.Errorf("cost_source = %q, want %q", got, CostSourceProviderReported)
			}
			if rc.Metadata[MetadataCatalogue] == rc.Metadata["cost"] {
				t.Fatal("fixture: the catalogue price equals total_cost")
			}
		})
	}
}

// The chat shape's request_cost is part of total_cost and is not added again.
func TestReportedCost_PerplexityChatRequestCostIsNotAddedTwice(t *testing.T) {
	t.Parallel()
	// Shape and values from the docs.perplexity.ai Sonar example.
	rc := reportedRC(t, "perplexity", "sonar", `{"prompt_tokens":8,"completion_tokens":439,"total_tokens":447,"search_context_size":"low","cost":{"input_tokens_cost":2.4e-05,"output_tokens_cost":0.006585,"request_cost":0.006,"total_cost":0.012609}}`)
	reportedPlugin(reportedTestDB(nil), nil, nil, "perplexity").ProcessResponse(context.Background(), rc)
	if got := rc.Metadata["cost"]; got != "0.012609" {
		t.Fatalf("cost = %q, want total_cost 0.012609 (not total_cost + request_cost)", got)
	}
}

// Whatever the vendor's statement cannot be used for, the catalogue prices
// the request and says so.
func TestReportedCost_FallsBackToTheCatalogue(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct{ name, vendor, usage string }{
		{"no cost object", "perplexity", `{"prompt_tokens":1000,"completion_tokens":100}`},
		{"EUR", "perplexity", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"EUR","input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`},
		{"responses shape without currency", "perplexity", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`},
		{"total null", "perplexity", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":null}}`},
		{"total negative", "perplexity", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":-0.003}}`},
		{"total string", "perplexity", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":"0.003"}}`},
		{"malformed object", "perplexity", `{"prompt_tokens":1000,"completion_tokens":100,"cost":"total_cost=0.003"}`},
		{"openrouter missing cost", "openrouter", `{"prompt_tokens":1000,"completion_tokens":100}`},
		{"openrouter string cost", "openrouter", `{"prompt_tokens":1000,"completion_tokens":100,"cost":"0.00042"}`},
		{"xai missing ticks", "xai", `{"prompt_tokens":1000,"completion_tokens":100}`},
		// An OpenRouter provider handed Perplexity's cost object reads only
		// its own field, which is not a number here.
		{"openrouter given perplexity's shape", "openrouter", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`},
		// Not trusted: the trust rule named no vendor.
		{"untrusted provider", "", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`},
		{"untrusted provider with openrouter cost", "", `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00042}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			rc := reportedRC(t, "perplexity", "sonar", tc.usage)
			reportedPlugin(reportedTestDB(nil), nil, nil, tc.vendor).ProcessResponse(context.Background(), rc)
			if got, want := rc.Metadata["cost"], catalogueCost(1000, 100); got != want {
				t.Errorf("cost = %q, want the catalogue price %q", got, want)
			}
			if got := rc.Metadata["cost_source"]; got != CostSourceCatalogue {
				t.Errorf("cost_source = %q, want %q", got, CostSourceCatalogue)
			}
			for _, k := range []string{MetadataReportedUnit, MetadataCatalogue, MetadataReportedZero} {
				if v, ok := rc.Metadata[k]; ok {
					t.Errorf("Metadata[%s] = %q, want unset", k, v)
				}
			}
		})
	}
}

// With no trust lookup wired, nothing is trusted.
func TestReportedCost_NoLookupMeansCatalogue(t *testing.T) {
	t.Parallel()
	db := reportedTestDB(nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	rc := reportedRC(t, "openrouter", "openai/gpt-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00042}`)
	p.ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != catalogueCost(1000, 100) || rc.Metadata["cost_source"] != CostSourceCatalogue {
		t.Fatalf("cost = %q source = %q, want the catalogue", rc.Metadata["cost"], rc.Metadata["cost_source"])
	}
}

// OpenRouter BYOK: usage.cost is OpenRouter's fee alone. The upstream's
// charge is the cost; without it the catalogue prices the request, and the
// row still says it was BYOK.
func TestReportedCost_OpenRouterBYOK(t *testing.T) {
	t.Parallel()
	t.Run("with upstream cost", func(t *testing.T) {
		t.Parallel()
		rc := reportedRC(t, "openrouter", "openai/gpt-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00001,"is_byok":true,"cost_details":{"upstream_inference_cost":0.0021}}`)
		reportedPlugin(reportedTestDB(nil), nil, nil, "openrouter").ProcessResponse(context.Background(), rc)
		if rc.Metadata["cost"] != "0.002100" || rc.Metadata["cost_source"] != CostSourceProviderReported || rc.Metadata[MetadataIsBYOK] != "true" {
			t.Fatalf("metadata = %v", rc.Metadata)
		}
	})
	t.Run("without upstream cost", func(t *testing.T) {
		t.Parallel()
		rc := reportedRC(t, "openrouter", "openai/gpt-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00001,"is_byok":true}`)
		reportedPlugin(reportedTestDB(nil), nil, nil, "openrouter").ProcessResponse(context.Background(), rc)
		if rc.Metadata["cost"] != catalogueCost(1000, 100) || rc.Metadata["cost_source"] != CostSourceCatalogue || rc.Metadata[MetadataIsBYOK] != "true" {
			t.Fatalf("metadata = %v", rc.Metadata)
		}
	})
}

// Org custom pricing wins over the vendor's statement.
func TestReportedCost_OrgCustomPricingWins(t *testing.T) {
	t.Parallel()
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{
		Enabled:       true,
		CustomPricing: map[string]*tenant.CustomPricing{"sonar": {InputPerMTok: 10, OutputPerMTok: 20}},
	}})
	rc := reportedRC(t, "perplexity", "sonar", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`)
	rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
	reportedPlugin(reportedTestDB(nil), store, nil, "perplexity").ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "0.012000" || rc.Metadata["cost_source"] != CostSourceOrgCustom {
		t.Fatalf("cost = %q source = %q, want the org's token price 0.012000, org_custom", rc.Metadata["cost"], rc.Metadata["cost_source"])
	}
	if _, ok := rc.Metadata[MetadataReportedUnit]; ok {
		t.Fatal("org-priced row says it used a reported cost")
	}
}

// model_database.overrides correct the catalogue; the vendor's statement
// still wins over them.
func TestReportedCost_WinsOverOperatorOverrides(t *testing.T) {
	t.Parallel()
	in, out := 5.0/1_000_000, 5.0/1_000_000
	db := reportedTestDB(map[string]modeldb.ModelOverride{
		"perplexity/sonar": {Pricing: &modeldb.PricingOverride{InputPerToken: &in, OutputPerToken: &out}},
	})
	rc := reportedRC(t, "perplexity", "sonar", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`)
	reportedPlugin(db, nil, nil, "perplexity").ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "0.003000" || rc.Metadata["cost_source"] != CostSourceProviderReported {
		t.Fatalf("cost = %q source = %q, want the reported 0.003000", rc.Metadata["cost"], rc.Metadata["cost_source"])
	}
	if got := rc.Metadata[MetadataCatalogue]; got != "0.005500" {
		t.Fatalf("cost_catalogue = %q, want the overridden catalogue price 0.005500", got)
	}

	// Untrusted (as with ignore_provider_cost), the override prices it.
	rc = reportedRC(t, "perplexity", "sonar", `{"prompt_tokens":1000,"completion_tokens":100,"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`)
	reportedPlugin(db, nil, nil, "").ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "0.005500" || rc.Metadata["cost_source"] != CostSourceCatalogue {
		t.Fatalf("untrusted: cost = %q source = %q, want the override 0.005500", rc.Metadata["cost"], rc.Metadata["cost_source"])
	}
}

// The alias cost factor multiplies a reported cost as it does a catalogue one.
func TestReportedCost_AliasFactorApplies(t *testing.T) {
	t.Parallel()
	rc := reportedRC(t, "xai", "grok-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost_in_usd_ticks":37756000}`)
	rc.Metadata["model_alias"] = "premium-grok"
	reportedPlugin(reportedTestDB(nil), nil, map[string]float64{"premium": 2}, "xai").ProcessResponse(context.Background(), rc)
	if got, want := rc.Metadata["cost"], fmt.Sprintf("%.6f", 0.0037756*2); got != want {
		t.Fatalf("cost = %q, want %q", got, want)
	}
	if got, want := rc.Metadata[MetadataCatalogue], fmt.Sprintf("%.6f", (1000*reportedTestInput+100*reportedTestOutput)*2); got != want {
		t.Fatalf("cost_catalogue = %q, want %q", got, want)
	}
}

// A model the catalogue does not price is still recorded at the vendor's
// cost, not marked unpriced.
func TestReportedCost_UnpricedModelUsesTheReportedCost(t *testing.T) {
	t.Parallel()
	rc := reportedRC(t, "openrouter", "some-vendor/new-model", `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00042}`)
	reportedPlugin(reportedTestDB(nil), nil, nil, "openrouter").ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "0.000420" || rc.Metadata["cost_source"] != CostSourceProviderReported {
		t.Fatalf("cost = %q source = %q", rc.Metadata["cost"], rc.Metadata["cost_source"])
	}
	if v, ok := rc.Metadata[MetadataCatalogue]; ok {
		t.Fatalf("cost_catalogue = %q for a model the catalogue does not price", v)
	}
}

// Only chat-like routes read a reported cost.
func TestReportedCost_NotReadOnOtherEndpoints(t *testing.T) {
	t.Parallel()
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.SetReportedCostVendors(func(*models.RequestContext) string { return "openrouter" })
	var u models.Usage
	_ = json.Unmarshal([]byte(`{"prompt_tokens":1000,"completion_tokens":100,"cost":0.5}`), &u)
	rc := decisionRC("typesafe/jev-latest", "jev-latest", "jev-1.13.0", 1000, 100)
	rc.Provider = "openrouter"
	rc.Response.Usage.Extra = u.Extra
	p.ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost_source"] == CostSourceProviderReported || rc.Metadata["cost"] == "0.500000" {
		t.Fatalf("decision call used the reported cost: %v", rc.Metadata)
	}
}

// Not parallel: it swaps the process-wide slog default to capture output.
func TestReportedCost_SuspectZeroIsFlaggedAndWarnedOnce(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	p := reportedPlugin(reportedTestDB(nil), nil, nil, "openrouter")
	zero := `{"prompt_tokens":1000,"completion_tokens":100,"cost":0}`
	for i := 0; i < 3; i++ {
		rc := reportedRC(t, "openrouter", "openai/gpt-x", zero)
		p.ProcessResponse(context.Background(), rc)
		if rc.Metadata["cost"] != "0.000000" || rc.Metadata["cost_source"] != CostSourceProviderReported || rc.Metadata[MetadataReportedZero] != "true" {
			t.Fatalf("metadata = %v, want cost 0.000000 provider_reported flagged", rc.Metadata)
		}
	}
	streamed := reportedRC(t, "openrouter", "openai/gpt-x", zero)
	streamed.IsStream = true
	p.ProcessResponse(context.Background(), streamed)

	out := buf.String()
	if n := strings.Count(out, "provider reported a cost of 0"); n != 2 {
		t.Fatalf("warned %d times, want once non-streamed and once streamed\n%s", n, out)
	}
	if !strings.Contains(out, "ignore_provider_cost") {
		t.Fatalf("the warning should name the switch\n%s", out)
	}

	// Not suspect: no tokens billed, or a model the catalogue does not price.
	for _, tc := range []struct{ model, usage string }{
		{"openai/gpt-x", `{"prompt_tokens":0,"completion_tokens":0,"cost":0}`},
		{"unlisted/model", zero},
	} {
		rc := reportedRC(t, "openrouter", tc.model, tc.usage)
		p.ProcessResponse(context.Background(), rc)
		if rc.Metadata["cost"] != "0.000000" || rc.Metadata["cost_source"] != CostSourceProviderReported {
			t.Fatalf("%s: metadata = %v", tc.model, rc.Metadata)
		}
		if v, ok := rc.Metadata[MetadataReportedZero]; ok {
			t.Fatalf("%s: flagged %q", tc.model, v)
		}
	}

	// A tiny cost that formats as 0.000000 is not a zero.
	rc := reportedRC(t, "openrouter", "openai/gpt-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost":1e-9}`)
	p.ProcessResponse(context.Background(), rc)
	if v, ok := rc.Metadata[MetadataReportedZero]; ok {
		t.Fatalf("a non-zero cost was flagged %q", v)
	}
}

// Not parallel: it swaps the process-wide slog default to capture output.
func TestReportedCost_SuspectZeroWarningSetIsBounded(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	db := reportedTestDB(nil)
	p := reportedPlugin(db, nil, nil, "openrouter")
	for i := 0; i < maxReportedZeroWarnings+10; i++ {
		rc := reportedRC(t, fmt.Sprintf("openrouter-%d", i), "openrouter/openai/gpt-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost":0}`)
		p.ProcessResponse(context.Background(), rc)
	}
	if n := strings.Count(buf.String(), "provider reported a cost of 0"); n != maxReportedZeroWarnings {
		t.Fatalf("warned %d times, want the cap %d", n, maxReportedZeroWarnings)
	}
}
