package pricing

import (
	"context"
	"log/slog"
	"math"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func nearCost(a, b float64) bool { return math.Abs(a-b) < 1e-12 }

func newOpenRouterTestPricer(t *testing.T, db rowQuerier) *Pricer {
	t.Helper()
	tbl, err := LoadTable("testdata/prices_openrouter_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	var custom *CustomPricing
	if db != nil {
		custom = NewCustomPricing(db, time.Hour, slog.Default())
	}
	return New(tbl, custom)
}

func TestTableOpenRouterCost(t *testing.T) {
	tbl, err := LoadTable("testdata/prices_openrouter_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	// 14*1e-6 + 9*5e-6
	for _, slug := range []string{"anthropic/claude-haiku-4.5", "openrouter/anthropic/claude-haiku-4.5"} {
		c, ok := tbl.OpenRouterCost(slug, 14, 9)
		if !ok || !nearCost(c, 5.9e-5) {
			t.Errorf("%s: got %v ok=%v, want 5.9e-05", slug, c, ok)
		}
	}
	for _, slug := range []string{"", "anthropic/claude-unknown", "gpt-4o", "openrouter/"} {
		if c, ok := tbl.OpenRouterCost(slug, 14, 9); ok {
			t.Errorf("%q: want ok=false, got %v", slug, c)
		}
	}
}

// Precedence for OpenRouter traffic: exact table key, then the org's own
// price for the exact name, then OpenRouter's rate for the slug, then the
// bare provider entry.
func TestOpenRouterTokenCostPrecedence(t *testing.T) {
	ctx := context.Background()

	p := newOpenRouterTestPricer(t, nil)
	if _, ok := p.OpenRouterTokenCost(ctx, "org", "", 10, 10); ok {
		t.Fatal("empty model must not price")
	}
	if _, ok := p.OpenRouterTokenCost(ctx, "org", "anthropic/claude-haiku-4.5", 0, 0); ok {
		t.Fatal("zero tokens must not price")
	}

	// No bare same-provider key: only the openrouter/ entry prices it, and
	// TokenCost (non-OpenRouter traffic) still does not.
	c, ok := p.OpenRouterTokenCost(ctx, "org", "anthropic/claude-haiku-4.5", 14, 9)
	if !ok || !nearCost(c, 5.9e-5) {
		t.Fatalf("anthropic/claude-haiku-4.5 = %v ok=%v, want 5.9e-05", c, ok)
	}
	if c, ok := p.TokenCost(ctx, "org", "anthropic/claude-haiku-4.5", 14, 9); ok {
		t.Fatalf("TokenCost must not use the openrouter/ entry, got %v", c)
	}

	// OpenRouter's rate beats the bare provider entry for OpenRouter traffic;
	// TokenCost keeps the bare entry.
	c, ok = p.OpenRouterTokenCost(ctx, "org", "openai/gpt-4o", 1000, 500)
	if want := 1000*3e-6 + 500*1.2e-5; !ok || !nearCost(c, want) {
		t.Fatalf("OpenRouter openai/gpt-4o = %v ok=%v, want %v", c, ok, want)
	}
	c, ok = p.TokenCost(ctx, "org", "openai/gpt-4o", 1000, 500)
	if want := 1000*2.5e-6 + 500*1e-5; !ok || !nearCost(c, want) {
		t.Fatalf("TokenCost openai/gpt-4o = %v ok=%v, want %v", c, ok, want)
	}

	// An exact table key wins over the openrouter/ entry.
	c, ok = p.OpenRouterTokenCost(ctx, "org", "anthropic/exact-slug", 1000, 0)
	if want := 1000 * 7e-6; !ok || !nearCost(c, want) {
		t.Fatalf("exact key = %v ok=%v, want %v", c, ok, want)
	}

	// The org's own price for the exact name wins over the openrouter/ entry.
	db := &fakeDB{row: fakeRow{in: 100, out: 100}}
	p = newOpenRouterTestPricer(t, db)
	c, ok = p.OpenRouterTokenCost(ctx, "org", "anthropic/claude-haiku-4.5", 1000, 0)
	if !ok || c != 1000*(100.0/1000) {
		t.Fatalf("custom price expected: %v ok=%v", c, ok)
	}

	// Custom misses: the openrouter/ entry prices it, after one lookup for
	// the exact name.
	db = &fakeDB{row: fakeRow{err: pgx.ErrNoRows}}
	p = newOpenRouterTestPricer(t, db)
	c, ok = p.OpenRouterTokenCost(ctx, "org", "anthropic/claude-haiku-4.5", 14, 9)
	if !ok || !nearCost(c, 5.9e-5) {
		t.Fatalf("after custom miss = %v ok=%v, want 5.9e-05", c, ok)
	}
	if db.calls != 1 {
		t.Fatalf("custom lookups = %d, want 1", db.calls)
	}

	// Nothing knows the slug.
	if c, ok := p.OpenRouterTokenCost(ctx, "org", "acme/unknown", 10, 10); ok {
		t.Fatalf("acme/unknown must not price, got %v", c)
	}
}

// Against the embedded litellm snapshot, the slugs R2 sent without a sender
// cost price at OpenRouter's rate.
func TestOpenRouterTokenCostEmbeddedSlugs(t *testing.T) {
	tbl, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	p := New(tbl, nil)
	for _, tc := range []struct {
		slug string
		p, c int32
		want float64
	}{
		{"anthropic/claude-haiku-4.5", 14, 9, 14*1e-6 + 9*5e-6},
		{"anthropic/claude-sonnet-4.5", 3412, 286, 3412*3e-6 + 286*1.5e-5},
		{"openai/gpt-4o-mini", 16, 11, 16*1.5e-7 + 11*6e-7},
	} {
		got, ok := p.OpenRouterTokenCost(context.Background(), "org", tc.slug, tc.p, tc.c)
		if !ok || !nearCost(got, tc.want) {
			t.Errorf("%s = %v ok=%v, want %v", tc.slug, got, ok, tc.want)
		}
	}
	if c, ok := p.TokenCost(context.Background(), "org", "anthropic/claude-haiku-4.5", 14, 9); ok {
		t.Errorf("non-OpenRouter TokenCost of anthropic/claude-haiku-4.5 = %v, want unpriced as before", c)
	}
}
