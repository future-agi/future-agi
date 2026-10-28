package cost

import (
	"context"
	"fmt"
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// endpointDB prices each non-token endpoint the way the bundled table does.
func endpointDB() *modeldb.ModelDB {
	return modeldb.New(map[string]*modeldb.ModelInfo{
		"gpt-image-1":                {ID: "gpt-image-1", Pricing: modeldb.PricingInfo{InputPerToken: 5e-6, InputPerImageToken: 1e-5, OutputPerImageToken: 4e-5}},
		"hd/1024-x-1024/dall-e-3":    {ID: "hd/1024-x-1024/dall-e-3", Pricing: modeldb.PricingInfo{InputPerPixel: 7.629e-8}},
		"dall-e-3":                   {ID: "dall-e-3", Pricing: modeldb.PricingInfo{InputPerImage: 0.04}},
		"gemini-image":               {ID: "gemini-image", Pricing: modeldb.PricingInfo{OutputPerImage: 0.039}},
		"rerank-v3.5":                {ID: "rerank-v3.5", Mode: modeldb.ModeRerank, Pricing: modeldb.PricingInfo{InputPerQuery: 0.002}},
		"mistral/mistral-ocr-latest": {ID: "mistral/mistral-ocr-latest", Mode: modeldb.ModeOCR, Pricing: modeldb.PricingInfo{PerPage: 0.001}},
		"perplexity/sonar":           {ID: "perplexity/sonar", Pricing: modeldb.PricingInfo{InputPerToken: 1e-6, OutputPerToken: 1e-6}},
		// Perplexity's Agent API rates: its own Sonar, and a Google model it
		// resells above Google's price.
		"perplexity/perplexity/sonar":                    {ID: "perplexity/perplexity/sonar", Pricing: modeldb.PricingInfo{InputPerToken: 2.5e-7, OutputPerToken: 2.5e-6}},
		"perplexity/google/gemini-3.6-flash":             {ID: "perplexity/google/gemini-3.6-flash", Pricing: modeldb.PricingInfo{InputPerToken: 1.5e-6, OutputPerToken: 7.5e-6}},
		"gemini-3.6-flash":                               {ID: "gemini-3.6-flash", Pricing: modeldb.PricingInfo{InputPerToken: 7.5e-7, OutputPerToken: 3.75e-6}},
		"gemini/gemini-4-argon":                          {ID: "gemini/gemini-4-argon", Pricing: modeldb.PricingInfo{InputPerToken: 2e-6, OutputPerToken: 1e-5}},
		"vertex_ai/gemini-4-argon":                       {ID: "vertex_ai/gemini-4-argon", Pricing: modeldb.PricingInfo{InputPerToken: 4e-6, OutputPerToken: 2e-5}},
		"fireworks_ai/accounts/fireworks/models/glm-5p3": {ID: "fireworks_ai/accounts/fireworks/models/glm-5p3", Pricing: modeldb.PricingInfo{InputPerToken: 1.4e-6, OutputPerToken: 4.4e-6}},
		"text-embedding-gecko":                           {ID: "text-embedding-gecko", Mode: modeldb.ModeEmbedding, Pricing: modeldb.PricingInfo{InputPerCharacter: 2.5e-8}},
		"gemini-embedding-001":                           {ID: "gemini-embedding-001", Mode: modeldb.ModeEmbedding, Pricing: modeldb.PricingInfo{InputPerToken: 1.5e-7}},
	}, nil)
}

func endpointRC(endpoint, model string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.EndpointType = endpoint
	rc.Model = model
	return rc
}

func priced(t *testing.T, rc *models.RequestContext, want float64) {
	t.Helper()
	db := endpointDB()
	pricedBy(t, New(true, func() *modeldb.ModelDB { return db }, nil, nil), rc, want)
}

func pricedBy(t *testing.T, p *Plugin, rc *models.RequestContext, want float64) {
	t.Helper()
	p.ProcessResponse(context.Background(), rc)
	got := rc.Metadata["cost"]
	if want == 0 {
		if got != "" || rc.Metadata["cost_source"] != CostSourceUnpriced {
			t.Fatalf("cost = %q, cost_source = %q; want no cost, marked unpriced", got, rc.Metadata["cost_source"])
		}
		return
	}
	// The cost is recorded to six decimals.
	var cost float64
	if _, err := fmtSscan(got, &cost); err != nil || math.Abs(cost-want) > 1e-6 {
		t.Fatalf("cost = %q, want %.6f", got, want)
	}
}

// Images were never priced, so an image call looked free.
func TestImageCost(t *testing.T) {
	t.Run("billed in tokens", func(t *testing.T) {
		rc := endpointRC("image", "gpt-image-1")
		rc.ImageResponse = &models.ImageResponse{
			Data: []models.ImageData{{B64JSON: "x"}},
			Usage: &models.ImageUsage{InputTokens: 60, OutputTokens: 1000, TotalTokens: 1060,
				InputTokensDetails: &models.ImageInputDetails{TextTokens: 50, ImageTokens: 10}},
		}
		priced(t, rc, 50*5e-6+10*1e-5+1000*4e-5)
	})
	t.Run("per pixel, by size and quality", func(t *testing.T) {
		rc := endpointRC("image", "dall-e-3")
		rc.ImageRequest = &models.ImageRequest{Model: "dall-e-3", Size: "1024x1024", Quality: "hd"}
		rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "a"}, {URL: "b"}}}
		priced(t, rc, 7.629e-8*1024*1024*2)
	})
	t.Run("per image, at a size the table does not list", func(t *testing.T) {
		rc := endpointRC("image", "dall-e-3")
		rc.ImageRequest = &models.ImageRequest{Model: "dall-e-3", Size: "512x512"}
		rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "a"}}}
		priced(t, rc, 0.04)
	})
	t.Run("per image, output price", func(t *testing.T) {
		rc := endpointRC("image", "gemini-image")
		rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{B64JSON: "a"}, {B64JSON: "b"}, {B64JSON: "c"}}}
		priced(t, rc, 0.039*3)
	})
	t.Run("no price: marked unpriced", func(t *testing.T) {
		rc := endpointRC("image", "some-image-model")
		rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "a"}}}
		priced(t, rc, 0)
	})
}

