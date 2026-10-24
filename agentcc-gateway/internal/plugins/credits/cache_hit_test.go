package credits

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

// A response served from the cache makes no provider call, so it must not be
// priced or deducted from a managed key, whichever cache layer answered it.
// The cached response still carries the usage of the call that filled the
// cache, so a hit the engine does not recognise is billed at full price.
func TestCacheHitDeductsNoCredits(t *testing.T) {
	ks := newKeyStore()
	managed := findKey(ks, "managed")
	if managed == nil {
		t.Fatal("expected to find managed key")
	}

	db := modeldb.New(map[string]*modeldb.ModelInfo{
		"gpt-4o": {
			ID:       "gpt-4o",
			Provider: "openai",
			Mode:     modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{
				InputPerToken:  2.50 / 1_000_000,
				OutputPerToken: 10.00 / 1_000_000,
			},
		},
	}, nil)

	cp := cacheplugin.NewFromStore(cache.NewStore(100), 5*time.Minute)
	cp.SetSemanticStoreFromConcrete(cache.NewSemanticStore(0.85, 256, 100))
	engine := pipeline.NewEngine(
		cp,
		cost.New(true, func() *modeldb.ModelDB { return db }, nil, nil),
		New(true, ks),
	)

	providerCalls := 0
	provider := func(_ context.Context, rc *models.RequestContext) error {
		providerCalls++
		rc.Response = &models.ChatCompletionResponse{
			ID:    "chatcmpl-1",
			Model: "gpt-4o",
			Choices: []models.Choice{{
				Message:      models.Message{Role: "assistant", Content: json.RawMessage(`"30 days"`)},
				FinishReason: "stop",
			}},
			Usage: &models.Usage{PromptTokens: 100_000, CompletionTokens: 1_000, TotalTokens: 101_000},
		}
		return nil
	}

	send := func(t *testing.T, prompt string) *models.RequestContext {
		t.Helper()
		content, _ := json.Marshal(prompt)
		rc := makeRC(managed.ID, "managed")
		rc.Model = "gpt-4o"
		rc.Request = &models.ChatCompletionRequest{
			Model:    "gpt-4o",
			Messages: []models.Message{{Role: "user", Content: content}},
		}
		if err := engine.Process(context.Background(), rc, provider); err != nil {
			t.Fatalf("Process: %v", err)
		}
		return rc
	}

	miss := send(t, "what is the refund policy")
	defer miss.Release()
	if providerCalls != 1 || miss.Metadata["cache_status"] != "miss" {
		t.Fatalf("setup: want one provider call on a miss, got calls=%d status=%q",
			providerCalls, miss.Metadata["cache_status"])
	}
	afterMiss := managed.BalanceMicros()
	if afterMiss >= 10_000_000 {
		t.Fatalf("setup: the provider call deducted nothing, balance=%d micros", afterMiss)
	}

	for _, tc := range []struct {
		prompt string
		status string
	}{
		{"what is the refund policy", "hit_exact"},
		{"what is the refund policy?", "hit_semantic"},
	} {
		t.Run(tc.status, func(t *testing.T) {
			rc := send(t, tc.prompt)
			defer rc.Release()

			if got := rc.Metadata["cache_status"]; got != tc.status {
				t.Fatalf("setup: cache_status=%q, want %q", got, tc.status)
			}
			if providerCalls != 1 {
				t.Fatalf("setup: a %s called the provider (calls=%d)", tc.status, providerCalls)
			}
			if c := rc.Metadata["cost"]; c != "" {
				t.Errorf("a %s was priced at cost=%s although no provider call was made", tc.status, c)
			}
			if got := managed.BalanceMicros(); got != afterMiss {
				t.Errorf("a %s deducted %d micros from the managed key", tc.status, afterMiss-got)
			}
		})
	}
}
