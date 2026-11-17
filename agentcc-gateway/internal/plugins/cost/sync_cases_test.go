package cost

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// syncCase is one real request priced through the whole cost step: 1M prompt
// tokens and 100k completion tokens at a fixed instant.
type syncCase struct {
	name           string
	model          string
	provider       string
	table          string // the price table the serving instance states
	configuredType string // the type the serving instance's config records
	at             time.Time
	override       map[string]modeldb.ModelOverride
	want           string
}

var (
	syncT26 = time.Date(2026, 12, 15, 12, 0, 0, 0, time.UTC)
	syncT27 = time.Date(2027, 1, 15, 12, 0, 0, 0, time.UTC)
)

func syncInput(v float64) *float64 { return &v }

// The cases priced the same before the dev merge on the side that owned each
// rule, and after it. Each request is priced by exactly one key and one rule:
// the price key picks the entry, and only then does the reseller rule decide
// whether a Google Gemini Flash entry is billed at a reseller's own price.
var syncCases = []syncCase{
	{name: "plain model", model: "gpt-4o", provider: "openai", at: syncT26, want: "3.500000"},
	{name: "reseller-prefixed name, the reseller's own entry", model: "google/gemini-3.6-flash", provider: "perplexity", table: "perplexity", at: syncT26, want: "2.250000"},
	{name: "dated Flash row, 2026", model: "gemini-3.7-flash", provider: "gemini", table: "gemini", at: syncT26, want: "1.125000"},
	{name: "dated Flash row, 2027", model: "gemini-3.7-flash", provider: "gemini", table: "gemini", at: syncT27, want: "2.250000"},
	{name: "operator input override on a dated row, 2027", model: "gemini/gemini-3.7-flash", provider: "gemini", table: "gemini", at: syncT27,
		override: map[string]modeldb.ModelOverride{"gemini/gemini-3.7-flash": {Pricing: &modeldb.PricingOverride{InputPerToken: syncInput(1e-6)}}},
		want:     "1.750000"},
	{name: "served-instance price table (vertex_ai_global serves vertex_ai)", model: "gemini-3.7-flash", provider: "vertex_ai_global", table: "vertex_ai", at: syncT27, want: "2.250000"},
	{name: "reseller own price from 2027 (deepinfra)", model: "gemini-3.7-flash", provider: "di", configuredType: "deepinfra", at: syncT27, want: "1.125000"},
}

func TestTheDevMergeKeepsEveryPricingRule(t *testing.T) {
	for _, c := range syncCases {
		t.Run(c.name, func(t *testing.T) {
			db := modeldb.New(modeldb.BundledModels, c.override)
			p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
			p.now = clockAt(c.at)
			table, provider, configuredType := c.table, c.provider, c.configuredType
			p.SetPriceTables(func(rc *models.RequestContext) string {
				if rc.Provider == provider {
					return table
				}
				return ""
			})
			p.SetProviderTypes(func(rc *models.RequestContext) string {
				if rc.Provider == provider {
					return configuredType
				}
				return ""
			})
			rc := newRC(c.model, 1_000_000, 100_000)
			rc.Provider = c.provider
			p.ProcessResponse(context.Background(), rc)
			got := rc.Metadata["cost"]
			t.Logf("SYNCCASE %s | %s", c.name, got)
			if got != c.want {
				t.Errorf("cost = %q, want %q", got, c.want)
			}
		})
	}
}

// The price key and the reseller rule never price one request twice: the key
// picks the entry, and the reseller rule acts only when that entry is one of
// Google's own Gemini Flash keys.
func TestTheKeyAndTheResellerRuleEachPriceOnce(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	for _, c := range []struct {
		name, model, provider, table, configuredType string
		at                                           time.Time
		wantKey, wantOwn                             string
		wantKeep                                     bool
	}{
		// The key already names Perplexity's own entry: the rule keeps its price as listed.
		{"perplexity, own entry by key", "google/gemini-3.6-flash", "perplexity", "perplexity", "", syncT26, "perplexity/google/gemini-3.6-flash", "", true},
		// Google's own key, then the reseller rule bills DeepInfra's entry from 2027.
		{"deepinfra, own entry by rule", "gemini-3.7-flash", "di", "", "deepinfra", syncT27, "gemini-3.7-flash", "deepinfra/google/gemini-3.7-flash", false},
		// Google's own provider: neither moves it off the dated row.
		{"google", "gemini-3.7-flash", "gemini", "gemini", "", syncT27, "gemini-3.7-flash", "", false},
	} {
		key := db.PriceKey(c.model, c.provider, c.table)
		if key != c.wantKey {
			t.Errorf("%s: PriceKey = %q, want %q", c.name, key, c.wantKey)
		}
		own, keep := resellerPrice(db, key, c.provider, c.table, c.configuredType, c.at)
		if own != c.wantOwn || keep != c.wantKeep {
			t.Errorf("%s: resellerPrice = (%q, %v), want (%q, %v)", c.name, own, keep, c.wantOwn, c.wantKeep)
		}
	}
}
