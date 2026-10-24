package cost

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func managedAttempt() *models.Attempt {
	return &models.Attempt{CredentialType: models.CredentialManaged, ProcessingFeeBps: 320}
}

func byokAttempt() *models.Attempt {
	return &models.Attempt{CredentialType: models.CredentialBYOK}
}

func usd(v float64) *float64 { return &v }

func rawDetails(t *testing.T, v map[string]int) *json.RawMessage {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	raw := json.RawMessage(b)
	return &raw
}

func TestPriceAttemptIntegerMicrosRounding(t *testing.T) {
	cases := []struct {
		name                     string
		u                        *models.Usage
		card                     models.PriceCard
		markupBps                int
		wantUpstream, wantBilled int64
		wantSource               string
	}{
		// 0.0000025 USD is 2.4999999999999996 micros in float64: the snap to
		// a millionth of a micro keeps it a half, which rounds up.
		{"reported half micro rounds up", &models.Usage{Cost: usd(0.0000025)}, models.PriceCard{}, 500, 3, 3, "provider_reported"},
		{"reported just under a half rounds down", &models.Usage{Cost: usd(0.0000024)}, models.PriceCard{}, 500, 2, 2, "provider_reported"},
		// 1 token at $0.5/M is 0.5 micros: half up gives 1, billed 1 × 1.05 = 1.05 → 1.
		{"token half micro", &models.Usage{PromptTokens: 1}, models.PriceCard{Prompt: 0.5e-6, Source: models.PriceSourceCatalog}, 500, 1, 1, "catalog"},
		// 10 micros × 1.05 = 10.5 → 11 (half up), never banker's 10.
		{"markup half up", &models.Usage{Cost: usd(0.00001)}, models.PriceCard{}, 500, 10, 11, "provider_reported"},
		// 7000 micros × 1.05 = 7350 exactly.
		{"exact markup", &models.Usage{Cost: usd(0.007)}, models.PriceCard{}, 500, 7000, 7350, "provider_reported"},
		{"zero tokens priced at zero", &models.Usage{}, models.PriceCard{Prompt: 1e-6, Source: models.PriceSourceModelDB}, 500, 0, 0, "modeldb"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			up, billed, _, source, ok := PriceAttempt(managedAttempt(), c.u, c.card, c.markupBps, 0)
			if !ok || up != c.wantUpstream || billed != c.wantBilled || source != c.wantSource {
				t.Fatalf("PriceAttempt = (%d, %d, %q, %v); want (%d, %d, %q, true)", up, billed, source, ok, c.wantUpstream, c.wantBilled, c.wantSource)
			}
		})
	}
	if _, _, _, source, ok := PriceAttempt(managedAttempt(), &models.Usage{PromptTokens: 10}, models.PriceCard{}, 500, 0); ok || source != "unpriced" {
		t.Fatalf("an empty card must leave the attempt unpriced; got %q, %v", source, ok)
	}
}

func TestCacheWriteAndReasoningPriced(t *testing.T) {
	card := models.PriceCard{
		Prompt: 3e-6, Completion: 15e-6, CacheRead: 0.3e-6, CacheWrite: 3.75e-6, Reasoning: 20e-6,
		Source: models.PriceSourceCatalog,
	}
	// prompt_tokens includes the cached and cache-write tokens; completion
	// includes the reasoning tokens (the OpenAI and OpenRouter convention).
	u := &models.Usage{
		PromptTokens: 1000, CompletionTokens: 500,
		PromptTokensDetails:     rawDetails(t, map[string]int{"cached_tokens": 600, "cache_write_tokens": 200}),
		CompletionTokensDetails: rawDetails(t, map[string]int{"reasoning_tokens": 100}),
	}
	// 200 × 3 + 600 × 0.3 + 200 × 3.75 + 400 × 15 + 100 × 20 = 600 + 180 + 750 + 6000 + 2000 = 9530 micros
	up, _, _, _, ok := PriceAttempt(byokAttempt(), u, card, 0, 0)
	if !ok || up != 9530 {
		t.Fatalf("upstream = %d, want 9530", up)
	}

	// Without separate cache-write and reasoning prices, those tokens cost
	// the plain input and output rates.
	plain := models.PriceCard{Prompt: 3e-6, Completion: 15e-6, CacheRead: 0.3e-6, Source: models.PriceSourceCatalog}
	// 200 × 3 + 600 × 0.3 + 200 × 3 + 500 × 15 = 600 + 180 + 600 + 7500 = 8880
	if up, _, _, _, _ := PriceAttempt(byokAttempt(), u, plain, 0, 0); up != 8880 {
		t.Fatalf("upstream without cache-write and reasoning prices = %d, want 8880", up)
	}

	// Anthropic's input_tokens excludes cache reads and writes: 200 uncached
	// tokens with 600 read and 200 written is the same 1,000-token prompt.
	anthropic := &models.Usage{PromptTokens: 200, CompletionTokens: 500,
		PromptTokensDetails:     rawDetails(t, map[string]int{"cached_tokens": 600, "cache_write_tokens": 200}),
		CompletionTokensDetails: rawDetails(t, map[string]int{"reasoning_tokens": 100}),
	}
	if up, _, _, _, _ := PriceAttempt(byokAttempt(), anthropic, card, 0, 0); up != 9530 {
		t.Fatalf("upstream for Anthropic-shaped counts = %d, want 9530", up)
	}

	// The attempt's own token counts stand in for usage details the
	// response did not carry.
	a := byokAttempt()
	a.CachedTokens, a.CacheWriteTokens, a.ReasoningTokens = 600, 200, 100
	if up, _, _, _, _ := PriceAttempt(a, &models.Usage{PromptTokens: 1000, CompletionTokens: 500}, card, 0, 0); up != 9530 {
		t.Fatalf("upstream from the attempt's token counts = %d, want 9530", up)
	}
}

