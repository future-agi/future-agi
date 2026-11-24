package cost

import (
	"context"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestStatedFreeSource(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, tc := range []struct {
		name, model, served, provider, endpoint, source, cost string
		failover                                              bool
	}{
		{"ollama-full", "ollama/llama3", "", "ollama", "chat", "catalogue", "", false},
		{"ollama-bare", "llama3", "", "ollama", "chat", "catalogue", "", false},
		{"codegeex-full", "ollama/codegeex4", "", "ollama", "chat", "catalogue", "", false},
		{"codegeex-bare", "codegeex4", "", "ollama", "chat", "catalogue", "", false},
		{"embedding", "doubao-embedding", "", "unrelated-provider", "embedding", "catalogue", "", false},
		{"evaluation", "bespoke/nimble-latest", "", "bespoke", "chat", "catalogue", "", false},
		{"missing", "unknown", "", "ollama", "chat", "unpriced", "", false},
		{"input-only-zero", "perplexity/pplx-7b-online", "", "perplexity", "chat", "unpriced", "", false},
		{"other-positive-rate", "gemini/lyria-3-clip-preview", "", "gemini", "chat", "unpriced", "", false},
		{"perplexity/pplx-70b-online", "perplexity/pplx-70b-online", "", "perplexity", "chat", "unpriced", "", false},
		{"perplexity/sonar-medium-online", "perplexity/sonar-medium-online", "", "perplexity", "chat", "unpriced", "", false},
		{"perplexity/sonar-small-online", "perplexity/sonar-small-online", "", "perplexity", "chat", "unpriced", "", false},
		{"gemini/lyria-3-pro-preview", "gemini/lyria-3-pro-preview", "", "gemini", "chat", "unpriced", "", false},
		{"gemini/lyria-3.5-clip-preview", "gemini/lyria-3.5-clip-preview", "", "gemini", "chat", "unpriced", "", false},
		{"gemini/lyria-3.5-pro-preview", "gemini/lyria-3.5-pro-preview", "", "gemini", "chat", "unpriced", "", false},
		{"gemini/lyria-3.5", "gemini/lyria-3.5", "", "gemini", "chat", "unpriced", "", false},
		{"gemini/lyria-realtime-exp", "gemini/lyria-realtime-exp", "", "gemini", "chat", "unpriced", "", false},
		{"requested-price", "gpt-4o-mini", "unknown-served", "openai", "chat", "catalogue", "0.000450", true},
		{"requested-price-with-free-served", "gpt-4o-mini", "ollama/llama3", "openai", "chat", "catalogue", "0.000450", true},
		{"requested-price-no-failover", "gpt-4o-mini", "ollama/llama3", "openai", "chat", "catalogue", "0.000450", false},
		{"free-served", "unknown", "ollama/llama3", "ollama", "chat", "catalogue", "", true},
		{"free-requested", "ollama/llama3", "unknown-served", "ollama", "chat", "catalogue", "", true},
		{"both-missing", "unknown", "unknown-served", "ollama", "chat", "unpriced", "", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := newRC(tc.model, 1000, 500)
			rc.Provider, rc.EndpointType, rc.ResolvedModel = tc.provider, tc.endpoint, tc.served
			rc.Flags.FallbackUsed = tc.failover
			if tc.failover {
				rc.Metadata["fallback_model"] = tc.served
			}
			if tc.endpoint == "embedding" {
				rc.EmbeddingResponse = &models.EmbeddingResponse{Usage: &models.EmbeddingUsage{PromptTokens: 1000}}
			}
			p.ProcessResponse(context.Background(), rc)
			cost, present := rc.Metadata["cost"]
			_, audit := rc.Metadata[MetadataCatalogue]
			if rc.Metadata["cost_source"] != tc.source || present != (tc.cost != "") || cost != tc.cost || audit {
				t.Fatalf("cost_source=%q cost=%q present=%t audit=%t; want %q %q", rc.Metadata["cost_source"], cost, present, audit, tc.source, tc.cost)
			}
		})
	}
}

func TestNewCodestralRowTaggedCatalogueNotUnpriced(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	rc := newRC("codestral/codestral-2405", 1000, 500)
	p.ProcessResponse(context.Background(), rc)
	if _, ok := rc.Metadata["cost"]; ok || rc.Metadata["cost_source"] != "catalogue" {
		t.Fatalf("new codestral row: want catalogue without cost, got %v", rc.Metadata)
	}
}

func TestStatedFreeDoesNotShadowPricedTwin(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, suffix := range []string{"exp-1206", "1.5-flash-exp-0827", "2.0-flash-exp", "2.0-pro-exp-02-05", "2.5-pro-exp-03-25"} {
		bare := "gemini-" + suffix
		cost, ok := db.CalculateCost(bare, 1000, 500, modeldb.CostOptions{})
		if !ok || cost <= 0 {
			t.Fatalf("missing priced control %s", bare)
		}
		rc := newRC("gemini/"+bare, 1000, 500)
		p.ProcessResponse(context.Background(), rc)
		if rc.Metadata["cost"] != fmt.Sprintf("%.6f", cost) || rc.Metadata["cost_source"] != "catalogue" {
			t.Errorf("%s: %v", bare, rc.Metadata)
		}
	}
}

// PriceKey selects an exact bare override before attempting provider names.
// A different, free provider entry must not replace that non-free selection.
func TestBareOverrideKeepsExactPriceKey(t *testing.T) {
	one := 1.0
	db := modeldb.New(modeldb.BundledModels, map[string]modeldb.ModelOverride{"llama3": {Pricing: &modeldb.PricingOverride{OutputPerToken: &one}}})
	if key := db.PriceKey("llama3", "ollama", ""); key != "llama3" {
		t.Fatalf("price key = %q", key)
	}
	rc := newRC("llama3", 1000, 500)
	rc.Provider = "ollama"
	New(true, func() *modeldb.ModelDB { return db }, nil, nil).ProcessResponse(context.Background(), rc)
	if _, present := rc.Metadata["cost"]; present || rc.Metadata["cost_source"] != "unpriced" {
		t.Fatalf("exact non-free override: cost=%q present=%t source=%q", rc.Metadata["cost"], present, rc.Metadata["cost_source"])
	}
}
