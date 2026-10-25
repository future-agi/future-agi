package gemini

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// mapSizeToAspectRatio tests
// ---------------------------------------------------------------------------

func TestMapSizeToAspectRatio(t *testing.T) {
	tests := []struct {
		size string
		want string
	}{
		{"256x256", "1:1"},
		{"512x512", "1:1"},
		{"1024x1024", "1:1"},
		{"1792x1024", "16:9"},
		{"1024x1792", "9:16"},
		{"1536x1024", "3:2"},
		{"1024x1536", "2:3"},
		{"unknown", "1:1"}, // default
		{"", "1:1"},        // empty defaults
	}
	for _, tc := range tests {
		got := mapSizeToAspectRatio(tc.size)
		if got != tc.want {
			t.Errorf("mapSizeToAspectRatio(%q) = %q, want %q", tc.size, got, tc.want)
		}
	}
}

// ---------------------------------------------------------------------------
// translateImageResponse tests
// ---------------------------------------------------------------------------

func TestTranslateImageResponse(t *testing.T) {
	gemResp := &geminiPredictResponse{
		Predictions: []geminiPrediction{
			{BytesBase64Encoded: "aGVsbG8=", MimeType: "image/png"},
			{BytesBase64Encoded: "d29ybGQ=", MimeType: "image/png"},
		},
	}

	resp := translateImageResponse(gemResp)

	if resp.Created == 0 {
		t.Error("Created should be non-zero")
	}
	if len(resp.Data) != 2 {
		t.Fatalf("Data length = %d, want 2", len(resp.Data))
	}
	if resp.Data[0].B64JSON != "aGVsbG8=" {
		t.Errorf("Data[0].B64JSON = %q, want %q", resp.Data[0].B64JSON, "aGVsbG8=")
	}
	if resp.Data[1].B64JSON != "d29ybGQ=" {
		t.Errorf("Data[1].B64JSON = %q, want %q", resp.Data[1].B64JSON, "d29ybGQ=")
	}
}

func TestTranslateImageResponse_Empty(t *testing.T) {
	gemResp := &geminiPredictResponse{
		Predictions: nil,
	}

	resp := translateImageResponse(gemResp)
	if resp.Data != nil {
		t.Errorf("Data = %v, want nil for empty predictions", resp.Data)
	}
}

// ---------------------------------------------------------------------------
// Integration tests — image generation
// ---------------------------------------------------------------------------

// imageGenUpstream is a Gemini API upstream answering generateContent with
// one inline image per call, recording every path and request it got.
type imageGenUpstream struct {
	*httptest.Server
	mu       sync.Mutex
	paths    []string
	keys     []string // x-goog-api-key, where the key belongs
	queries  []string // the raw query string, which must never carry the key
	auths    []string
	requests []geminiImageRequest
	reply    string // the generateContent answer; empty: one PNG
}

func startImageGenUpstream(t *testing.T) *imageGenUpstream {
	t.Helper()
	u := &imageGenUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req geminiImageRequest
		_ = json.Unmarshal(body, &req)
		u.mu.Lock()
		u.paths = append(u.paths, r.URL.Path)
		u.keys = append(u.keys, r.Header.Get("x-goog-api-key"))
		u.queries = append(u.queries, r.URL.RawQuery)
		u.auths = append(u.auths, r.Header.Get("Authorization"))
		u.requests = append(u.requests, req)
		n := len(u.paths)
		reply := u.reply
		u.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if reply == "" {
			reply = fmt.Sprintf(`{"candidates":[{"content":{"role":"model","parts":[{"text":"Here is your image."},{"inlineData":{"mimeType":"image/png","data":"aW1hZ2Ut%d"}}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":8,"candidatesTokenCount":1290,"totalTokenCount":1298}}`, n)
		}
		io.WriteString(w, reply)
	}))
	t.Cleanup(u.Close)
	return u
}

