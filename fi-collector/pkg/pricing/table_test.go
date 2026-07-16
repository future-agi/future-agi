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
