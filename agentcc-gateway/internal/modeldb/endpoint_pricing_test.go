package modeldb

import "testing"

// OCR, video and search entries were mapped to chat and then dropped as
// zero-priced chat models, so their per-page and per-second prices never
// loaded. Read from the bundled table, each endpoint's price must be there.
func TestBundledModels_NonTokenPricesLoad(t *testing.T) {
	db := New(BundledModels, nil)

	checks := []struct {
		name string
		cost func() (float64, bool)
	}{
		{"OCR per page", func() (float64, bool) { return db.PageCost("mistral/mistral-ocr-latest", 10) }},
		{"video per second", func() (float64, bool) { return db.VideoCost("gemini/veo-3.1-generate-preview", 5) }},
		{"rerank per query", func() (float64, bool) { return db.QueryCost("azure_ai/cohere-rerank-v3.5", 1) }},
		{"image per pixel, by size and quality", func() (float64, bool) { return db.ImageCost("dall-e-3", "1024x1024", "hd", 1) }},
		{"image per image", func() (float64, bool) { return db.ImageCost("dall-e-3", "", "", 2) }},
		{"image in tokens", func() (float64, bool) { return db.ImageTokenCost("gpt-image-1", 10, 0, 100) }},
	}
	for _, c := range checks {
		if cost, ok := c.cost(); !ok || cost <= 0 {
			t.Errorf("%s: cost = %v, priced = %v; want a price from the bundled table", c.name, cost, ok)
		}
	}
}

func TestParseImageSize(t *testing.T) {
	for size, want := range map[string][2]int{"1024x1024": {1024, 1024}, "1792x1024": {1792, 1024}} {
		if w, h, ok := parseImageSize(size); !ok || w != want[0] || h != want[1] {
			t.Errorf("%s: got %dx%d ok=%v", size, w, h, ok)
		}
	}
	for _, size := range []string{"", "auto", "1024", "0x10", "ax10"} {
		if _, _, ok := parseImageSize(size); ok {
			t.Errorf("%q parsed as a size", size)
		}
	}
}

// A size-keyed price is looked up exactly. Get would strip "1792-x-1024/" and
// answer with dall-e-3's flat price, half the real one at that size. A request
// that names no quality is priced as standard, the API's default.
func TestImageCost_SizeKeyedPriceWithoutQuality(t *testing.T) {
	db := New(BundledModels, nil)
	flat, _ := db.ImageCost("dall-e-3", "", "", 1)
	standard, ok := db.ImageCost("dall-e-3", "1792x1024", "standard", 1)
	if !ok {
		t.Fatal("no standard price for dall-e-3 at 1792x1024")
	}
	got, _ := db.ImageCost("dall-e-3", "1792x1024", "", 1)
	if got != standard || got <= flat {
		t.Errorf("no-quality cost = %v, want the standard size price %v, above the flat %v", got, standard, flat)
	}
}