// TestGeminiImageUsesGenerateContent: the Gemini API's image models generate
// through generateContent with image output, never Imagen's :predict (the
// Gemini API shut Imagen down), one call per image, with the size mapped to
// an aspect ratio and the API key in the x-goog-api-key header, never in the
// URL.
func TestGeminiImageUsesGenerateContent(t *testing.T) {
	for _, model := range []string{"gemini-3.1-flash-image", "gemini/gemini-3-pro-image", "gemini-nano-banana-2.1"} {
		t.Run(model, func(t *testing.T) {
			up := startImageGenUpstream(t)
			p := newTestProvider(t, up.URL)
			n := 2
			resp, err := p.CreateImage(context.Background(), &models.ImageRequest{
				Model: model, Prompt: "a lighthouse at dusk", N: &n, Size: "1792x1024",
			})
			if err != nil {
				t.Fatalf("CreateImage() error = %v", err)
			}
			bare := strings.TrimPrefix(model, "gemini/")
			if len(up.paths) != 2 {
				t.Fatalf("upstream calls = %d, want 2 (one per image)", len(up.paths))
			}
			for i, path := range up.paths {
				if path != "/v1beta/models/"+bare+":generateContent" {
					t.Errorf("call %d path = %q, want /v1beta/models/%s:generateContent", i, path, bare)
				}
				if strings.Contains(path, ":predict") {
					t.Errorf("call %d used Imagen's :predict", i)
				}
				if up.keys[i] != "test-api-key" {
					t.Errorf("call %d x-goog-api-key = %q, want test-api-key", i, up.keys[i])
				}
				if strings.Contains(up.queries[i], "test-api-key") || strings.Contains(up.queries[i], "key=") {
					t.Errorf("call %d sent the key in the query: %q", i, up.queries[i])
				}
				req := up.requests[i]
				if len(req.Contents) != 1 || len(req.Contents[0].Parts) != 1 || req.Contents[0].Parts[0].Text != "a lighthouse at dusk" {
					t.Errorf("call %d contents = %+v, want the prompt as one user text part", i, req.Contents)
				}
				if got := req.GenerationConfig.ResponseModalities; len(got) != 1 || got[0] != "IMAGE" {
					t.Errorf("call %d responseModalities = %v, want [IMAGE]", i, got)
				}
				if ic := req.GenerationConfig.ImageConfig; ic == nil || ic.AspectRatio != "16:9" {
					t.Errorf("call %d imageConfig = %+v, want aspectRatio 16:9", i, ic)
				}
			}
			if len(resp.Data) != 2 || resp.Data[0].B64JSON != "aW1hZ2Ut1" || resp.Data[1].B64JSON != "aW1hZ2Ut2" {
				t.Errorf("Data = %+v, want the two inline images in order", resp.Data)
			}
			if resp.Created == 0 {
				t.Error("Created should be non-zero")
			}
		})
	}
}

// A response without an image (a safety block) is an error, never an empty
// success.
func TestGeminiImageNoImageIsAnError(t *testing.T) {
	up := startImageGenUpstream(t)
	up.reply = `{"candidates":[{"content":{"role":"model","parts":[{"text":"I can't make that."}]},"finishReason":"IMAGE_SAFETY"}]}`
	p := newTestProvider(t, up.URL)

	_, err := p.CreateImage(context.Background(), &models.ImageRequest{Model: "gemini-3.1-flash-image", Prompt: "x"})
	apiErr, ok := err.(*models.APIError)
	if !ok || apiErr.Status != http.StatusBadGateway || apiErr.Code != "no_image_returned" || !strings.Contains(apiErr.Message, "IMAGE_SAFETY") {
		t.Fatalf("err = %v, want a 502 no_image_returned naming IMAGE_SAFETY", err)
	}
}

// More than ten images is refused before any upstream call (each image is
// one call); an n of 0 or less is one image.
func TestGeminiImageNOutOfRangeRefused(t *testing.T) {
	up := startImageGenUpstream(t)
	p := newTestProvider(t, up.URL)
	for _, n := range []int{11, 100} {
		n := n
		_, err := p.CreateImage(context.Background(), &models.ImageRequest{Model: "gemini-3.1-flash-image", Prompt: "x", N: &n})
		if apiErr, ok := err.(*models.APIError); !ok || apiErr.Status != http.StatusBadRequest || apiErr.Code != "invalid_n" {
			t.Errorf("n=%d: err = %v, want 400 invalid_n", n, err)
		}
	}
	if len(up.paths) != 0 {
		t.Errorf("upstream calls = %d, want 0", len(up.paths))
	}

	for i, n := range []int{0, -1} {
		n := n
		resp, err := p.CreateImage(context.Background(), &models.ImageRequest{Model: "gemini-3.1-flash-image", Prompt: "x", N: &n})
		if err != nil {
			t.Fatalf("n=%d: %v, want one image", n, err)
		}
		if len(resp.Data) != 1 || len(up.paths) != i+1 {
			t.Errorf("n=%d: %d images over %d upstream calls in total, want 1 image from one more call", n, len(resp.Data), len(up.paths))
		}
	}
}

// A transport failure names the request without its URL, and never the
// Gemini API key.
func TestGeminiImageErrorHidesAPIKey(t *testing.T) {
	closed := httptest.NewServer(http.NotFoundHandler())
	closed.Close()
	p := newTestProvider(t, closed.URL)
	_, err := p.CreateImage(context.Background(), &models.ImageRequest{Model: "gemini-3.1-flash-image", Prompt: "x"})
	if err == nil {
		t.Fatal("a closed upstream answered")
	}
	if msg := err.Error(); strings.Contains(msg, "test-api-key") || !strings.Contains(msg, "gemini: image request failed: Post") {
		t.Errorf("transport error = %q, want the failure without the key", msg)
	}

	p.baseURL = "http://[::1" // unparseable: the request is never built
	_, err = p.CreateImage(context.Background(), &models.ImageRequest{Model: "gemini-3.1-flash-image", Prompt: "x"})
	if err == nil || strings.Contains(err.Error(), "test-api-key") || !strings.Contains(err.Error(), "creating image request") {
		t.Errorf("bad URL error = %v, want the failure without the key", err)
	}
}

