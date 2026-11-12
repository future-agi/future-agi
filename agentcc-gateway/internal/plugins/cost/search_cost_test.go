package cost

import (
	"bytes"
	"context"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Ported from dev 68f20bd8c8 (endpoint_cost_test.go TestSearchCostPerRequest,
// which stays deleted on the candidate): a search is priced per query from
// the bundled "<search provider>/search" entry, and a search with no price is
// marked unpriced, never recorded as free.

func searchRC(provider string, answered bool) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.EndpointType = "search"
	rc.SearchRequest = &models.SearchRequest{SearchProvider: provider}
	if answered {
		rc.SearchResponse = &models.SearchResponse{}
	}
	return rc
}

func TestSearchCostPerRequest(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)

	// The bundled table prices perplexity/search at input_cost_per_query 0.005.
	price, ok := db.SearchPrice("perplexity")
	if !ok || price != 0.005 {
		t.Fatalf("bundled perplexity/search price = %v, %v; want 0.005", price, ok)
	}
	rc := searchRC("perplexity", true)
	defer rc.Release()
	p.ProcessResponse(context.Background(), rc)
	if got := rc.Metadata["cost"]; got != "0.005000" {
		t.Fatalf("cost = %q, want one query at 0.005000", got)
	}
	if got := rc.Metadata["cost_source"]; got != string(models.CostSourceModelDB) {
		t.Fatalf("cost_source = %q, want %q", got, models.CostSourceModelDB)
	}

	// No price: unpriced, not free.
	none := searchRC("no-such-search", true)
	defer none.Release()
	p.ProcessResponse(context.Background(), none)
	if got := none.Metadata["cost"]; got != "" {
		t.Fatalf("cost = %q for an unpriced search", got)
	}
	if got := none.Metadata["cost_source"]; got != CostSourceUnpriced {
		t.Fatalf("cost_source = %q, want %q", got, CostSourceUnpriced)
	}

	// No response: nothing was used.
	unanswered := searchRC("no-such-search", false)
	defer unanswered.Release()
	p.ProcessResponse(context.Background(), unanswered)
	if unanswered.Metadata["cost_source"] != "" {
		t.Fatalf("cost_source = %q for a search that returned nothing", unanswered.Metadata["cost_source"])
	}
}

// A tiered-only entry has no flat per-query price: the search is unpriced.
func TestSearchCostTieredOnlyIsUnpriced(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	if _, ok := db.SearchPrice("exa_ai"); ok {
		t.Skip("the bundled exa_ai/search entry has a flat per-query price; this test assumes it is tiered only")
	}
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	rc := searchRC("exa_ai", true)
	defer rc.Release()
	p.ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "" || rc.Metadata["cost_source"] != CostSourceUnpriced {
		t.Fatalf("cost = %q source = %q; want unpriced", rc.Metadata["cost"], rc.Metadata["cost_source"])
	}
}

// The unpriced-search warning names the "<search provider>/search" entry, once
// per provider: a search names no model, so the warnings must not collapse
// into one.
func TestSearchUnpricedWarnsPerProvider(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, provider := range []string{"no-such-search", "no-such-search", "other-search"} {
		rc := searchRC(provider, true)
		p.ProcessResponse(context.Background(), rc)
		rc.Release()
	}
	out := buf.String()
	if n := strings.Count(out, "model=no-such-search/search"); n != 1 {
		t.Fatalf("warned %d times for no-such-search/search, want once\n%s", n, out)
	}
	if n := strings.Count(out, "model=other-search/search"); n != 1 {
		t.Fatalf("warned %d times for other-search/search, want once\n%s", n, out)
	}
	if !strings.Contains(out, "input_cost_per_query") {
		t.Fatalf("warning should name the per-query price to set\n%s", out)
	}
}

// Managed admission stays closed to search and decision calls: neither is a
// priced endpoint kind for Future AGI credits.
func TestSearchAndDecisionAreNotManagedPriced(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	if p.Priced("search", "perplexity/search") {
		t.Fatal("search is priced for managed admission")
	}
	if p.Priced("decision", "jev-latest") {
		t.Fatal("decision is priced for managed admission")
	}
}
