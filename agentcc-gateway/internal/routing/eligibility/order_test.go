package eligibility

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

func latency(ms float64) func(*catalog.Endpoint) {
	return func(e *catalog.Endpoint) { e.LatencyP50Ms = &ms }
}

func throughput(tps float64) func(*catalog.Endpoint) {
	return func(e *catalog.Endpoint) { e.ThroughputP50 = &tps }
}

// orderedIn is a managed candidate with three endpoints: cheap is the
// best-ranked, fast has the lowest latency and the most throughput.
func orderedIn(prefs *models.ProviderPrefs) Input {
	in := managedIn(nil, entry(
		ep("cheap", 0.5e-6, latency(900), throughput(20)),
		ep("mid", 1e-6, latency(400), throughput(60)),
		ep("fast", 2e-6, latency(100), throughput(200)),
	))
	in.Prefs = prefs
	return in
}

func TestOrderFieldsRegistered(t *testing.T) {
	for _, f := range []string{reqopts.FieldProviderOrder, reqopts.FieldProviderSort, reqopts.FieldProviderAllowFallbacks} {
		if !reqopts.Supported(f) {
			t.Errorf("%s is not registered", f)
		}
	}
}

// provider.order puts the endpoints it names first, in its order; the rest
// keep their rank after them.
func TestProviderOrderLeadsTheEndpointSet(t *testing.T) {
	in := orderedIn(&models.ProviderPrefs{Order: []string{"fast", "mid"}})
	v := Check(&in)
	if !v.OK {
		t.Fatalf("refused: %+v", v)
	}
	if got := tags(v.EndpointSet(MaxEndpointSet)); got != "fast,mid,cheap" {
		t.Fatalf("set = %s, want fast,mid,cheap", got)
	}
}

// provider.allow_fallbacks false keeps only the endpoints order names, and
// refuses the candidate when none of its endpoints is named.
func TestProviderAllowFallbacksFalse(t *testing.T) {
	no := false
	in := orderedIn(&models.ProviderPrefs{Order: []string{"mid"}, AllowFallbacks: &no})
	v := Check(&in)
	if !v.OK || tags(v.EndpointSet(MaxEndpointSet)) != "mid" {
		t.Fatalf("verdict %+v set %s, want only mid", v, tags(v.EndpointSet(MaxEndpointSet)))
	}

	in = orderedIn(&models.ProviderPrefs{Order: []string{"elsewhere"}, AllowFallbacks: &no})
	if v := Check(&in); v.OK || v.Code != CodeNoProviderMatches || StatusFor(v.Code) != http.StatusNotFound {
		t.Fatalf("verdict %+v, want 404 no_provider_matches", v)
	}

	// No order: the first-ranked endpoint only.
	in = orderedIn(&models.ProviderPrefs{AllowFallbacks: &no})
	if v := Check(&in); !v.OK || tags(v.EndpointSet(MaxEndpointSet)) != "cheap" {
		t.Fatalf("set %s, want only the best-ranked cheap", tags(v.EndpointSet(MaxEndpointSet)))
	}

	// allow_fallbacks true leaves the set as order and rank make it.
	yes := true
	in = orderedIn(&models.ProviderPrefs{Order: []string{"mid"}, AllowFallbacks: &yes})
	if v := Check(&in); !v.OK || tags(v.EndpointSet(MaxEndpointSet)) != "mid,cheap,fast" {
		t.Fatalf("set %s, want mid,cheap,fast", tags(v.EndpointSet(MaxEndpointSet)))
	}
}

