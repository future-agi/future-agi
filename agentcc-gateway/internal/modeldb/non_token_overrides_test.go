package modeldb

import (
	"encoding/json"
	"testing"

	"gopkg.in/yaml.v3"
)

// Decode the operator-facing YAML rather than constructing new Go fields, so
// the regression also compiles against a schema that silently drops them.
func TestNonTokenOverrideSchemaAndEffectivePrices(t *testing.T) {
	var overrides map[string]ModelOverride
	if err := yaml.Unmarshal([]byte(`
synthetic-units:
  pricing:
    input_per_query: 0.017
    per_page: 0.023
    output_per_image: 0.041
    output_per_second: 0.059
`), &overrides); err != nil {
		t.Fatal(err)
	}
	db := New(nil, overrides)
	price, ok := db.GetPricing("synthetic-units")
	if !ok {
		t.Fatal("missing configured model")
	}
	encoded, err := json.Marshal(price)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]float64
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatal(err)
	}
	for field, want := range map[string]float64{"input_per_query": 0.017, "per_page": 0.023, "output_per_image": 0.041, "output_per_second": 0.059} {
		if fields[field] != want {
			t.Errorf("effective %s = %v, want %v", field, fields[field], want)
		}
	}
	if page, _, ok := db.OCRPrices("", "synthetic-units"); !ok || page != 0.023 {
		t.Errorf("configured OCR price = %v/%v", page, ok)
	}
	if image, ok := db.ImagePrice("synthetic-units", "1024x1024", "standard"); !ok || image != 0.041 {
		t.Errorf("configured image price = %v/%v", image, ok)
	}
	if prices, ok := db.Prices("", "synthetic-units"); !ok || prices.PerQuery != 0.017 {
		t.Errorf("configured per-query price = %+v/%v", prices, ok)
	}
}

func TestSearchConfiguredPricePrecedesBundledDefault(t *testing.T) {
	for _, tc := range []struct {
		name, provider, price string
		want                  float64
		ok                    bool
	}{
		{"custom", "synthetic-search", "0.017", 0.017, true},
		{"bundled_override", "perplexity", "0.019", 0.019, true},
		{"zero_is_not_bundled", "perplexity", "0", 0, false},
		{"negative_is_not_bundled", "perplexity", "-1", 0, false},
		{"nan_is_not_bundled", "perplexity", ".nan", 0, false},
		{"infinity_is_not_bundled", "perplexity", ".inf", 0, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var overrides map[string]ModelOverride
			if err := yaml.Unmarshal([]byte(SearchPriceKey(tc.provider)+":\n  pricing:\n    input_per_query: "+tc.price+"\n"), &overrides); err != nil {
				t.Fatal(err)
			}
			db := New(BundledModels, overrides)
			if got, ok := db.SearchPrice(tc.provider); got != tc.want || ok != tc.ok {
				t.Fatalf("SearchPrice = %v/%v, want %v/%v", got, ok, tc.want, tc.ok)
			}
		})
	}
	// An override of unrelated metadata must not erase the bundled price.
	db := New(BundledModels, map[string]ModelOverride{"perplexity/search": {Provider: "perplexity"}})
	if got, ok := db.SearchPrice("perplexity"); !ok || got != 0.005 {
		t.Fatalf("bundled fallback = %v/%v", got, ok)
	}
	// Never strip the provider prefix and borrow another search entry.
	var generic map[string]ModelOverride
	if err := yaml.Unmarshal([]byte("search:\n  pricing:\n    input_per_query: 0.099\n"), &generic); err != nil {
		t.Fatal(err)
	}
	if _, ok := New(nil, generic).SearchPrice("synthetic-search"); ok {
		t.Fatal("borrowed an unqualified search price")
	}
}

func TestImageConfiguredPricePrecedesBundledSizePrice(t *testing.T) {
	var overrides map[string]ModelOverride
	if err := yaml.Unmarshal([]byte("dall-e-3:\n  pricing:\n    output_per_image: 0.017\n"), &overrides); err != nil {
		t.Fatal(err)
	}
	db := New(BundledModels, overrides)
	if price, ok := db.ImagePrice("dall-e-3", "1024x1024", "hd"); !ok || price != 0.017 {
		t.Fatalf("bundled size price defeated configured price: %v/%v", price, ok)
	}
}
