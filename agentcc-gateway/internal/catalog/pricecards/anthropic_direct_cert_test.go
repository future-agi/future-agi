package pricecards

import (
	"encoding/json"
	"io/fs"
	"math/big"
	"testing"
	"time"
)

// These are public USD/MTok rates expressed as integer micro-USD/MTok.
// The JSON card keeps its existing decimal-string USD/token representation.
// Source: https://platform.claude.com/docs/en/about-claude/pricing#model-pricing
// Fetched 2026-11-07 UTC. Certification covers standard global inference,
// the card's context limit and 5-minute cache writes, not account eligibility.
func TestAnthropicDirectCertifiedPrices(t *testing.T) {
	body, err := fs.ReadFile(FS, "anthropic-direct.json")
	if err != nil {
		t.Fatal(err)
	}
	var card struct {
		Provider       string `json:"provider"`
		AsOf           string `json:"as_of"`
		DataCollection string `json:"data_collection"`
		ZDR            *bool  `json:"zdr"`
		Models         []struct {
			ID                 string            `json:"id"`
			ContextLength      int               `json:"context_length"`
			Prices             map[string]string `json:"prices"`
			PriceCertified     *bool             `json:"price_certified"`
			PriceCertification *struct {
				SourceURL  string `json:"source_url"`
				VerifiedOn string `json:"verified_on"`
				Scope      string `json:"scope"`
			} `json:"price_certification"`
		} `json:"models"`
	}
	if err := json.Unmarshal(body, &card); err != nil {
		t.Fatal(err)
	}
	if card.Provider != "anthropic-direct" {
		t.Fatalf("provider = %q", card.Provider)
	}
	// Do not freshen unverified rows or assert data/resale terms by certifying
	// prices. The existing managed-eligibility behavior must stay unchanged.
	if card.AsOf != "2026-09-07" || card.DataCollection != "" || card.ZDR != nil {
		t.Fatal("price-only certification changed freshness or account terms")
	}
	type expected struct {
		certified bool
		context   int
		micros    [4]int64 // prompt, completion, cache_read, cache_write (5m)
	}
	want := map[string]expected{
		"claude-haiku-4-5-20251001":  {true, 200000, [4]int64{1000000, 5000000, 100000, 1250000}},
		"claude-sonnet-4-5-20250929": {true, 200000, [4]int64{3000000, 15000000, 300000, 3750000}},
		"claude-opus-4-5-20251101":   {true, 200000, [4]int64{5000000, 25000000, 500000, 6250000}},
		"claude-sonnet-4-6":          {true, 1000000, [4]int64{3000000, 15000000, 300000, 3750000}},
		// Official IDs are claude-opus-4-6 and claude-opus-4-7, without dates.
		// Do not certify the existing suffixed IDs by inferring an alias.
		"claude-opus-4-6-20260205": {false, 1000000, [4]int64{5000000, 25000000, 500000, 6250000}},
		"claude-opus-4-7-20260416": {false, 1000000, [4]int64{5000000, 25000000, 500000, 6250000}},
		"claude-opus-4-8":          {true, 1000000, [4]int64{5000000, 25000000, 500000, 6250000}},
		"claude-sonnet-5":          {true, 1000000, [4]int64{2000000, 10000000, 200000, 2500000}},
		"claude-opus-5":            {true, 1000000, [4]int64{5000000, 25000000, 500000, 6250000}},
		"claude-fable-5":           {true, 1000000, [4]int64{10000000, 50000000, 1000000, 12500000}},
		"claude-fable-5-1":         {true, 1000000, [4]int64{10000000, 50000000, 250000, 12500000}},
	}
	if len(card.Models) != len(want) {
		t.Fatalf("models = %d, want %d", len(card.Models), len(want))
	}
	seen := make(map[string]bool)
	certifiedCount := 0
	for _, row := range card.Models {
		t.Run(row.ID, func(t *testing.T) {
			expect, ok := want[row.ID]
			if !ok || seen[row.ID] {
				t.Fatalf("unexpected or duplicate row %q", row.ID)
			}
			seen[row.ID] = true
			if row.PriceCertified == nil || *row.PriceCertified != expect.certified {
				t.Fatalf("price_certified must explicitly be %t", expect.certified)
			}
			if row.ContextLength != expect.context {
				t.Errorf("context_length = %d, want %d", row.ContextLength, expect.context)
			}
			if len(row.Prices) != len(expect.micros) {
				t.Fatalf("prices must contain exactly the four confirmed token dimensions")
			}
			for i, key := range []string{"prompt", "completion", "cache_read", "cache_write"} {
				rate, ok := new(big.Rat).SetString(row.Prices[key])
				if !ok || rate.Sign() < 0 {
					t.Fatalf("%s is not a non-negative decimal rate", key)
				}
				// USD/token * 1e6 tokens/MTok * 1e6 micro-USD/USD.
				rate.Mul(rate, big.NewRat(1000000000000, 1))
				if !rate.IsInt() || !rate.Num().IsInt64() || rate.Num().Int64() != expect.micros[i] {
					t.Errorf("%s = %s micro-USD/MTok, want %d", key, rate.RatString(), expect.micros[i])
				}
			}
			if !expect.certified {
				if row.PriceCertification != nil {
					t.Error("unverified native ID must not carry a successful certification")
				}
				return
			}
			certifiedCount++
			proof := row.PriceCertification
			if proof == nil {
				t.Fatal("certified row lacks provenance")
			}
			if proof.SourceURL != "https://platform.claude.com/docs/en/about-claude/pricing#model-pricing" ||
				proof.VerifiedOn != "2026-11-07" || proof.Scope != "standard-global-5m-cache" {
				t.Fatal("certification source, date or pricing scope drifted")
			}
			if _, err := time.Parse(time.DateOnly, proof.VerifiedOn); err != nil {
				t.Fatal(err)
			}
		})
	}
	if certifiedCount != 9 {
		t.Errorf("certified rows = %d, want 9", certifiedCount)
	}
}