// provider.sort ranks the endpoints by the figure it names, from the
// catalogue's data.
func TestProviderSortRanksEndpoints(t *testing.T) {
	for by, want := range map[string]string{
		"price":      "cheap,mid,fast",
		"latency":    "fast,mid,cheap",
		"throughput": "fast,mid,cheap",
	} {
		in := orderedIn(&models.ProviderPrefs{Sort: &models.ProviderSort{By: by}})
		v := Check(&in)
		if !v.OK {
			t.Fatalf("%s: refused %+v", by, v)
		}
		if got := tags(v.EndpointSet(MaxEndpointSet)); got != want {
			t.Errorf("sort %s: set = %s, want %s", by, got, want)
		}
	}
	// Order wins over sort for the endpoints it names.
	in := orderedIn(&models.ProviderPrefs{Order: []string{"cheap"}, Sort: &models.ProviderSort{By: "latency"}})
	if v := Check(&in); tags(v.EndpointSet(MaxEndpointSet)) != "cheap,fast,mid" {
		t.Errorf("order then sort = %s, want cheap,fast,mid", tags(v.EndpointSet(MaxEndpointSet)))
	}
}

// A sort the gateway has no figures for, or a managed model with no list of
// its providers, is refused with 400 rather than served unsorted.
func TestProviderSortRefusedWhenItCannotBeHonoured(t *testing.T) {
	in := managedIn(nil, entry(ep("a", 1e-6), ep("b", 2e-6)))
	in.Prefs = &models.ProviderPrefs{Sort: &models.ProviderSort{By: "latency"}}
	if v := Check(&in); v.OK || v.Code != CodeProviderPreferenceUnavailable || StatusFor(v.Code) != http.StatusBadRequest {
		t.Fatalf("verdict %+v, want 400 provider_preference_unavailable", v)
	}

	noList := managedIn(nil, entry())
	noList.Prefs = &models.ProviderPrefs{Order: []string{"anthropic"}}
	if v := Check(&noList); v.OK || v.Code != CodeProviderPreferenceUnavailable {
		t.Fatalf("verdict %+v, want provider_preference_unavailable for a model with no endpoint list", v)
	}

	partition := orderedIn(&models.ProviderPrefs{Sort: &models.ProviderSort{By: "price", Partition: "none"}})
	if v := Check(&partition); v.OK || v.Code != CodeProviderPreferenceUnavailable {
		t.Fatalf("verdict %+v, want partition none refused", v)
	}
}

// A BYOK candidate that is not an aggregator is one endpoint: order and
// sort are met as they are; allow_fallbacks false with an order that does
// not name its provider refuses it. A BYOK aggregator applies them itself.
func TestProviderOrderOnBYOK(t *testing.T) {
	no := false
	in := byokIn(nil)
	in.Prefs = &models.ProviderPrefs{Order: []string{"anthropic"}, Sort: &models.ProviderSort{By: "latency"}}
	if v := Check(&in); !v.OK {
		t.Fatalf("refused %+v", v)
	}
	in.Prefs = &models.ProviderPrefs{Order: []string{"anthropic"}, AllowFallbacks: &no}
	if v := Check(&in); v.OK || v.Code != CodeNoProviderMatches {
		t.Fatalf("verdict %+v, want no_provider_matches", v)
	}
	in.Prefs = &models.ProviderPrefs{Order: []string{"OpenAI"}, AllowFallbacks: &no}
	if v := Check(&in); !v.OK {
		t.Fatalf("an order naming the provider was refused: %+v", v)
	}

	agg := byokIn(nil)
	agg.Aggregator = true
	agg.Prefs = &models.ProviderPrefs{Order: []string{"anthropic"}, AllowFallbacks: &no, Sort: &models.ProviderSort{By: "throughput"}}
	if v := Check(&agg); !v.OK {
		t.Fatalf("a BYOK aggregator is forwarded the object and enforces it: %+v", v)
	}
}

// On a candidate with no endpoints (the chat hot path) the rule allocates
// nothing, with or without an order.
func TestProviderOrderRuleAllocationFree(t *testing.T) {
	in := byokIn(nil)
	if allocs := testing.AllocsPerRun(200, func() { _ = Check(&in) }); allocs != 0 {
		t.Fatalf("Check allocates %.0f with no provider preferences", allocs)
	}
	no := false
	in.Prefs = &models.ProviderPrefs{Order: []string{"openai"}, AllowFallbacks: &no}
	if allocs := testing.AllocsPerRun(200, func() { _ = Check(&in) }); allocs != 0 {
		t.Fatalf("Check allocates %.0f with an order the provider meets", allocs)
	}
}
