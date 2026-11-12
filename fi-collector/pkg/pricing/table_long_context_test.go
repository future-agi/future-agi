package pricing

import (
	"math"
	"os"
	"path/filepath"
	"testing"
)

func TestTableCost_LongContextEmbedded(t *testing.T) {
	tbl, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}

	for _, tc := range []struct {
		name   string
		model  string
		prompt int32
		output int32
		want   float64
	}{
		{"gpt54_below", "gpt-5.4", 271_999, 1_000, 0.6949975},
		{"gpt54_at", "gpt-5.4", 272_000, 1_000, 0.695},
		{"gpt54_above", "gpt-5.4", 272_001, 1_000, 1.382505},
		{"gpt54_300k", "gpt-5.4", 300_000, 1_000, 1.5225},
		{"gpt54_input_only", "gpt-5.4", 300_000, 0, 1.5},
		{"gemini25_below", "gemini/gemini-2.5-pro", 199_999, 1_000, 0.25999875},
		{"gemini25_at", "gemini/gemini-2.5-pro", 200_000, 1_000, 0.26},
		{"gemini25_above", "gemini/gemini-2.5-pro", 200_001, 1_000, 0.5150025},
		{"gemini25_300k", "gemini/gemini-2.5-pro", 300_000, 1_000, 0.765},
		{"qwen35_at", "openrouter/qwen/qwen3.5-plus-02-15", 256_000, 1_000, 0.06812},
		{"qwen35_above", "openrouter/qwen/qwen3.5-plus-02-15", 256_001, 1_000, 0.1310005},
		{"minimax_m3_at", "minimax/MiniMax-M3", 512_000, 1_000, 0.1548},
		{"minimax_m3_above", "minimax/MiniMax-M3", 512_001, 1_000, 0.3096006},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := tbl.Cost(tc.model, tc.prompt, tc.output)
			if !ok || math.IsNaN(got) || math.Abs(got-tc.want) > 1e-12 {
				t.Fatalf("Cost(%q, %d, %d) = %.12g, %v; want %.12g, true", tc.model, tc.prompt, tc.output, got, ok, tc.want)
			}
		})
	}
}

func TestTableCost_LongContextBoundaries(t *testing.T) {
	// Use JSON rather than modelPrice fields so this test also detects missing
	// decoding and compiles unchanged against the base implementation.
	tbl := loadLongContextTestTable(t, `{
		"tiered": {
			"input_cost_per_token": 0.000001,
			"output_cost_per_token": 0.000002,
			"input_cost_per_token_above_100k_tokens": 0.000003,
			"output_cost_per_token_above_100k_tokens": 0.000004,
			"input_cost_per_token_above_128k_tokens": 0.000005,
			"output_cost_per_token_above_128k_tokens": 0.000006,
			"input_cost_per_token_above_200k_tokens": 0.000007,
			"output_cost_per_token_above_200k_tokens": 0.000008,
			"input_cost_per_token_above_256k_tokens": 0.000009,
			"output_cost_per_token_above_256k_tokens": 0.000010,
			"input_cost_per_token_above_272k_tokens": 0.000011,
			"output_cost_per_token_above_272k_tokens": 0.000012,
			"input_cost_per_token_above_512k_tokens": 0.000013,
			"output_cost_per_token_above_512k_tokens": 0.000014
		}
	}`)

	for _, tier := range []struct {
		name      string
		threshold int32
		lowerIn   float64
		lowerOut  float64
		upperIn   float64
		upperOut  float64
	}{
		{"100k", 100_000, 1e-6, 2e-6, 3e-6, 4e-6},
		{"128k", 128_000, 3e-6, 4e-6, 5e-6, 6e-6},
		{"200k", 200_000, 5e-6, 6e-6, 7e-6, 8e-6},
		{"256k", 256_000, 7e-6, 8e-6, 9e-6, 10e-6},
		{"272k", 272_000, 9e-6, 10e-6, 11e-6, 12e-6},
		{"512k", 512_000, 11e-6, 12e-6, 13e-6, 14e-6},
	} {
		for _, point := range []struct {
			name    string
			prompt  int32
			inRate  float64
			outRate float64
		}{
			{"below", tier.threshold - 1, tier.lowerIn, tier.lowerOut},
			{"at", tier.threshold, tier.lowerIn, tier.lowerOut},
			{"above", tier.threshold + 1, tier.upperIn, tier.upperOut},
		} {
			t.Run(tier.name+"/"+point.name, func(t *testing.T) {
				// All tokens, not just those beyond the threshold, use the
				// selected rate. Output must not influence tier selection.
				const output int32 = 1_000
				want := float64(point.prompt)*point.inRate + float64(output)*point.outRate
				got, ok := tbl.Cost("tiered", point.prompt, output)
				if !ok || math.IsNaN(got) || math.Abs(got-want) > 1e-12 {
					t.Fatalf("Cost(tiered, %d, %d) = %.12g, %v; want %.12g, true", point.prompt, output, got, ok, want)
				}
			})
		}
	}
}