func TestRerankCost(t *testing.T) {
	rc := endpointRC("rerank", "rerank-v3.5")
	rc.RerankResponse = &models.RerankResponse{Meta: &models.RerankMeta{BilledUnits: map[string]int{"search_units": 3}}}
	priced(t, rc, 3*0.002)

	rc = endpointRC("rerank", "rerank-v3.5")
	rc.RerankResponse = &models.RerankResponse{}
	priced(t, rc, 0.002) // no billed units reported: one call
}

// The table lists OCR under the provider ("mistral/mistral-ocr-latest") while
// the request names the bare model; the lookup must add the prefix.
func TestOCRCostByPagesUnderTheProviderKey(t *testing.T) {
	rc := endpointRC("ocr", "mistral-ocr-latest")
	rc.Provider = "mistral"
	rc.OCRResponse = &models.OCRResponse{UsageInfo: &models.OCRUsage{PagesProcessed: 12}}
	priced(t, rc, 12*0.001)

	rc = endpointRC("ocr", "mistral-ocr-latest")
	rc.Provider = "mistral"
	rc.OCRResponse = &models.OCRResponse{Pages: []models.OCRPage{{}, {}}}
	priced(t, rc, 2*0.001) // no usage reported: the pages returned
}

// The same prefix lookup prices a chat model the table keys by provider, as
// Perplexity's sonar.
func TestChatCostUnderTheProviderKey(t *testing.T) {
	rc := endpointRC("chat", "sonar")
	rc.Provider = "perplexity"
	rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 100, CompletionTokens: 50}}
	priced(t, rc, 150*1e-6)
}

// The table spells some providers its own way: a Fireworks model is keyed
// under fireworks_ai, and was priced at zero under the gateway's "fireworks".
func TestChatCostUnderTheTablesProviderName(t *testing.T) {
	rc := endpointRC("chat", "accounts/fireworks/models/glm-5p3")
	rc.Provider = "fireworks"
	rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 1000, CompletionTokens: 100}}
	priced(t, rc, 1000*1.4e-6+100*4.4e-6)
}

// Perplexity's Agent API names models with their vendor's prefix. They are
// priced at Perplexity's rate, not at the vendor's own.
func TestChatCostUnderTheResellersPrice(t *testing.T) {
	for _, tc := range []struct {
		model, provider string
		want            float64
	}{
		{"google/gemini-3.6-flash", "perplexity", 1000*1.5e-6 + 100*7.5e-6},
		{"perplexity/sonar", "perplexity", 1000*2.5e-7 + 100*2.5e-6},
		// The legacy Sonar API names the model bare.
		{"sonar", "perplexity", 1000*1e-6 + 100*1e-6},
		// The vendor's own provider keeps the vendor's price.
		{"google/gemini-3.6-flash", "gemini", 1000*7.5e-7 + 100*3.75e-6},
	} {
		rc := endpointRC("chat", tc.model)
		rc.Provider = tc.provider
		rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 1000, CompletionTokens: 100}}
		t.Run(tc.provider+"/"+tc.model, func(t *testing.T) { priced(t, rc, tc.want) })
	}
}

// The table lists gemini-4-argon only under "gemini/" and "vertex_ai/". The
// provider's ID need not be either: the provider states the table's name for
// itself. One that states none is not guessed at.
func TestChatCostUnderTheNameTheProviderStates(t *testing.T) {
	tables := map[string]string{"vertex_ai_global": "vertex_ai", "google": "gemini"}
	for _, tc := range []struct {
		provider string
		want     float64
	}{
		{"vertex_ai_global", 1000*4e-6 + 100*2e-5},
		{"google", 1000*2e-6 + 100*1e-5},
		{"custom", 0},
	} {
		t.Run(tc.provider, func(t *testing.T) {
			db := endpointDB()
			p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
			var asked [2]string
			p.SetPriceTables(func(orgID, providerID string) string {
				asked = [2]string{orgID, providerID}
				return tables[providerID]
			})
			rc := endpointRC("chat", "gemini-4-argon")
			rc.Provider = tc.provider
			rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
			rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 1000, CompletionTokens: 100}}
			pricedBy(t, p, rc, tc.want)
			if asked != [2]string{"org-1", tc.provider} {
				t.Errorf("asked for the name of %q, want org-1's %q", asked, tc.provider)
			}
		})
	}
}

// embedContent reports no usage. A model priced per character is priced; one
// priced per token is recorded as unpriced instead of looking free.
func TestGenAIEmbedCost(t *testing.T) {
	rc := endpointRC("genai_embed", "text-embedding-gecko")
	rc.Metadata["input_characters"] = "400"
	priced(t, rc, 400*2.5e-8)

	rc = endpointRC("genai_embed", "gemini-embedding-001")
	rc.Metadata["input_characters"] = "400"
	priced(t, rc, 0)
}

func fmtSscan(s string, v *float64) (int, error) { return fmt.Sscan(s, v) }