// The shutdown calendar's host for a Gemini-format provider is "vertex"
// exactly when this adapter calls Vertex AI: modeldb.DeprecationHost repeats
// isVertexAI's test (modeldb cannot import this package), and this keeps
// the two from drifting apart.
func TestDeprecationHostAgreesWithVertexDetection(t *testing.T) {
	for _, base := range []string{
		"https://generativelanguage.googleapis.com",
		"https://us-central1-aiplatform.googleapis.com",
		"https://europe-west4-aiplatform.googleapis.com/v1/projects/p/locations/europe-west4",
		"https://aiplatform.googleapis.com/v1beta1/projects/p/locations/global",
		"http://127.0.0.1:8080",
	} {
		host := modeldb.DeprecationHost("gemini", base)
		if (host == "vertex") != isVertexAI(base) {
			t.Errorf("%s: calendar host %q, isVertexAI %v", base, host, isVertexAI(base))
		}
	}
}

// newVertexTestProvider is a Vertex AI provider (project and location in the
// base URL) whose token is "vertex-token".
func newVertexTestProvider(serverURL string, client *http.Client) *Provider {
	return &Provider{
		id: "test-vertex", baseURL: serverURL + "/v1beta1/projects/p1/locations/us-central1",
		apiKey: "vertex-token", vertexAI: true, httpClient: client, semaphore: make(chan struct{}, 4),
	}
}

// On Vertex AI, Imagen keeps its :predict endpoint, with all n images in
// one call; Gemini image models use generateContent there too.
func TestVertexImagenKeepsPredict(t *testing.T) {
	var paths, auths []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		auths = append(auths, r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, ":predict") {
			var req geminiPredictRequest
			body, _ := io.ReadAll(r.Body)
			if err := json.Unmarshal(body, &req); err != nil || req.Parameters == nil || req.Parameters.SampleCount != 3 ||
				req.Parameters.AspectRatio != "9:16" || req.Instances[0].Prompt != "landscape" {
				t.Errorf("predict request = %s", body)
			}
			io.WriteString(w, `{"predictions":[{"bytesBase64Encoded":"YQ==","mimeType":"image/png"},{"bytesBase64Encoded":"Yg==","mimeType":"image/png"},{"bytesBase64Encoded":"Yw==","mimeType":"image/png"}]}`)
			return
		}
		io.WriteString(w, `{"candidates":[{"content":{"parts":[{"inlineData":{"mimeType":"image/png","data":"Z2VtaW5p"}}]}}]}`)
	}))
	defer server.Close()
	p := newVertexTestProvider(server.URL, server.Client())

	n := 3
	resp, err := p.CreateImage(context.Background(), &models.ImageRequest{Model: "imagen-4.0-generate-001", Prompt: "landscape", N: &n, Size: "1024x1792"})
	if err != nil {
		t.Fatalf("Imagen on Vertex: %v", err)
	}
	if len(resp.Data) != 3 || len(paths) != 1 || paths[0] != "/v1beta1/projects/p1/locations/us-central1/publishers/google/models/imagen-4.0-generate-001:predict" {
		t.Errorf("Imagen on Vertex: %d images over paths %v, want 3 from one :predict call", len(resp.Data), paths)
	}

	resp, err = p.CreateImage(context.Background(), &models.ImageRequest{Model: "gemini-3-pro-image", Prompt: "x"})
	if err != nil {
		t.Fatalf("Gemini image on Vertex: %v", err)
	}
	if len(paths) != 2 || paths[1] != "/v1beta1/projects/p1/locations/us-central1/publishers/google/models/gemini-3-pro-image:generateContent" {
		t.Errorf("Gemini image on Vertex path = %v, want generateContent", paths)
	}
	if len(resp.Data) != 1 || resp.Data[0].B64JSON != "Z2VtaW5p" {
		t.Errorf("Gemini image on Vertex data = %+v", resp.Data)
	}
	for i, a := range auths {
		if a != "Bearer vertex-token" {
			t.Errorf("call %d Authorization = %q, want the Vertex bearer token", i, a)
		}
	}
}

func TestIntegration_CreateImage_ProviderError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(geminiErrorResponse{
			Error: geminiErrorDetail{
				Code:    404,
				Message: "Model not found",
				Status:  "NOT_FOUND",
			},
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	_, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:  "invalid-model",
		Prompt: "test",
	})
	if err == nil {
		t.Fatal("expected error from provider")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != http.StatusNotFound {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusNotFound)
	}
}
