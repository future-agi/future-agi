package gemini

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// imagesGap is the gap TestContractImagesOnGeminiAPI pins, in the
// "known gap <ID>: <reason>" form the contract fixtures use (the coverage
// package's KNOWN_GAPS.md reads it from here). The gemini images cell of
// contractcoverage/exemptions.json is not_served for the same reason and
// carries the sources.
const imagesGap = "known gap G34: on the Gemini API, /v1/images/generations has nothing it can call: CreateImage " +
	"sends every model to Imagen's :predict, which the Gemini API no longer serves, and never to generateContent " +
	"with IMAGE output, where the Gemini API's own image models (such as gemini-2.5-flash-image) generate images. " +
	"The official SDKs refuse image generation in Gemini Developer API mode and Google's cookbook says Imagen is " +
	"deprecated in the Gemini API; Imagen is served on Vertex AI, where vertex@imagen-4.0-generate-001's " +
	"images_basic pins the same :predict call."

// geminiAPIBase is the Gemini API's address, as the contract recorder and
// config.example.yaml configure it.
const geminiAPIBase = "https://generativelanguage.googleapis.com"

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// TestContractImagesOnGeminiAPI pins where CreateImage sends an image request
// on the Gemini API today: Imagen's :predict, for an Imagen model and for a
// Gemini image model alike (gap G34). The upstream is never dialled: the
// transport records the request and answers the 404 an unserved model gets.
// Fixing G34 (routing image models to generateContent, or refusing the
// endpoint on the Gemini API) fails this test: delete the gap, and give the
// gemini images cell a contract case or a new reason in
// contractcoverage/exemptions.json.
func TestContractImagesOnGeminiAPI(t *testing.T) {
	for _, model := range []string{"imagen-4.0-generate-001", "gemini-2.5-flash-image"} {
		t.Run(model, func(t *testing.T) {
			p, err := New("gemini", config.ProviderConfig{BaseURL: geminiAPIBase, APIKey: "test-key"})
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			if p.vertexAI {
				t.Fatalf("%s is taken for Vertex AI", geminiAPIBase)
			}
			var calls []string
			p.httpClient.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
				calls = append(calls, r.Method+" "+r.URL.String())
				return &http.Response{
					StatusCode: http.StatusNotFound,
					Header:     http.Header{"Content-Type": {"application/json"}},
					Body:       io.NopCloser(strings.NewReader(`{"error":{"code":404,"message":"not found","status":"NOT_FOUND"}}`)),
					Request:    r,
				}, nil
			})

			_, err = p.CreateImage(context.Background(), &models.ImageRequest{Model: "gemini/" + model, Prompt: "a lighthouse"})
			want := "POST " + geminiAPIBase + "/v1beta/models/" + model + ":predict"
			if len(calls) != 1 || calls[0] != want {
				t.Errorf("CreateImage on the Gemini API called %q, not [%q]. This pins %q: if the adapter no longer calls Imagen's :predict there, the gap may be fixed (see the test's comment)", calls, want, imagesGap)
			}
			if err == nil {
				t.Errorf("CreateImage succeeded on a 404")
			}
		})
	}
}