func TestRequestFeePriced(t *testing.T) {
	card := models.PriceCard{Prompt: 1e-6, Completion: 2e-6, Request: 0.005, WebSearch: 0.01, Source: models.PriceSourceCatalog}
	u := &models.Usage{PromptTokens: 100, CompletionTokens: 50, ServerToolUse: &models.ServerToolUsage{WebSearchRequests: 2}}
	// 100 + 100 tokens' micros + 5000 per request + 2 × 10000 per web search
	up, _, _, _, ok := PriceAttempt(byokAttempt(), u, card, 0, 0)
	if !ok || up != 25200 {
		t.Fatalf("upstream = %d, want 25200", up)
	}
	// A request fee alone prices a model.
	if up, _, _, _, ok := PriceAttempt(byokAttempt(), &models.Usage{}, models.PriceCard{Request: 0.005, Source: models.PriceSourceCatalog}, 0, 0); !ok || up != 5000 {
		t.Fatalf("request-fee-only upstream = %d, %v; want 5000", up, ok)
	}
}

func TestByokNoMarkup(t *testing.T) {
	up, billed, landed, source, ok := PriceAttempt(byokAttempt(), &models.Usage{Cost: usd(0.0068)}, models.PriceCard{}, 500, 550)
	if !ok || up != 6800 || billed != 6800 || source != "provider_reported" {
		t.Fatalf("BYOK = (%d, %d, %q); want billed equal to upstream 6800", up, billed, source)
	}
	if landed != 0 {
		t.Fatalf("BYOK landed = %d, want 0", landed)
	}
}

func TestLandedCostUsesUpstreamFeeBps(t *testing.T) {
	up, billed, landed, _, ok := PriceAttempt(managedAttempt(), &models.Usage{Cost: usd(1.0)}, models.PriceCard{}, 500, 550)
	// 1,000,000 upstream: billed 1,050,000; landed 1,055,000 upstream part +
	// 1,050,000 × 3.2% = 33,600 processing part.
	if !ok || up != 1_000_000 || billed != 1_050_000 || landed != 1_088_600 {
		t.Fatalf("PriceAttempt = (%d, %d, %d); want (1000000, 1050000, 1088600)", up, billed, landed)
	}
	// Each part rounds half up on its own: 333 micros at 550 bps is 351.315
	// → 351, plus billed 350 (349.65 → 350) at 320 bps 11.2 → 11.
	if _, billed, landed, _, _ := PriceAttempt(managedAttempt(), &models.Usage{Cost: usd(0.000333)}, models.PriceCard{}, 500, 550); billed != 350 || landed != 362 {
		t.Fatalf("billed, landed = %d, %d; want 350, 362", billed, landed)
	}
}

func TestByokLandedZero(t *testing.T) {
	a := byokAttempt()
	a.ProcessingFeeBps = 320 // even when the attempt carries a processing rate
	if _, _, landed, _, _ := PriceAttempt(a, &models.Usage{PromptTokens: 1000}, models.PriceCard{Prompt: 1e-6, Source: models.PriceSourceModelDB}, 500, 550); landed != 0 {
		t.Fatalf("BYOK landed = %d, want 0: the customer pays the upstream", landed)
	}
}
