package cost

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// bundledPlugin prices from the bundled model database, which carries the
// image, speech, transcription and rerank prices.
func bundledPlugin() *Plugin {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.SetManagedMarkup(5)
	p.SetFees(Fees{ProcessingFeeBps: 320, UpstreamFeeBps: map[string]int{"openai": 0}})
	return p
}

func modalityRC(endpoint, model string) *models.RequestContext {
	return &models.RequestContext{
		RequestID:    "req-m",
		Model:        model,
		Provider:     "openai",
		EndpointType: endpoint,
		Metadata:     map[string]string{},
		Timings:      map[string]time.Duration{},
	}
}

func TestImageSpeechTranscriptionRerankCost(t *testing.T) {
	// dall-e-3's size and quality prices are litellm's per-pixel rates
	// (7.629e-08 for hd 1024x1024, 4.359e-08 for standard 1792x1024), which
	// price an image to within 0.01% of the $0.08 list price; a catalog card
	// with a per-image price wins over them (TestCatalogImagePriceWins).
	two := 2
	cases := []struct {
		name  string
		rc    func() *models.RequestContext
		want  string // the cost metadata, in USD
		micro int64
	}{
		{"two hd 1024x1024 dall-e-3 images", func() *models.RequestContext {
			rc := modalityRC("image", "dall-e-3")
			rc.ImageRequest = &models.ImageRequest{Model: "dall-e-3", N: &two, Size: "1024x1024", Quality: "hd"}
			rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "a"}, {URL: "b"}}}
			return rc
		}, "0.159992", 159_992},
		{"one standard 1792x1024 dall-e-3 image", func() *models.RequestContext {
			rc := modalityRC("image", "dall-e-3")
			rc.ImageRequest = &models.ImageRequest{Model: "dall-e-3", Size: "1792x1024"}
			rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "a"}}}
			return rc
		}, "0.079988", 79_988},
		{"2,000 characters of tts-1 at $15 per 1M", func() *models.RequestContext {
			rc := modalityRC("speech", "tts-1")
			rc.SpeechRequest = &models.SpeechRequest{Model: "tts-1"}
			rc.Metadata["input_characters"] = "2000"
			return rc
		}, "0.030000", 30_000},
		{"90.5 seconds of whisper-1 at $0.0001 a second", func() *models.RequestContext {
			rc := modalityRC("transcription", "whisper-1")
			rc.TranscriptionResp = &models.TranscriptionResponse{Text: "hi"}
			rc.Metadata["audio_seconds"] = "90.500000"
			return rc
		}, "0.009050", 9050},
		{"250 documents are three rerank search units at $0.002", func() *models.RequestContext {
			rc := modalityRC("rerank", "rerank-english-v3.0")
			rc.Provider = "cohere"
			rc.RerankRequest = &models.RerankRequest{Documents: make([]string, 250)}
			// The units come from the request's 250 documents; the response's
			// billed_units are not read.
			rc.RerankResponse = &models.RerankResponse{Meta: &models.RerankMeta{BilledUnits: map[string]int{"search_units": 4000}}}
			return rc
		}, "0.006000", 6000},
		{"a rerank of up to 100 documents is one search unit", func() *models.RequestContext {
			rc := modalityRC("rerank", "rerank-english-v3.0")
			rc.Provider = "cohere"
			rc.RerankRequest = &models.RerankRequest{Documents: make([]string, 100)}
			rc.RerankResponse = &models.RerankResponse{}
			return rc
		}, "0.002000", 2000},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rc := c.rc()
			bundledPlugin().ProcessResponse(context.Background(), rc)
			if rc.Metadata["cost"] != c.want || rc.Billing.UpstreamMicros != c.micro || rc.Metadata["cost_source"] != "modeldb" {
				t.Fatalf("cost %q, upstream %d, source %q; want %s, %d, modeldb",
					rc.Metadata["cost"], rc.Billing.UpstreamMicros, rc.Metadata["cost_source"], c.want, c.micro)
			}
		})
	}

	// A managed image is billed with the markup like any other attempt:
	// 79,996 micros × 1.05 = 83,995.8 → 83,996.
	rc := modalityRC("image", "dall-e-3")
	rc.Metadata["billing_mode"] = "managed"
	rc.ImageRequest = &models.ImageRequest{Model: "dall-e-3", Size: "1024x1024", Quality: "hd"}
	rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "a"}}}
	bundledPlugin().ProcessResponse(context.Background(), rc)
	if rc.Billing.BilledMicros != 83_996 || rc.Metadata["cost"] != "0.083996" {
		t.Fatalf("managed image billed %d (%q), want 83996", rc.Billing.BilledMicros, rc.Metadata["cost"])
	}
}

func TestUnmeasurableModalityIsUnpriced(t *testing.T) {
	// Audio whose length the gateway cannot read cannot be priced per second.
	rc := modalityRC("transcription", "whisper-1")
	rc.TranscriptionResp = &models.TranscriptionResponse{Text: "hi"}
	bundledPlugin().ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "" || rc.Metadata["cost_source"] != "unpriced" {
		t.Fatalf("metadata = %v; want an unpriced transcription", rc.Metadata)
	}

	// A chat model has no image price.
	rc = modalityRC("image", "gpt-4o")
	rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "a"}}}
	bundledPlugin().ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "" || rc.Metadata["cost_source"] != "unpriced" {
		t.Fatalf("metadata = %v; want an unpriced image", rc.Metadata)
	}
}

func TestCatalogImagePriceWins(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.SetPricer(NewPricer(func() *modeldb.ModelDB { return db },
		fixedLookup(models.PriceCard{Image: 0.05, Source: models.PriceSourceCatalog})))
	rc := modalityRC("image", "dall-e-3")
	rc.ImageRequest = &models.ImageRequest{Model: "dall-e-3", Size: "1024x1024", Quality: "hd"}
	rc.ImageResponse = &models.ImageResponse{Data: []models.ImageData{{URL: "a"}, {URL: "b"}}}

	p.ProcessResponse(context.Background(), rc)

	if rc.Billing.UpstreamMicros != 100_000 || rc.Metadata["cost_source"] != "catalog" {
		t.Fatalf("upstream %d, source %q; want the catalog's $0.05 per image", rc.Billing.UpstreamMicros, rc.Metadata["cost_source"])
	}
}
