package pricing

import (
	"context"
	"log/slog"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"go.opentelemetry.io/collector/pdata/ptrace"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
)

func observePricingContext() context.Context {
	resolved := &auth.ResolveResult{WorkspaceID: "workspace"}
	resolved.SetProjectTypesInWorkspace("workspace", map[string]string{"project": auth.ProjectTypeObserve, "experiment": "experiment"})
	return WithProjectTypes(auth.WithResolvedContext(context.Background(), resolved, ""), pricingTraces("project", "experiment"))
}

func pricingTraces(ids ...string) ptrace.Traces {
	td := ptrace.NewTraces()
	for _, id := range ids {
		r := td.ResourceSpans().AppendEmpty()
		r.Resource().Attributes().PutStr("fi.project_id", id)
		r.Resource().Attributes().PutStr("project_type", "observe")
		r.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("test")
	}
	return td
}

func TestTokenCostUsesRequestSnapshot(t *testing.T) {
	resolved := &auth.ResolveResult{WorkspaceID: "ws"}
	resolved.SetProjectTypesInWorkspace("ws", map[string]string{"p": auth.ProjectTypeObserve})
	ctx := auth.WithResolvedContext(context.Background(), resolved, "")
	td := pricingTraces("p", "p", "unknown")
	marshaler := &ptrace.ProtoMarshaler{}
	before, err := marshaler.MarshalTraces(td)
	if err != nil {
		t.Fatal(err)
	}
	batch := WithProjectTypes(ctx, td)
	p := newTestPricer(t, &fakeDB{row: fakeRow{in: 2.5}})
	price := func(c context.Context, id string, want float64) {
		t.Helper()
		got, ok := p.TokenCost(c, "org", "custom", 1_000_000, 0, id)
		if !ok || got != want {
			t.Fatalf("%s: got %v, found %v; want %v", id, got, ok, want)
		}
	}
	price(batch, "p", 2.5)
	resolved.SetProjectTypesInWorkspace("ws", map[string]string{"p": "experiment", "unknown": auth.ProjectTypeObserve})
	price(batch, "p", 2.5)
	price(batch, "unknown", 2500)
	fresh := WithProjectTypes(ctx, td)
	price(fresh, "p", 2500)
	price(fresh, "unknown", 2.5)
	after, err := marshaler.MarshalTraces(td)
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatal("pricing changed the accepted payload")
	}
}

func newTestPricer(t *testing.T, db rowQuerier) *Pricer {
	tbl, err := LoadTable("testdata/prices_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	var custom *CustomPricing
	if db != nil {
		custom = NewCustomPricing(db, time.Hour, slog.Default())
	}
	return New(tbl, custom)
}

func TestTokenCost_GateConditions(t *testing.T) {
	p := newTestPricer(t, nil)
	// Django: `if model and (prompt > 0 or completion > 0)` — else cost 0.
	if _, ok := p.TokenCost(observePricingContext(), "org", "", 100, 100, "project"); ok {
		t.Fatal("empty model must not price")
	}
	if _, ok := p.TokenCost(observePricingContext(), "org", "gpt-4o", 0, 0, "project"); ok {
		t.Fatal("zero tokens must not price")
	}
}

func TestTokenCost_LitellmFirstThenCustom(t *testing.T) {
	db := &fakeDB{row: fakeRow{in: 100, out: 100}} // absurd custom prices
	p := newTestPricer(t, db)

	// Known litellm model: table wins, custom never queried.
	c, ok := p.TokenCost(observePricingContext(), "org", "gpt-4o", 1000, 500, "project")
	if !ok || c < 0.00749 || c > 0.00751 {
		t.Fatalf("litellm price expected: %v ok=%v", c, ok)
	}
	if db.calls != 0 {
		t.Fatal("custom pricing must not be consulted for litellm-known models")
	}

	// Unknown model falls through to custom.
	c, ok = p.TokenCost(observePricingContext(), "org", "acme-v1", 1000, 0, "project")
	if !ok || c != 0.10 {
		t.Fatalf("custom price expected: %v ok=%v", c, ok)
	}
	if got, ok := p.TokenCost(observePricingContext(), "org", "acme-v1", 1000, 0, "experiment"); !ok || got != 100 {
		t.Fatalf("experiment custom price: got %v ok=%v, want 100", got, ok)
	}
}

func TestTokenCost_NoCustomConfigured(t *testing.T) {
	p := newTestPricer(t, nil) // custom == nil (e.g. no PG configured)
	if _, ok := p.TokenCost(observePricingContext(), "org", "acme-v1", 10, 10, "project"); ok {
		t.Fatal("unknown model without custom pricing must return ok=false")
	}
}

func TestTokenCost_CustomConfiguredButNoOrgID(t *testing.T) {
	// custom != nil but orgID == "" — table-miss model must return (0,false)
	// without ever querying the DB (Pricer's `orgID != ""` gate).
	db := &fakeDB{row: fakeRow{in: 100, out: 100}}
	p := newTestPricer(t, db)
	if _, ok := p.TokenCost(observePricingContext(), "", "acme-v1", 10, 10, "project"); ok {
		t.Fatal("want ok=false when orgID is empty, even with custom pricing configured")
	}
	if db.calls != 0 {
		t.Fatalf("DB must not be queried when orgID is empty, got %d calls", db.calls)
	}
}

func TestTokenCost_CustomNotFound(t *testing.T) {
	db := &fakeDB{row: fakeRow{err: pgx.ErrNoRows}}
	p := newTestPricer(t, db)
	if _, ok := p.TokenCost(observePricingContext(), "org", "ghost", 10, 10, "project"); ok {
		t.Fatal("want ok=false when neither table nor custom knows the model")
	}
}

// The prefix fallback runs only after the exact table and the org's custom
// price both miss, and an org's custom price for the exact name wins.
func TestTokenCost_ProviderPrefixedFallback(t *testing.T) {
	// No custom pricing: openai/gpt-4o prices as gpt-4o.
	p := newTestPricer(t, nil)
	c, ok := p.TokenCost(observePricingContext(), "org", "openai/gpt-4o", 1000, 500, "project")
	if !ok || c < 0.00749 || c > 0.00751 {
		t.Fatalf("fallback price expected: %v ok=%v", c, ok)
	}

	// Custom pricing knows the exact name: it wins over the fallback.
	db := &fakeDB{row: fakeRow{in: 100, out: 100}}
	p = newTestPricer(t, db)
	c, ok = p.TokenCost(observePricingContext(), "org", "openai/gpt-4o", 1000, 0, "project")
	if !ok || c != 0.10 {
		t.Fatalf("custom price expected: %v ok=%v", c, ok)
	}

	// Custom pricing misses: the fallback still prices, after one lookup.
	db = &fakeDB{row: fakeRow{err: pgx.ErrNoRows}}
	p = newTestPricer(t, db)
	c, ok = p.TokenCost(observePricingContext(), "org", "openai/gpt-4o", 1000, 500, "project")
	if !ok || c < 0.00749 || c > 0.00751 {
		t.Fatalf("fallback after custom miss expected: %v ok=%v", c, ok)
	}
	if db.calls != 1 {
		t.Fatalf("custom lookups = %d, want 1 (for the exact name only)", db.calls)
	}

	// A prefix that isn't the entry's provider stays unpriced.
	if c, ok := p.TokenCost(observePricingContext(), "org", "my-proxy/gpt-4o", 1000, 500, "project"); ok {
		t.Fatalf("my-proxy/gpt-4o must not price, got %v", c)
	}
}
