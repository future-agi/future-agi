package budget

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The response traverses both plugins. A model may already have a budget-only
// price, so an absent cost must not be replaced with an explicit zero.
func TestCatalogueTagSpendParity(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	type fixture struct{ name, model, served, provider, endpoint string }
	cases := []fixture{
		{"free", "ollama/llama3", "", "ollama", "chat"},
		{"free-bare", "llama3", "", "ollama", "chat"},
		{"codegeex", "ollama/codegeex4", "", "ollama", "chat"},
		{"codegeex-bare", "codegeex4", "", "ollama", "chat"},
		{"grandfather", "ollama/llama3", "", "ollama", "chat"},
		{"embedding", "doubao-embedding", "", "other", "embedding"},
		{"evaluation", "bespoke/nimble-latest", "", "bespoke", "chat"},
		{"codestral", "codestral/codestral-2405", "", "codestral", "chat"},
		{"rounded", "gpt-4o-mini", "", "openai", "chat"},
		{"alias-zero", "gpt-4o-mini", "", "openai", "chat"},
		{"org-zero", "configured-model", "", "test", "chat"},
		{"reported", "openrouter/z-ai/glm-5.2:free", "", "openrouter", "chat"},
		{"requested-priced", "gpt-4o-mini", "unknown", "openai", "chat"},
		{"served-free-requested-priced", "gpt-4o-mini", "ollama/llama3", "ollama", "chat"},
		{"served-free", "unknown", "ollama/llama3", "ollama", "chat"},
		{"requested-free", "ollama/llama3", "unknown", "ollama", "chat"},
		{"both-missing", "unknown", "other-unknown", "ollama", "chat"},
		{"input-zero-output-positive", "perplexity/pplx-7b-online", "", "perplexity", "chat"},
		{"other-positive-rate", "gemini/lyria-3-clip-preview", "", "gemini", "chat"},
	}
	for _, key := range []string{"gemini-exp-1206", "gemini-1.5-flash-exp-0827", "gemini-2.0-flash-exp", "gemini-2.0-pro-exp-02-05", "gemini-2.5-pro-exp-03-25"} {
		cases = append(cases, fixture{key, "gemini/" + key, "", "gemini", "chat"})
	}
	for _, ep := range []string{"image", "search", "speech", "transcription", "translation", "rerank", "ocr", "decision", "genai_embed"} {
		for _, key := range []string{"ollama/llama3", "gpt-4o-mini"} {
			cases = append(cases, fixture{ep + "-" + key, key, "", "test", ep})
		}
	}
	var client *redisstate.Client
	if addr := os.Getenv("TEST_REDIS_ADDR"); addr != "" {
		var err error
		client, err = redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { client.Close() })
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model, rc.ResolvedModel, rc.Provider, rc.EndpointType = tc.model, tc.served, tc.provider, tc.endpoint
			if tc.served != "" {
				rc.Flags.FallbackUsed = true
				rc.Metadata["fallback_model"] = tc.served
			}
			rc.Metadata[tenant.MetadataKeyOrgID] = "test-org"
			rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 1000, CompletionTokens: 500, TotalTokens: 1500}}
			// Non-chat responses do not also carry a chat usage body.
			if tc.endpoint != "chat" && tc.endpoint != "decision" {
				rc.Response = nil
			}
			switch tc.endpoint {
			case "embedding":
				rc.EmbeddingResponse = &models.EmbeddingResponse{Usage: &models.EmbeddingUsage{PromptTokens: 1000}}
			case "image":
				rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "fixture"}}}
			case "search":
				rc.SearchRequest = &models.SearchRequest{SearchProvider: tc.model}
				rc.SearchResponse = &models.SearchResponse{}
			case "speech", "genai_embed":
				rc.Metadata["input_characters"] = "50"
			case "transcription", "translation":
				rc.Metadata["audio_seconds"] = "3"
			case "rerank":
				rc.RerankResponse = &models.RerankResponse{}
			case "ocr":
				rc.OCRResponse = &models.OCRResponse{UsageInfo: &models.OCRUsage{PagesProcessed: 2}}
			}
			store := tenant.NewStore()
			org := &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: true, DefaultPeriod: "monthly", OrgLimit: 100}}
			if tc.name == "org-zero" {
				org.CostTracking = &tenant.CostTrackingConfig{Enabled: true, CustomPricing: map[string]*tenant.CustomPricing{tc.model: {}}}
			}
			store.Set("test-org", org)
			prices := map[string]config.CustomPricing{}
			if tc.name == "grandfather" {
				prices[tc.model] = config.CustomPricing{InputPerMTok: 2}
			}
			bill := costplugin.New(true, func() *modeldb.ModelDB { return db }, store, map[string]float64{"test-alias": 0})
			if tc.name == "alias-zero" {
				rc.Metadata["model_alias"] = "test-alias"
			}
			if tc.name == "rounded" {
				rc.Response.Usage = &models.Usage{PromptTokens: 1, TotalTokens: 1}
			}
			if tc.name == "reported" {
				bill.SetReportedCostVendors(func(*models.RequestContext) string { return "openrouter" })
				if err := json.Unmarshal([]byte(`{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":0.15}`), rc.Response.Usage); err != nil {
					t.Fatal(err)
				}
			}
			tracker := globalTracker()
			bud := New(tracker, NewPricingLookup(prices), true, store)
			var ledger *redisstate.BudgetStore
			if client != nil {
				prefix := fmt.Sprintf("test:catalogue:%d:", time.Now().UnixNano())
				ledger = redisstate.NewBudgetStore(client, prefix)
				bud.SetRedisBudget(ledger)
				t.Cleanup(func() {
					keys, _ := client.Redis().Keys(ctx, prefix+"*").Result()
					if len(keys) > 0 {
						client.Redis().Del(ctx, keys...)
					}
				})
			}
			bill.ProcessResponse(ctx, rc)
			bud.ProcessResponse(ctx, rc)
			cost, present := rc.Metadata["cost"]
			catalogue, audit := rc.Metadata["cost_catalogue"]
			global, _ := tracker.GetSpend("org", "")
			counter := bud.getOrgHierarchy("test-org").getOrCreateCounter("org", "")
			local, _ := bud.getCounterSpend(counter, "monthly", time.Now())
			var want float64
			if present {
				var err error
				want, err = strconv.ParseFloat(cost, 64)
				if err != nil {
					t.Fatal(err)
				}
			}
			if tc.endpoint == "decision" && tc.model == "gpt-4o-mini" {
				// A decision request carries chat usage; the existing independent budget
				// price applies even when no decision-endpoint price exists.
				want = 0.00045
			}
			if tc.name == "grandfather" {
				want = .002
			}
			if math.Abs(want-global) > 1e-9 || math.Abs(want-local) > 1e-9 {
				t.Errorf("global=%v org=%v want %v", global, local, want)
			}
			if ledger != nil {
				spent, _, found := ledger.GetSpend("test-org", "org", "", "monthly")
				if (want > 0 && !found) || math.Abs(spent-want) > 1e-9 {
					t.Errorf("Redis spend=%v present=%t want=%v", spent, found, want)
				}
			}
			if tc.endpoint != "chat" && tc.endpoint != "embedding" && (present || rc.Metadata["cost_source"] != "unpriced") {
				t.Errorf("unexpected endpoint metadata: %v", rc.Metadata)
			}
			if tc.name == "reported" && (audit || cost != "0.150000" || rc.Metadata["cost_source"] != "provider_reported") {
				t.Errorf("unexpected reported metadata: %v", rc.Metadata)
			}
			if tc.name == "org-zero" && (present || rc.Metadata["cost_source"] != "") {
				t.Errorf("org zero: %v", rc.Metadata)
			}
			if (tc.name == "alias-zero" || tc.name == "rounded") && cost != "0.000000" {
				t.Errorf("expected priced zero, got %q", cost)
			}
			t.Logf("PARITY %s source=%q cost=%q present=%t cost_catalogue=%q present=%t global=%.6f org=%.6f", tc.name, rc.Metadata["cost_source"], cost, present, catalogue, audit, global, local)
		})
	}
}
