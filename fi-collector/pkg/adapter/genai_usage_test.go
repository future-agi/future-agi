package adapter

import (
	"math"
	"testing"
)

// A sender's billed cost (gen_ai.usage.*_cost, OpenRouter Broadcast) is read
// with the Django cost rule, and only when no Django cost key is present.
func TestDeriveHotKeys_SenderCost(t *testing.T) {
	cases := []struct {
		name    string
		str     map[string]string
		num     map[string]float64
		want    float64
		wantSet bool
	}{
		{
			name: "total wins over input+output (OpenRouter's total includes fees)",
			num: map[string]float64{
				"gen_ai.usage.input_cost":  0.00003,
				"gen_ai.usage.output_cost": 0.00009,
				"gen_ai.usage.total_cost":  0.0001275,
			},
			want: 0.0001275, wantSet: true,
		},
		{
			name: "input+output summed without a total",
			num: map[string]float64{
				"gen_ai.usage.input_cost":  0.00003,
				"gen_ai.usage.output_cost": 0.00009,
			},
			want: 0.00012, wantSet: true,
		},
		{
			name: "one side only",
			num:  map[string]float64{"gen_ai.usage.output_cost": 0.00009},
			want: 0.00009, wantSet: true,
		},
		{
			name: "decimal string",
			str:  map[string]string{"gen_ai.usage.total_cost": "0.0001275"},
			want: 0.0001275, wantSet: true,
		},
		{
			name: "explicit zero (a free model) is still set",
			num:  map[string]float64{"gen_ai.usage.total_cost": 0},
			want: 0, wantSet: true,
		},
		{
			name: "gen_ai.cost.total beats gen_ai.usage.total_cost",
			num:  map[string]float64{"gen_ai.cost.total": 0.5, "gen_ai.usage.total_cost": 0.7},
			want: 0.5, wantSet: true,
		},
		{
			name: "llm.cost.total beats gen_ai.usage.total_cost",
			num:  map[string]float64{"llm.cost.total": 0.25, "gen_ai.usage.total_cost": 0.7},
			want: 0.25, wantSet: true,
		},
		{
			name: "Django input+output beats a sender total",
			num: map[string]float64{
				"gen_ai.cost.input":       0.1,
				"gen_ai.cost.output":      0.2,
				"gen_ai.usage.total_cost": 0.7,
			},
			want: 0.3, wantSet: true,
		},
		{
			name: "a single Django side still beats the sender keys",
			num:  map[string]float64{"llm.cost.prompt": 0.004, "gen_ai.usage.total_cost": 0.7},
			want: 0.004, wantSet: true,
		},
		{
			name: "unparseable Django total falls through to the sender total",
			str:  map[string]string{"gen_ai.cost.total": "n/a"},
			num:  map[string]float64{"gen_ai.usage.total_cost": 0.7},
			want: 0.7, wantSet: true,
		},
		{
			name: "cost-allocation and generation-metadata keys are not cost",
			str: map[string]string{
				"span.metadata.openrouter_cost_allocation.output_cost":  "0.00009",
				"trace.metadata.openrouter_cost_allocation.output_cost": "0.00009",
			},
			num: map[string]float64{
				"trace.metadata.openrouter_generation.usage": 0.0001275,
				"span.metadata.openrouter_generation.usage":  0.0001275,
			},
			want: 0, wantSet: false,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			hk := DeriveHotKeys(c.str, c.num)
			if hk.CostUserSet != c.wantSet || math.Abs(hk.Cost-c.want) > 1e-12 {
				t.Fatalf("cost = %v (set %v), want %v (set %v)", hk.Cost, hk.CostUserSet, c.want, c.wantSet)
			}
		})
	}
}

// gen_ai.usage.prompt_tokens / completion_tokens are read when the current
// names are absent; every current name wins over them.
func TestDeriveHotKeys_LegacyGenAITokenNames(t *testing.T) {
	cases := []struct {
		name                   string
		str                    map[string]string
		num                    map[string]float64
		wantIn, wantOut, wantT int32
	}{
		{
			name: "legacy names alone (OpenRouter webhook example)",
			num: map[string]float64{
				"gen_ai.usage.prompt_tokens":     100,
				"gen_ai.usage.completion_tokens": 50,
			},
			wantIn: 100, wantOut: 50, wantT: 150,
		},
		{
			name: "legacy names as strings",
			str: map[string]string{
				"gen_ai.usage.prompt_tokens":     "7",
				"gen_ai.usage.completion_tokens": "3",
			},
			wantIn: 7, wantOut: 3, wantT: 10,
		},
		{
			name: "gen_ai.usage.input/output_tokens win",
			num: map[string]float64{
				"gen_ai.usage.input_tokens":      12,
				"gen_ai.usage.output_tokens":     9,
				"gen_ai.usage.prompt_tokens":     100,
				"gen_ai.usage.completion_tokens": 50,
			},
			wantIn: 12, wantOut: 9, wantT: 21,
		},
		{
			name: "an explicit current 0 still wins",
			num: map[string]float64{
				"gen_ai.usage.input_tokens":  0,
				"gen_ai.usage.prompt_tokens": 100,
			},
			wantIn: 0, wantOut: 0, wantT: 0,
		},
		{
			name: "llm.token_count.* wins",
			num: map[string]float64{
				"llm.token_count.prompt":         11,
				"llm.token_count.completion":     22,
				"gen_ai.usage.prompt_tokens":     100,
				"gen_ai.usage.completion_tokens": 50,
			},
			wantIn: 11, wantOut: 22, wantT: 33,
		},
		{
			name: "llm.usage.* (OpenLLMetry) wins",
			num: map[string]float64{
				"llm.usage.prompt_tokens":        5,
				"llm.usage.completion_tokens":    7,
				"gen_ai.usage.prompt_tokens":     100,
				"gen_ai.usage.completion_tokens": 50,
			},
			wantIn: 5, wantOut: 7, wantT: 12,
		},
		{
			name: "each side resolves on its own",
			num: map[string]float64{
				"gen_ai.usage.input_tokens":      12,
				"gen_ai.usage.completion_tokens": 50,
			},
			wantIn: 12, wantOut: 50, wantT: 62,
		},
		{
			name: "an explicit total is kept",
			num: map[string]float64{
				"gen_ai.usage.prompt_tokens":     100,
				"gen_ai.usage.completion_tokens": 50,
				"gen_ai.usage.total_tokens":      160,
			},
			wantIn: 100, wantOut: 50, wantT: 160,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			hk := DeriveHotKeys(c.str, c.num)
			if hk.PromptTokens != c.wantIn || hk.CompletionTokens != c.wantOut || hk.TotalTokens != c.wantT {
				t.Fatalf("tokens = %d/%d/%d, want %d/%d/%d",
					hk.PromptTokens, hk.CompletionTokens, hk.TotalTokens, c.wantIn, c.wantOut, c.wantT)
			}
		})
	}
}
