package pricing

import "testing"

func TestTableCost(t *testing.T) {
	tbl, err := LoadTable("testdata/prices_fixture.json")
	if err != nil {
		t.Fatal(err)
	}

	// 1000 in + 500 out on gpt-4o: 1000*2.5e-6 + 500*1e-5 = 0.0075
	c, ok := tbl.Cost("gpt-4o", 1000, 500)
	if !ok || c < 0.00749 || c > 0.00751 {
		t.Fatalf("want 0.0075, got %v ok=%v", c, ok)
	}

	// Unknown model → not ok (falls through to custom pricing, like Django).
	if _, ok := tbl.Cost("acme-custom-model", 10, 10); ok {
		t.Fatal("unknown model must return ok=false")
	}

	// Model key EXISTS but has no per-token prices → ok=true, cost 0.
	// Django's gate was `model_cost.get(model)` truthiness — key existence —
	// so such models never fell through to CustomAIModel. Keep that.
	c, ok = tbl.Cost("no-price-model", 10, 10)
	if !ok || c != 0 {
		t.Fatalf("priced-at-zero model: got %v ok=%v", c, ok)
	}

	// sample_spec is documentation noise in the litellm file — must be skipped.
	if _, ok := tbl.Cost("sample_spec", 10, 10); ok {
		t.Fatal("sample_spec must be excluded from the table")
	}

	// Malformed entry (string-typed input_cost_per_token) is skipped, not
	// fatal — gpt-4o (and the rest of the file) still loads and prices.
	if tbl.Skipped != 1 {
		t.Fatalf("want Skipped=1 for the malformed fixture entry, got %d", tbl.Skipped)
	}
	if _, ok := tbl.Cost("malformed-model", 10, 10); ok {
		t.Fatal("malformed-model must not be present in the table")
	}
}

func TestTableCost_Tiered128k(t *testing.T) {
	tbl, err := LoadTable("testdata/prices_fixture.json")
	if err != nil {
		t.Fatal(err)
	}

	// Below threshold: base rates apply on both sides.
	// 1000*0.000001 + 500*0.000002 = 0.002
	c, ok := tbl.Cost("tiered-model", 1000, 500)
	if !ok || c < 0.00199 || c > 0.00201 {
		t.Fatalf("below-threshold: want ~0.002, got %v ok=%v", c, ok)
	}

	// Above threshold (prompt > 128_000): ALL tokens on both sides price at
	// the above-128k rate, not just the marginal tokens past the boundary.
	// 200000*0.000003 + 500*0.000004 = 0.6 + 0.002 = 0.602
	c, ok = tbl.Cost("tiered-model", 200_000, 500)
	if !ok || c < 0.6019 || c > 0.6021 {
		t.Fatalf("above-threshold: want ~0.602, got %v ok=%v", c, ok)
	}
}

func TestLoadEmbedded(t *testing.T) {
	tbl, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := tbl.Cost("gpt-4o", 1, 1); !ok {
		t.Fatal("embedded snapshot must know gpt-4o")
	}
}

// "provider/model" names fall back to the bare entry only when the prefix is
// that entry's litellm_provider.
func TestTableProviderPrefixedCost(t *testing.T) {
	tbl, err := LoadTable("testdata/prices_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	// gpt-4o is an "openai" entry: 1000*2.5e-6 + 500*1e-5 = 0.0075.
	c, ok := tbl.ProviderPrefixedCost("openai/gpt-4o", 1000, 500)
	if !ok || c < 0.00749 || c > 0.00751 {
		t.Fatalf("openai/gpt-4o: want 0.0075, got %v ok=%v", c, ok)
	}
	for _, model := range []string{
		"azure/gpt-4o",       // gpt-4o belongs to openai, not azure
		"my-proxy/gpt-4o",    // a reseller or self-hosted prefix
		"OpenAI/gpt-4o",      // provider names are matched exactly
		"openai/acme-model",  // no bare entry
		"gpt-4o",             // no prefix (exact lookup is Cost's job)
		"/gpt-4o", "openai/", // empty halves
		"openai/openai/gpt-4o", // only one prefix is dropped
	} {
		if c, ok := tbl.ProviderPrefixedCost(model, 1000, 500); ok {
			t.Errorf("%s: want ok=false, got %v", model, c)
		}
	}
}

// OpenRouter slugs against the embedded litellm snapshot: openai/* prices as
// the OpenAI entry; vendors whose OpenRouter slug isn't a litellm key of the
// same provider stay unpriced (OpenRouter sends its own cost for those).
func TestTableProviderPrefixedCost_EmbeddedOpenRouterSlugs(t *testing.T) {
	tbl, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	for _, model := range []string{"gpt-4o", "gpt-4o-mini", "gpt-4"} {
		want, ok := tbl.Cost(model, 1000, 500)
		if !ok || want == 0 {
			t.Fatalf("embedded table must price %s", model)
		}
		got, ok := tbl.ProviderPrefixedCost("openai/"+model, 1000, 500)
		if !ok || got != want {
			t.Errorf("openai/%s = %v ok=%v, want %v", model, got, ok, want)
		}
	}
	for _, model := range []string{
		"anthropic/claude-3.5-sonnet", // litellm keys it claude-3-5-sonnet-*
		"google/gemini-2.5-flash",     // the bare entry is a vertex_ai entry
		"meta-llama/llama-3.3-70b-instruct",
	} {
		if c, ok := tbl.ProviderPrefixedCost(model, 1000, 500); ok {
			t.Errorf("%s: want ok=false, got %v", model, c)
		}
	}
}
