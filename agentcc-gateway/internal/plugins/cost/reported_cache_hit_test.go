package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// A cached response still carries the vendor's usage.cost from the call that
// filled the cache. No vendor served a cache hit, so that figure must never be
// recorded as this request's provider-reported cost: a cache hit is priced as
// it was before vendor costs were read (from the catalogue, on the cached usage).
func TestReportedCost_NotReadOnCacheHits(t *testing.T) {
	t.Parallel()
	usages := map[string]string{
		"vendor cost": `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00042}`,
		"byok":        `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00001,"is_byok":true,"cost_details":{"upstream_inference_cost":0.00042}}`,
	}
	for _, status := range []string{"hit_exact", "hit_semantic", "hit_conditional"} {
		for name, usage := range usages {
			t.Run(status+"/"+name, func(t *testing.T) {
				t.Parallel()
				rc := reportedRC(t, "openrouter", "openai/gpt-x", usage)
				rc.Flags.CacheHit = true
				rc.Flags.ShortCircuited = true
				rc.Metadata["cache_status"] = status

				reportedPlugin(reportedTestDB(nil), nil, nil, "openrouter").ProcessResponse(context.Background(), rc)

				if got, want := rc.Metadata["cost"], catalogueCost(1000, 100); got != want {
					t.Errorf("cost = %q, want the catalogue price %q", got, want)
				}
				if got := rc.Metadata["cost_source"]; got == CostSourceProviderReported {
					t.Errorf("cost_source = %q on a cache hit", got)
				}
				for _, key := range []string{MetadataReportedUnit, MetadataIsBYOK, MetadataCatalogue, MetadataReportedZero} {
					if v, ok := rc.Metadata[key]; ok {
						t.Errorf("%s = %q on a cache hit", key, v)
					}
				}
			})
		}
	}
}

// Through the engine: an exact hit skips the cost plugin as before; a semantic
// hit runs it, priced from the catalogue, not at the cached vendor cost.
func TestReportedCost_CacheHitsThroughTheEngine(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		status, wantCost string
	}{
		{"hit_exact", ""},
		{"hit_semantic", catalogueCost(1000, 100)},
	} {
		t.Run(tc.status, func(t *testing.T) {
			t.Parallel()
			rc := reportedRC(t, "openrouter", "openai/gpt-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost":0.00042}`)
			rc.Flags.CacheHit = true
			rc.Flags.ShortCircuited = true
			rc.Metadata["cache_status"] = tc.status

			pipeline.NewEngine(reportedPlugin(reportedTestDB(nil), nil, nil, "openrouter")).RunPostPlugins(context.Background(), rc)

			if got := rc.Metadata["cost"]; got != tc.wantCost {
				t.Errorf("cost = %q, want %q", got, tc.wantCost)
			}
			if got := rc.Metadata["cost_source"]; got == CostSourceProviderReported {
				t.Errorf("cost_source = %q on a cache hit", got)
			}
		})
	}
}

// A vendor cost below one micro-dollar is recorded unrounded. Rounded to six
// places it read as "0.000000", which budgets, credits and the request log
// took for a vendor-stated $0.
func TestReportedCost_TinyCostIsNotRoundedToZero(t *testing.T) {
	t.Parallel()
	rc := reportedRC(t, "openrouter", "openai/gpt-x", `{"prompt_tokens":10,"completion_tokens":1,"cost":0.0000004}`)

	reportedPlugin(reportedTestDB(nil), nil, nil, "openrouter").ProcessResponse(context.Background(), rc)

	if got := rc.Metadata["cost"]; got != "0.0000004" {
		t.Errorf("cost = %q, want the unrounded %q", got, "0.0000004")
	}
	if got := rc.Metadata["cost_source"]; got != CostSourceProviderReported {
		t.Errorf("cost_source = %q, want %q", got, CostSourceProviderReported)
	}
	if v, ok := rc.Metadata[MetadataReportedZero]; ok {
		t.Errorf("%s = %q for a non-zero vendor cost", MetadataReportedZero, v)
	}
}

// At exactly half a micro-dollar six places still print "0.000000", so the
// boundary is kept unrounded too (xAI: 5,000 ticks).
func TestReportedCost_HalfMicroIsNotRoundedToZero(t *testing.T) {
	t.Parallel()
	rc := reportedRC(t, "xai", "grok-x", `{"prompt_tokens":10,"completion_tokens":1,"cost_in_usd_ticks":5000}`)

	reportedPlugin(reportedTestDB(nil), nil, nil, "xai").ProcessResponse(context.Background(), rc)

	if got := rc.Metadata["cost"]; got == "0.000000" || got == "" {
		t.Fatalf("cost = %q, want the unrounded half micro-dollar", got)
	}
	if got := rc.Metadata["cost"]; got != "0.0000005" {
		t.Errorf("cost = %q, want %q", got, "0.0000005")
	}
}

// A stated cost above the per-request ceiling is implausible and would
// overflow the micro-dollar ledgers: it is priced from the catalogue. The
// ceiling itself is accepted.
func TestReportedCost_CeilingFallsBackToTheCatalogue(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		cost, wantCost, wantSource string
	}{
		{"1e13", catalogueCost(1000, 100), CostSourceCatalogue},
		{"1000", "1000.000000", CostSourceProviderReported},
	} {
		rc := reportedRC(t, "openrouter", "openai/gpt-x", `{"prompt_tokens":1000,"completion_tokens":100,"cost":`+tc.cost+`}`)
		reportedPlugin(reportedTestDB(nil), nil, nil, "openrouter").ProcessResponse(context.Background(), rc)
		if rc.Metadata["cost"] != tc.wantCost || rc.Metadata["cost_source"] != tc.wantSource {
			t.Errorf("cost %s: got cost %q source %q, want %q %q", tc.cost, rc.Metadata["cost"], rc.Metadata["cost_source"], tc.wantCost, tc.wantSource)
		}
	}
}