func TestTableCost_LongContextSparseRates(t *testing.T) {
	tbl := loadLongContextTestTable(t, `{
		"input-only": {
			"input_cost_per_token": 0.000001,
			"output_cost_per_token": 0.000002,
			"input_cost_per_token_above_200k_tokens": 0.000003
		},
		"output-only": {
			"input_cost_per_token": 0.000001,
			"output_cost_per_token": 0.000002,
			"output_cost_per_token_above_272k_tokens": 0.000004
		},
		"zero-input": {
			"input_cost_per_token": 0.000001,
			"output_cost_per_token": 0.000002,
			"input_cost_per_token_above_200k_tokens": 0,
			"output_cost_per_token_above_200k_tokens": 0.000004
		},
		"zero-output": {
			"input_cost_per_token": 0.000001,
			"output_cost_per_token": 0.000002,
			"input_cost_per_token_above_272k_tokens": 0.000003,
			"output_cost_per_token_above_272k_tokens": 0
		},
		"only-128k": {
			"input_cost_per_token": 0.000001,
			"output_cost_per_token": 0.000002,
			"input_cost_per_token_above_128k_tokens": 0.000003,
			"output_cost_per_token_above_128k_tokens": 0.000004
		},
		"zero-both": {
			"input_cost_per_token": 0.000001,
			"output_cost_per_token": 0.000002,
			"input_cost_per_token_above_200k_tokens": 0,
			"output_cost_per_token_above_200k_tokens": 0
		},
		"stacked": {
			"input_cost_per_token": 0.000001,
			"output_cost_per_token": 0.000002,
			"input_cost_per_token_above_128k_tokens": 0.000003,
			"output_cost_per_token_above_128k_tokens": 0.000004,
			"input_cost_per_token_above_200k_tokens": 0,
			"output_cost_per_token_above_200k_tokens": 0.000006,
			"input_cost_per_token_above_272k_tokens": 0.000002,
			"output_cost_per_token_above_272k_tokens": 0
		}
	}`)

	for _, tc := range []struct {
		name   string
		model  string
		prompt int32
		output int32
		want   float64
	}{
		{"missing_output", "input-only", 300_000, 1_000, 0.902},
		{"missing_input", "output-only", 300_000, 1_000, 0.304},
		{"zero_input", "zero-input", 300_000, 1_000, 0.304},
		{"zero_output", "zero-output", 300_000, 1_000, 0.902},
		{"retain_128k", "only-128k", 600_000, 1_000, 1.804},
		{"128k_exact_boundary", "only-128k", 128_000, 1_000, 0.13},
		{"128k_one_above", "only-128k", 128_001, 1_000, 0.388003},
		{"zero_both", "zero-both", 600_000, 1_000, 0.602},
		{"independent_tiers", "stacked", 250_000, 1_000, 0.756},
		{"highest_threshold_not_highest_rate", "stacked", 300_000, 1_000, 0.606},
		{"output_count_not_selector", "stacked", 1_000, 600_000, 1.201},
		{"total_count_not_selector", "input-only", 200_000, 1_000, 0.202},
		{"no_prompt", "input-only", 0, 600_000, 1.2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := tbl.Cost(tc.model, tc.prompt, tc.output)
			if !ok || math.IsNaN(got) || math.Abs(got-tc.want) > 1e-12 {
				t.Fatalf("Cost(%q, %d, %d) = %.12g, %v; want %.12g, true", tc.model, tc.prompt, tc.output, got, ok, tc.want)
			}
		})
	}
}

func loadLongContextTestTable(t *testing.T, contents string) *Table {
	t.Helper()
	path := filepath.Join(t.TempDir(), "prices.json")
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatal(err)
	}
	tbl, err := LoadTable(path)
	if err != nil {
		t.Fatal(err)
	}
	if tbl.Skipped != 0 {
		t.Fatalf("valid fixture entries were skipped: %d", tbl.Skipped)
	}
	return tbl
}
