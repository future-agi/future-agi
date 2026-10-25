package modeldb

import (
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func bundledDB() *ModelDB { return New(BundledModels, nil) }

func near(got, want float64) bool { return math.Abs(got-want) <= 1e-12 }

func TestPricesCarryBundledExtPricing(t *testing.T) {
	db := bundledDB()
	cases := []struct {
		model string
		check func(p Prices) bool
		want  string
	}{
		{"claude-sonnet-4-5", func(p Prices) bool { return near(p.Card.CacheWrite, 3.75e-6) && near(p.Card.CacheRead, 3e-7) }, "cache write 3.75e-6, cache read 3e-7"},
		{"dashscope/qwen-turbo", func(p Prices) bool { return near(p.Card.Reasoning, 5e-7) }, "reasoning 5e-7"},
		{"tts-1", func(p Prices) bool { return near(p.PerCharacter, 1.5e-5) }, "per character 1.5e-5"},
		{"whisper-1", func(p Prices) bool { return near(p.PerSecond, 1e-4) }, "per second 1e-4"},
		{"rerank-english-v3.0", func(p Prices) bool { return near(p.PerQuery, 0.002) }, "per query 0.002"},
	}
	for _, c := range cases {
		p, ok := db.Prices("", c.model)
		if !ok || !c.check(p) {
			t.Errorf("%s: prices = %+v (ok %v), want %s", c.model, p, ok, c.want)
		}
		if p.Card.Source != models.PriceSourceModelDB {
			t.Errorf("%s: source = %q, want modeldb", c.model, p.Card.Source)
		}
	}
}

// An entry a catalog or an override replaced keeps no bundled extension: its
// own prices differ from the bundled ones, so the bundled cache-write rate
// is not this entry's.
func TestExtPricingNotAttachedToAReplacedEntry(t *testing.T) {
	replaced := WithExtra(BundledModels, map[string]*ModelInfo{
		"claude-sonnet-4-5": {Mode: ModeChat, Pricing: PricingInfo{InputPerToken: 9e-6, OutputPerToken: 9e-5}},
	})
	p, ok := New(replaced, nil).Prices("", "claude-sonnet-4-5")
	if !ok || p.Card.Prompt != 9e-6 || p.Card.CacheWrite != 0 {
		t.Fatalf("prices = %+v; want the replacement's own prices and no bundled cache-write rate", p)
	}
}

func TestPricesPreferTheProviderQualifiedEntry(t *testing.T) {
	db := New(map[string]*ModelInfo{
		"claude-x":                       {Mode: ModeChat, Pricing: PricingInfo{InputPerToken: 3e-6, OutputPerToken: 15e-6}},
		"openrouter/anthropic/claude-x":  {Mode: ModeChat, Pricing: PricingInfo{InputPerToken: 2e-6, OutputPerToken: 10e-6}},
		"openrouter/vendor/unpriced-one": {Mode: ModeChat},
	}, nil)

	p, ok := db.Prices("openrouter", "anthropic/claude-x")
	if !ok || !p.Qualified || p.Card.Prompt != 2e-6 {
		t.Fatalf("prices = %+v; want the provider's own entry", p)
	}
	p, ok = db.Prices("openai", "claude-x")
	if !ok || p.Qualified || p.Card.Prompt != 3e-6 {
		t.Fatalf("prices = %+v; want the model's own entry, not provider-qualified", p)
	}
	// A provider entry with no price falls back to the model's own.
	if _, ok := db.Prices("openrouter", "vendor/unpriced-one"); ok {
		t.Fatal("an entry with no price at all must not count as priced")
	}
	if _, ok := db.Prices("", "nobody/knows"); ok {
		t.Fatal("an unknown model has no prices")
	}
}

func TestImagePriceBySizeAndQuality(t *testing.T) {
	db := bundledDB()
	cases := []struct {
		model, size, quality string
		want                 float64
	}{
		{"dall-e-3", "1024x1024", "standard", 0.04},
		{"dall-e-3", "1024x1024", "", 0.04}, // standard is dall-e-3's default quality
		{"dall-e-3", "1024x1024", "hd", 0.08},
		{"dall-e-3", "1792x1024", "hd", 0.12},
		{"openai/dall-e-3", "1024x1792", "standard", 0.08},
		{"dall-e-2", "512x512", "", 0.018},
		{"gpt-image-1", "1024x1024", "low", 0.011},
		{"dall-e-3", "640x480", "", 0.04}, // no size-specific price: the model's flat price
	}
	for _, c := range cases {
		got, ok := db.ImagePrice(c.model, c.size, c.quality)
		if !ok || math.Abs(got-c.want) > 0.0005 {
			t.Errorf("ImagePrice(%s, %s, %q) = %v, %v; want %v", c.model, c.size, c.quality, got, ok, c.want)
		}
	}
	if _, ok := db.ImagePrice("gpt-4o", "1024x1024", ""); ok {
		t.Error("a chat model has no image price")
	}
}
