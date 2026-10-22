package modeldb

import (
	"encoding/json"
	"log/slog"
	"strings"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Prices is everything the model database knows about what one model
// charges: its price card (per token, per request, per image) and the
// per-unit prices of the audio and rerank endpoints, which a card has no
// line for.
type Prices struct {
	Card models.PriceCard
	// PerCharacter is speech input, in USD per character of text.
	PerCharacter float64
	// PerSecond is transcription input, in USD per second of audio.
	PerSecond float64
	// PerQuery is rerank, in USD per search unit (or per query when the
	// provider reports no units).
	PerQuery float64
	// ID is the entry the prices were read from, and Qualified whether it
	// is the provider's own "<provider>/<model>" entry, where a managed
	// provider's catalog files its models.
	ID        string
	Qualified bool
}

// Prices returns model's prices as providerID serves it: the provider's own
// "<provider>/<model>" entry first, then (for a preset id the database files
// under another prefix, DBPrefix) its "together_ai/<model>"-style entry, then
// the model's general entry (an aggregator's price for "vendor/model" is not
// the vendor's direct price). An entry with no price at all does not count.
func (db *ModelDB) Prices(providerID, model string) (Prices, bool) {
	if db == nil {
		return Prices{}, false
	}
	if providerID != "" && !strings.HasPrefix(model, providerID+"/") {
		if info, ok := db.models[providerID+"/"+model]; ok {
			if p, ok := pricesOf(info, providerID); ok {
				return p, true
			}
		}
		if prefix := DBPrefix(providerID); prefix != providerID && !strings.HasPrefix(model, prefix+"/") {
			if info, ok := db.models[prefix+"/"+model]; ok {
				if p, ok := pricesOf(info, providerID); ok {
					return p, true
				}
			}
		}
	}
	if info, ok := db.Get(model); ok {
		return pricesOf(info, providerID)
	}
	return Prices{}, false
}

func pricesOf(info *ModelInfo, providerID string) (Prices, bool) {
	ext := extFor(info)
	p := Prices{
		Card: models.PriceCard{
			Prompt:     info.Pricing.InputPerToken,
			Completion: info.Pricing.OutputPerToken,
			CacheRead:  info.Pricing.CachedInputPerToken,
			CacheWrite: ext.CacheWrite,
			Request:    ext.Request,
			Image:      firstPositive(info.Pricing.OutputPerImage, ext.PerImage),
			Reasoning:  ext.Reasoning,
			Source:     models.PriceSourceModelDB,
		},
		PerCharacter: firstPositive(info.Pricing.InputPerCharacter, ext.PerCharacter),
		PerSecond:    firstPositive(info.Pricing.InputPerSecond, ext.PerSecond),
		PerQuery:     ext.PerQuery,
		ID:           info.ID,
		Qualified:    providerID != "" && strings.HasPrefix(info.ID, providerID+"/"),
	}
	c := p.Card
	priced := c.Prompt > 0 || c.Completion > 0 || c.Request > 0 || c.Image > 0 ||
		p.PerCharacter > 0 || p.PerSecond > 0 || p.PerQuery > 0
	return p, priced
}

// ImagePrice returns what one image of size ("1024x1024") and quality
// ("standard", "hd", "low"...) costs from model: the size- and
// quality-specific entry when the model has one, else its flat per-image
// price. An empty quality tries the unqualified size, then "standard".
func (db *ModelDB) ImagePrice(model, size, quality string) (float64, bool) {
	if db == nil {
		return 0, false
	}
	info, ok := db.Get(model)
	if !ok {
		return 0, false
	}
	if w, h, ok := parseImageSize(size); ok {
		qualities := []string{quality, ""}
		if quality == "" {
			qualities = []string{"", "standard"}
		}
		for _, q := range qualities {
			for _, key := range imageSizeKeys(info.ID, w+"-x-"+h, q) {
				if e, ok := bundledExt()[key]; ok {
					if price := e.imagePrice(pixels(w, h)); price > 0 {
						return price, true
					}
				}
			}
		}
	}
	if price := firstPositive(info.Pricing.OutputPerImage, extFor(info).PerImage); price > 0 {
		return price, true
	}
	return 0, false
}

// OCRPrices returns model's OCR prices as providerID serves it, in USD per
// processed page and per annotated page: the provider's own
// "<provider>/<model>" entry first, then the model as given. It reads the
// bundled entries directly, because the model database keeps no OCR rows
// (they carry no token price). ok is false when neither entry has a per-page
// price.
func (db *ModelDB) OCRPrices(providerID, model string) (perPage, annotationPerPage float64, ok bool) {
	if db == nil {
		return 0, 0, false
	}
	keys := []string{model}
	if providerID != "" && !strings.HasPrefix(model, providerID+"/") {
		keys = []string{providerID + "/" + model, model}
	}
	for _, key := range keys {
		if e, found := bundledExt()[key]; found && e.PerPage > 0 {
			return e.PerPage, e.AnnotationPerPage, true
		}
	}
	return 0, 0, false
}

// imageSizeKeys are where litellm files a size-specific image price:
// "<quality>/<w>-x-<h>/<model>", or for a provider-prefixed model
// "<provider>/<quality>/<w>-x-<h>/<model>". An empty quality is left out.
func imageSizeKeys(id, dims, quality string) []string {
	mid := dims
	if quality != "" {
		mid = quality + "/" + dims
	}
	keys := []string{mid + "/" + id}
	if i := strings.IndexByte(id, '/'); i > 0 {
		keys = append(keys, id[:i]+"/"+mid+"/"+id[i+1:])
	}
	return keys
}

func parseImageSize(size string) (w, h string, ok bool) {
	w, h, ok = strings.Cut(strings.ToLower(strings.TrimSpace(size)), "x")
	if !ok || !allDigits(w) || !allDigits(h) {
		return "", "", false
	}
	return w, h, true
}

func allDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func pixels(w, h string) float64 {
	return float64(atoiDigits(w)) * float64(atoiDigits(h))
}

func atoiDigits(s string) int {
	n := 0
	for _, r := range s {
		n = n*10 + int(r-'0')
	}
	return n
}

func firstPositive(vs ...float64) float64 {
	for _, v := range vs {
		if v > 0 {
			return v
		}
	}
	return 0
}

// extPricing is the part of a bundled litellm entry PricingInfo does not
// carry. baseIn and baseOut are the entry's own token prices: an extension
// applies to a model entry only while its token prices still match, so a
// catalog or override entry that replaced the bundled one never inherits the
// bundled cache-write or request rate.
type extPricing struct {
	baseIn, baseOut float64

	CacheWrite   float64 `json:"cache_creation_input_token_cost"`
	Reasoning    float64 `json:"output_cost_per_reasoning_token"`
	Request      float64 `json:"input_cost_per_request"`
	PerImage     float64 `json:"input_cost_per_image"`
	OutPerImage  float64 `json:"output_cost_per_image"`
	PerPixel     float64 `json:"input_cost_per_pixel"`
	PerCharacter float64 `json:"input_cost_per_character"`
	PerSecond    float64 `json:"input_cost_per_second"`
	PerQuery     float64 `json:"input_cost_per_query"`
	// PerPage and AnnotationPerPage are OCR's per processed page and per
	// annotated page prices.
	PerPage           float64 `json:"ocr_cost_per_page"`
	AnnotationPerPage float64 `json:"annotation_cost_per_page"`
}

func (e extPricing) imagePrice(px float64) float64 {
	return firstPositive(e.PerImage, e.OutPerImage, e.PerPixel*px)
}

func (e extPricing) empty() bool {
	return e.CacheWrite == 0 && e.Reasoning == 0 && e.Request == 0 && e.PerImage == 0 &&
		e.OutPerImage == 0 && e.PerPixel == 0 && e.PerCharacter == 0 && e.PerSecond == 0 && e.PerQuery == 0 &&
		e.PerPage == 0 && e.AnnotationPerPage == 0
}

// extFor returns info's bundled extension, or none when info is not the
// bundled entry it shares an id with.
func extFor(info *ModelInfo) extPricing {
	e, ok := bundledExt()[info.ID]
	if !ok || e.baseIn != info.Pricing.InputPerToken || e.baseOut != info.Pricing.OutputPerToken {
		return extPricing{}
	}
	return e
}

// bundledExt parses the extended prices out of the bundled litellm data
// once, on first use.
var bundledExt = sync.OnceValue(func() map[string]extPricing {
	ext, err := parseExtPricing(liteLLMData)
	if err != nil {
		slog.Warn("bundled litellm data has no readable extended prices", "error", err)
	}
	return ext
})

func parseExtPricing(data []byte) (map[string]extPricing, error) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return map[string]extPricing{}, err
	}
	out := make(map[string]extPricing)
	for key, entry := range raw {
		var e extPricing
		var base struct {
			In  float64 `json:"input_cost_per_token"`
			Out float64 `json:"output_cost_per_token"`
		}
		if json.Unmarshal(entry, &e) != nil || json.Unmarshal(entry, &base) != nil || e.empty() {
			continue
		}
		e.baseIn, e.baseOut = base.In, base.Out
		out[key] = e
	}
	return out, nil
}
