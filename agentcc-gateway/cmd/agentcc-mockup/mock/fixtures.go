package mock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Fixtures is what a fixtures directory configures:
//
//	config.json   behaviour defaults (FixtureConfig)
//	models.json   the catalog, in OpenRouter's /api/v1/models format; its
//	              pricing is also the mock's price table
//	zdr.json      the /api/v1/endpoints/zdr body (optional)
//	endpoints/<author>/<slug>.json
//	              a model's /api/v1/models/{author}/{slug}/endpoints body
//	              (optional; synthesized from models.json otherwise)
type Fixtures struct {
	Config     FixtureConfig
	catalog    []byte                    // models.json as served
	models     map[string]map[string]any // catalog entries by id
	zdr        []byte
	endpoints  map[string][]byte // "author/slug" -> body
	modelOrder []string
}

// FixtureConfig is config.json. Decimal fields accept a JSON string or number.
type FixtureConfig struct {
	ReportCost       bool    `json:"report_cost"`
	BalanceUSD       decimal `json:"balance_usd"`
	PromptTokens     int     `json:"prompt_tokens"`
	CompletionTokens int     `json:"completion_tokens"`
	StreamChunks     int     `json:"stream_chunks"`
	DefaultPricing   struct {
		Prompt     decimal `json:"prompt"`
		Completion decimal `json:"completion"`
	} `json:"default_pricing"`
	ImagePriceUSD         decimal `json:"image_price_usd"`
	SpeechPricePerCharUSD decimal `json:"speech_price_per_char_usd"`
}

// Price is a model's USD price per token.
type Price struct {
	Prompt, Completion, CacheRead, CacheWrite float64
}

// decimal is a USD amount written as a decimal string ("0.000002") or a number.
type decimal float64

func (d *decimal) UnmarshalJSON(b []byte) error {
	s := strings.Trim(string(b), `"`)
	if s == "" || s == "null" {
		*d = 0
		return nil
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return fmt.Errorf("not a decimal: %s", b)
	}
	*d = decimal(f)
	return nil
}

func defaultFixtureConfig() FixtureConfig {
	c := FixtureConfig{
		ReportCost:            true,
		BalanceUSD:            1000,
		PromptTokens:          1200,
		CompletionTokens:      300,
		StreamChunks:          6,
		ImagePriceUSD:         0.04,
		SpeechPricePerCharUSD: 0.000015,
	}
	c.DefaultPricing.Prompt = 0.000001
	c.DefaultPricing.Completion = 0.000002
	return c
}

// LoadFixtures reads dir. dir "" gives the defaults and an empty catalog; a
// dir that does not exist is an error, so a mistyped path fails loudly.
func LoadFixtures(dir string) (*Fixtures, error) {
	f := &Fixtures{
		Config:    defaultFixtureConfig(),
		catalog:   []byte(`{"data":[]}`),
		models:    map[string]map[string]any{},
		endpoints: map[string][]byte{},
	}
	if dir == "" {
		return f, nil
	}
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("fixtures directory %q: not a directory (%v)", dir, err)
	}
	if b, err := readOptional(filepath.Join(dir, "config.json")); err != nil {
		return nil, err
	} else if b != nil {
		if err := json.Unmarshal(b, &f.Config); err != nil {
			return nil, fmt.Errorf("config.json: %w", err)
		}
	}
	if err := f.loadCatalog(filepath.Join(dir, "models.json")); err != nil {
		return nil, err
	}
	zdr, err := readOptional(filepath.Join(dir, "zdr.json"))
	if err != nil {
		return nil, err
	}
	f.zdr = zdr
	return f, f.loadEndpoints(filepath.Join(dir, "endpoints"))
}

func (f *Fixtures) loadCatalog(path string) error {
	b, err := readOptional(path)
	if err != nil || b == nil {
		return err
	}
	var cat struct {
		Data []map[string]any `json:"data"`
	}
	if err := json.Unmarshal(b, &cat); err != nil {
		return fmt.Errorf("models.json: %w", err)
	}
	for _, m := range cat.Data {
		id, _ := m["id"].(string)
		if id == "" {
			return fmt.Errorf("models.json: a model has no id")
		}
		f.models[id] = m
		f.modelOrder = append(f.modelOrder, id)
	}
	f.catalog = b
	return nil
}

func (f *Fixtures) loadEndpoints(root string) error {
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".json") {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		b, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if !json.Valid(b) {
			return fmt.Errorf("endpoints/%s: invalid JSON", rel)
		}
		f.endpoints[strings.TrimSuffix(filepath.ToSlash(rel), ".json")] = b
		return nil
	})
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	return err
}

// PriceOf returns model's price from the catalog, or the default pricing for
// a model the catalog does not list. Cache prices default to the prompt price.
func (f *Fixtures) PriceOf(model string) Price {
	var pricing map[string]any // nil for an uncatalogued model: every field falls back
	if m, ok := f.models[model]; ok {
		pricing, _ = m["pricing"].(map[string]any)
	}
	p := Price{
		Prompt:     decimalField(pricing, "prompt", float64(f.Config.DefaultPricing.Prompt)),
		Completion: decimalField(pricing, "completion", float64(f.Config.DefaultPricing.Completion)),
	}
	p.CacheRead = decimalField(pricing, "input_cache_read", p.Prompt)
	p.CacheWrite = decimalField(pricing, "input_cache_write", p.Prompt)
	return p
}

func decimalField(m map[string]any, key string, fallback float64) float64 {
	switch v := m[key].(type) {
	case string:
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	case float64:
		return v
	}
	return fallback
}

func readOptional(path string) ([]byte, error) {
	b, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	return b, err
}

// usdToMicros rounds a USD amount to integer micros, half away from zero.
func usdToMicros(usd float64) int64 { return int64(math.Round(usd * 1e6)) }
