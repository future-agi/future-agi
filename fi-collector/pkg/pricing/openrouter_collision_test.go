package pricing

import (
	"context"
	"testing"
)

// OpenRouter slugs can also be exact keys for a direct provider. The
// OpenRouter path must use the reseller's rate, while TokenCost must keep
// the direct provider's rate. Pin every differently priced collision in the
// embedded snapshot, including the reported 1,000-input/500-output span.
func TestOpenRouterTokenCostEmbeddedProviderCollisions(t *testing.T) {
	tbl, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	p := New(tbl, nil)
	ctx := context.Background()
	for _, tc := range []struct {
		slug           string
		wantOpenRouter float64
		wantDirect     float64
	}{
		{"deepseek/deepseek-chat", 0.000765, 0.000490},
		{"deepseek/deepseek-v3.2", 0.000469, 0.000480},
		{"deepseek/deepseek-r1", 0.001950, 0.001645},
		{"deepseek/deepseek-v4-flash-vision-exp", 0.000550, 0.000900},
		{"deepseek/deepseek-v4-flash", 0.00017556, 0.000900},
	} {
		t.Run(tc.slug, func(t *testing.T) {
			for _, model := range []string{tc.slug, "openrouter/" + tc.slug} {
				got, ok := p.OpenRouterTokenCost(ctx, "org", model, 1000, 500)
				if !ok || !nearCost(got, tc.wantOpenRouter) {
					t.Errorf("OpenRouterTokenCost(%q) = %v ok=%v, want %v", model, got, ok, tc.wantOpenRouter)
				}
			}
			got, ok := p.TokenCost(ctx, "org", tc.slug, 1000, 500)
			if !ok || !nearCost(got, tc.wantDirect) {
				t.Errorf("TokenCost(%q) = %v ok=%v, want %v", tc.slug, got, ok, tc.wantDirect)
			}
		})
	}
}

// Preserve the existing custom-pricing policy: an exact table hit is not
// overridden by an org price. For OpenRouter traffic, that table hit must
// nevertheless use the OpenRouter rate when the slug collides.
func TestOpenRouterTokenCostCollisionKeepsExactTableCustomPrecedence(t *testing.T) {
	for _, model := range []string{"anthropic/exact-slug", "openrouter/anthropic/exact-slug"} {
		t.Run(model, func(t *testing.T) {
			db := &fakeDB{row: fakeRow{in: 100, out: 100}}
			p := newOpenRouterTestPricer(t, db)
			got, ok := p.OpenRouterTokenCost(context.Background(), "org", model, 1000, 0)
			if want := 1000 * 1e-9; !ok || !nearCost(got, want) {
				t.Errorf("OpenRouterTokenCost(%q) = %v ok=%v, want %v", model, got, ok, want)
			}
			if db.calls != 0 {
				t.Errorf("custom lookups = %d, want 0 for an exact table hit", db.calls)
			}
		})
	}
}

// A direct-provider fallback remains available when no OpenRouter rate is
// known. It must not take precedence when the OpenRouter entry does exist.
func TestOpenRouterTokenCostKeepsDirectFallbackWithoutRouterRate(t *testing.T) {
	p := newTestPricer(t, nil)
	for _, model := range []string{"gpt-4o", "openai/gpt-4o"} {
		t.Run(model, func(t *testing.T) {
			if _, ok := p.table.OpenRouterCost(model, 1000, 500); ok {
				t.Fatal("fixture unexpectedly has an OpenRouter rate")
			}
			got, ok := p.OpenRouterTokenCost(context.Background(), "org", model, 1000, 500)
			if want := 0.0075; !ok || !nearCost(got, want) {
				t.Errorf("direct fallback for %q = %v ok=%v, want %v", model, got, ok, want)
			}
		})
	}
}
