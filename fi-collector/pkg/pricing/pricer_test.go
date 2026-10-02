package pricing

import (
	"context"
	"log/slog"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

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
	if _, ok := p.TokenCost(context.Background(), "org", "", 100, 100); ok {
		t.Fatal("empty model must not price")
	}
	if _, ok := p.TokenCost(context.Background(), "org", "gpt-4o", 0, 0); ok {
		t.Fatal("zero tokens must not price")
	}
}

func TestTokenCost_LitellmFirstThenCustom(t *testing.T) {
	db := &fakeDB{row: fakeRow{in: 100, out: 100}} // absurd custom prices
	p := newTestPricer(t, db)

	// Known litellm model: table wins, custom never queried.
	c, ok := p.TokenCost(context.Background(), "org", "gpt-4o", 1000, 500)
	if !ok || c < 0.00749 || c > 0.00751 {
		t.Fatalf("litellm price expected: %v ok=%v", c, ok)
	}
	if db.calls != 0 {
		t.Fatal("custom pricing must not be consulted for litellm-known models")
	}

	// Unknown model falls through to custom.
	c, ok = p.TokenCost(context.Background(), "org", "acme-v1", 1000, 0)
	if !ok || c != 1000*(100.0/1000) {
		t.Fatalf("custom price expected: %v ok=%v", c, ok)
	}
}

func TestTokenCost_NoCustomConfigured(t *testing.T) {
	p := newTestPricer(t, nil) // custom == nil (e.g. no PG configured)
	if _, ok := p.TokenCost(context.Background(), "org", "acme-v1", 10, 10); ok {
		t.Fatal("unknown model without custom pricing must return ok=false")
	}
}

func TestTokenCost_CustomConfiguredButNoOrgID(t *testing.T) {
	// custom != nil but orgID == "" — table-miss model must return (0,false)
	// without ever querying the DB (Pricer's `orgID != ""` gate).
	db := &fakeDB{row: fakeRow{in: 100, out: 100}}
	p := newTestPricer(t, db)
	if _, ok := p.TokenCost(context.Background(), "", "acme-v1", 10, 10); ok {
		t.Fatal("want ok=false when orgID is empty, even with custom pricing configured")
	}
	if db.calls != 0 {
		t.Fatalf("DB must not be queried when orgID is empty, got %d calls", db.calls)
	}
}

func TestTokenCost_CustomNotFound(t *testing.T) {
	db := &fakeDB{row: fakeRow{err: pgx.ErrNoRows}}
	p := newTestPricer(t, db)
	if _, ok := p.TokenCost(context.Background(), "org", "ghost", 10, 10); ok {
		t.Fatal("want ok=false when neither table nor custom knows the model")
	}
}
