package cost

import (
	"context"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// managedDB has a vendor's direct price and the managed provider's own price
// for the same model, filed "<provider>/<model>" as the catalog files it.
func managedDB() *modeldb.ModelDB {
	return modeldb.New(map[string]*modeldb.ModelInfo{
		"claude-sonnet-5.5": {Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{InputPerToken: 3e-6, OutputPerToken: 15e-6}},
		"openrouter/anthropic/claude-sonnet-5.5": {Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{InputPerToken: 2e-6, OutputPerToken: 10e-6}},
	}, nil)
}

func managedPlugin(store *tenant.Store) *Plugin {
	db := managedDB()
	p := New(true, func() *modeldb.ModelDB { return db }, store, nil)
	p.SetManagedMarkup(5)
	return p
}

func TestManagedRequestUsesReportedCostPlusMarkup(t *testing.T) {
	rc := newRC("anthropic/claude-sonnet-5.5", 1000, 500)
	rc.Provider = "openrouter"
	rc.Metadata["billing_mode"] = "managed"
	reported := 0.007
	rc.Response.Usage.Cost = &reported

	managedPlugin(nil).ProcessResponse(context.Background(), rc)

	if rc.Metadata["cost"] != "0.007350" || rc.Metadata["cost_base"] != "0.007000" || rc.Metadata["cost_markup_percent"] != "5" {
		t.Fatalf("metadata = %v", rc.Metadata)
	}
	if rc.Metadata["cost_source"] != "provider_reported" {
		t.Errorf("cost_source = %q", rc.Metadata["cost_source"])
	}
}

func TestManagedRequestWithoutReportedCostUsesTheProvidersPrice(t *testing.T) {
	rc := newRC("anthropic/claude-sonnet-5.5", 1000, 500)
	rc.Provider = "openrouter"
	rc.Metadata["billing_mode"] = "managed"

	managedPlugin(nil).ProcessResponse(context.Background(), rc)

	// 1000 × $2/M + 500 × $10/M = $0.007 at OpenRouter's price, not $0.0105 at Anthropic's.
	if rc.Metadata["cost_base"] != "0.007000" || rc.Metadata["cost"] != fmt.Sprintf("%.6f", 0.007*1.05) {
		t.Fatalf("metadata = %v", rc.Metadata)
	}
	if rc.Metadata["cost_source"] != "catalog" {
		t.Errorf("cost_source = %q", rc.Metadata["cost_source"])
	}
}

func TestManagedRequestIgnoresOrgCustomPricing(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{
		Enabled:       true,
		CustomPricing: map[string]*tenant.CustomPricing{"anthropic/claude-sonnet-5.5": {InputPerMTok: 0, OutputPerMTok: 0}},
	}})
	rc := newRC("anthropic/claude-sonnet-5.5", 1000, 500)
	rc.Provider = "openrouter"
	rc.Metadata["billing_mode"] = "managed"
	rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"

	managedPlugin(store).ProcessResponse(context.Background(), rc)

	if rc.Metadata["cost"] != fmt.Sprintf("%.6f", 0.007*1.05) {
		t.Fatalf("an org's own pricing must not lower what FutureAGI bills; cost = %q", rc.Metadata["cost"])
	}
}

func TestBYOKRequestUsesReportedCostWithoutMarkup(t *testing.T) {
	rc := newRC("anthropic/claude-sonnet-5.5", 1000, 500)
	rc.Provider = "openrouter"
	rc.Metadata["billing_mode"] = "byok"
	reported := 0.0068
	rc.Response.Usage.Cost = &reported

	managedPlugin(nil).ProcessResponse(context.Background(), rc)

	if rc.Metadata["cost"] != "0.006800" || rc.Metadata["cost_base"] != "" {
		t.Fatalf("a BYOK request carries no markup; metadata = %v", rc.Metadata)
	}
}

func TestUnpricedManagedRequestIsFlagged(t *testing.T) {
	rc := newRC("vendor/unknown", 1000, 500)
	rc.Provider = "openrouter"
	rc.Metadata["billing_mode"] = "managed"

	managedPlugin(nil).ProcessResponse(context.Background(), rc)

	if rc.Metadata["cost_source"] != "unpriced" || rc.Metadata["cost"] != "" {
		t.Fatalf("metadata = %v", rc.Metadata)
	}
}

func TestManagedCacheHitIsNotCharged(t *testing.T) {
	rc := newRC("anthropic/claude-sonnet-5.5", 1000, 500)
	rc.Provider = "openrouter"
	rc.Metadata["billing_mode"] = "managed"
	rc.Metadata["cache_status"] = "hit_semantic"
	rc.Flags.CacheHit = true
	reported := 0.007
	rc.Response.Usage.Cost = &reported // the original request's charge, cached with it

	managedPlugin(nil).ProcessResponse(context.Background(), rc)

	if rc.Metadata["cost"] != "" || rc.Metadata["cost_source"] != "cache_hit" {
		t.Fatalf("a cache hit made no upstream call and must not be charged; metadata = %v", rc.Metadata)
	}
}
