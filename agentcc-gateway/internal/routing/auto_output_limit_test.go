package routing

import (
	"errors"
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

func TestChooseAutoEffectiveOutputLimit(t *testing.T) {
	intPtr := func(n int) *int { return &n }
	cases := []struct {
		name                string
		maxTokens           *int
		maxCompletionTokens *int
		smallModelLimit     int
		wantModel           string
		wantOutputTokens    int
		wantRejection       string
	}{
		{
			name: "completion_limit_only", maxCompletionTokens: intPtr(20000),
			smallModelLimit: 16384, wantModel: "gpt-4.1-mini", wantOutputTokens: 20000,
			wantRejection: "max_completion_tokens 20000 exceeds its 16384-token output limit",
		},
		{
			name: "legacy_limit_only", maxTokens: intPtr(20000),
			smallModelLimit: 16384, wantModel: "gpt-4.1-mini", wantOutputTokens: 20000,
			wantRejection: "max_tokens 20000 exceeds its 16384-token output limit",
		},
		{
			name: "completion_limit_takes_precedence_when_larger", maxTokens: intPtr(100), maxCompletionTokens: intPtr(20000),
			smallModelLimit: 16384, wantModel: "gpt-4.1-mini", wantOutputTokens: 20000,
			wantRejection: "max_completion_tokens 20000 exceeds its 16384-token output limit",
		},
		{
			name: "completion_limit_takes_precedence_when_smaller", maxTokens: intPtr(20000), maxCompletionTokens: intPtr(100),
			smallModelLimit: 16384, wantModel: "gpt-4o-mini", wantOutputTokens: 100,
		},
		{
			name: "exact_output_limit_is_allowed", maxCompletionTokens: intPtr(16384),
			smallModelLimit: 16384, wantModel: "gpt-4o-mini", wantOutputTokens: 16384,
		},
		{
			name: "unknown_model_limit_is_allowed", maxCompletionTokens: intPtr(20000),
			smallModelLimit: 0, wantModel: "gpt-4o-mini", wantOutputTokens: 20000,
		},
		{
			name:            "estimate_is_not_an_explicit_limit",
			smallModelLimit: 128, wantModel: "gpt-4o-mini", wantOutputTokens: autoDefaultOutputTokens,
		},
		{
			name: "zero_legacy_limit_preserves_eligibility", maxTokens: intPtr(0),
			smallModelLimit: 128, wantModel: "gpt-4o-mini", wantOutputTokens: autoDefaultOutputTokens,
		},
		{
			name: "zero_completion_limit_preserves_eligibility", maxCompletionTokens: intPtr(0),
			smallModelLimit: 128, wantModel: "gpt-4o-mini", wantOutputTokens: autoDefaultOutputTokens,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Mirror the bundled models' prices and output limits without a
			// global catalog or a provider call. Cost preference favors 4o-mini.
			small := autoInfo(0.15, 0.6, allCaps)
			small.MaxOutputTokens = tc.smallModelLimit
			large := autoInfo(0.4, 1.6, allCaps)
			large.MaxOutputTokens = 32768
			pool := []AutoCandidate{
				{Model: "gpt-4o-mini", ProviderID: "openai", Info: small},
				{Model: "gpt-4.1-mini", ProviderID: "openai", Info: large},
			}
			req := &models.ChatCompletionRequest{
				Model: "auto", Messages: []models.Message{userMsg("hello")},
				MaxTokens: tc.maxTokens, MaxCompletionTokens: tc.maxCompletionTokens,
			}
			d, err := ChooseAuto(req, pool, AutoPolicy{Preference: AutoPreferCost})
			if err != nil {
				t.Fatal(err)
			}
			if d.Model != tc.wantModel || d.ProviderID != "openai" {
				t.Fatalf("chose %s on %s, want %s on openai", d.Model, d.ProviderID, tc.wantModel)
			}
			if tc.wantRejection != "" {
				if len(d.Rejected) != 1 || d.Rejected[0].Model != "gpt-4o-mini" || d.Rejected[0].Reason != tc.wantRejection {
					t.Fatalf("rejected = %+v, want gpt-4o-mini: %s", d.Rejected, tc.wantRejection)
				}
				if len(d.Ranked) != 1 || len(d.Fallbacks) != 0 {
					t.Fatalf("undersized model remains ranked or in fallbacks: %+v", d)
				}
			} else if len(d.Rejected) != 0 || len(d.Ranked) != 2 {
				t.Fatalf("both models should remain eligible: %+v", d)
			}
			for _, ranked := range d.Ranked {
				info := small
				if ranked.Model == "gpt-4.1-mini" {
					info = large
				}
				wantCost := float64(tokenizer.EstimateTokens(req.Messages))*info.Pricing.InputPerToken + float64(tc.wantOutputTokens)*info.Pricing.OutputPerToken
				if math.Abs(ranked.EstCostUSD-wantCost) > 1e-12 {
					t.Errorf("%s cost = %g, want %g using %d output tokens", ranked.Model, ranked.EstCostUSD, wantCost, tc.wantOutputTokens)
				}
			}
		})
	}
}

func TestChooseAutoCompletionLimitRejectsAllUndersizedModels(t *testing.T) {
	maxCompletionTokens := 32769
	req := &models.ChatCompletionRequest{
		Model: "auto", Messages: []models.Message{userMsg("hello")},
		MaxCompletionTokens: &maxCompletionTokens,
	}
	small := autoInfo(0.15, 0.6, allCaps)
	small.MaxOutputTokens = 16384
	large := autoInfo(0.4, 1.6, allCaps)
	large.MaxOutputTokens = 32768
	d, err := ChooseAuto(req, []AutoCandidate{
		{Model: "gpt-4o-mini", ProviderID: "openai", Info: small},
		{Model: "gpt-4.1-mini", ProviderID: "openai", Info: large},
	}, AutoPolicy{Preference: AutoPreferCost})
	var none *NoAutoCandidateError
	if d != nil || !errors.As(err, &none) || len(none.Rejected) != 2 {
		t.Fatalf("want no candidate with two output-limit rejections, got decision %+v, error %v", d, err)
	}
	want := []AutoRejection{
		{Model: "gpt-4o-mini", Reason: "max_completion_tokens 32769 exceeds its 16384-token output limit"},
		{Model: "gpt-4.1-mini", Reason: "max_completion_tokens 32769 exceeds its 32768-token output limit"},
	}
	for i, rejection := range none.Rejected {
		if rejection != want[i] {
			t.Errorf("rejection %d = %+v, want %+v", i, rejection, want[i])
		}
	}
}
