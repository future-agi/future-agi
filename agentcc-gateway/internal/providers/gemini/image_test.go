package gemini

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

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

func TestIntegration_CreateImage(t *testing.T) {
	gemResp := geminiPredictResponse{
		Predictions: []geminiPrediction{
			{BytesBase64Encoded: "dGVzdA==", MimeType: "image/png"},
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("Method = %q, want POST", r.Method)
		}
		if !strings.Contains(r.URL.Path, ":predict") {
			t.Errorf("URL path = %q, want to contain :predict", r.URL.Path)
		}
		if r.URL.Query().Get("key") != "test-api-key" {
			t.Errorf("key = %q, want test-api-key", r.URL.Query().Get("key"))
		}

		body, _ := io.ReadAll(r.Body)
		var req geminiPredictRequest
		if err := json.Unmarshal(body, &req); err != nil {
			t.Fatalf("unmarshal request: %v", err)
		}
		if len(req.Instances) != 1 {
			t.Fatalf("instances length = %d, want 1", len(req.Instances))
		}
		if req.Instances[0].Prompt != "a cute cat" {
			t.Errorf("prompt = %q, want %q", req.Instances[0].Prompt, "a cute cat")
		}
		if req.Parameters == nil {
			t.Fatal("parameters should not be nil")
		}
		if req.Parameters.SampleCount != 1 {
			t.Errorf("sampleCount = %d, want 1", req.Parameters.SampleCount)
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(gemResp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	resp, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:  "gemini/imagen-3.0-generate-001",
		Prompt: "a cute cat",
	})
	if err != nil {
		t.Fatalf("CreateImage() error = %v", err)
	}

	if len(resp.Data) != 1 {
		t.Fatalf("Data length = %d, want 1", len(resp.Data))
	}
	if resp.Data[0].B64JSON != "dGVzdA==" {
		t.Errorf("B64JSON = %q, want %q", resp.Data[0].B64JSON, "dGVzdA==")
	}
	if resp.Created == 0 {
		t.Error("Created should be non-zero")
	}
}

func TestIntegration_CreateImage_WithNAndSize(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req geminiPredictRequest
		if err := json.Unmarshal(body, &req); err != nil {
			t.Fatalf("unmarshal request: %v", err)
		}

		if req.Parameters == nil {
			t.Fatal("parameters should not be nil")
		}
		if req.Parameters.SampleCount != 3 {
			t.Errorf("sampleCount = %d, want 3", req.Parameters.SampleCount)
		}
		if req.Parameters.AspectRatio != "16:9" {
			t.Errorf("aspectRatio = %q, want %q", req.Parameters.AspectRatio, "16:9")
		}

		gemResp := geminiPredictResponse{
			Predictions: []geminiPrediction{
				{BytesBase64Encoded: "YQ==", MimeType: "image/png"},
				{BytesBase64Encoded: "Yg==", MimeType: "image/png"},
				{BytesBase64Encoded: "Yw==", MimeType: "image/png"},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(gemResp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	n := 3

	resp, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:  "imagen-3.0-generate-001",
		Prompt: "landscape",
		N:      &n,
		Size:   "1792x1024",
	})
	if err != nil {
		t.Fatalf("CreateImage() error = %v", err)
	}

	if len(resp.Data) != 3 {
		t.Errorf("Data length = %d, want 3", len(resp.Data))
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

func TestIntegration_CreateImage_ModelPrefixStripping(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify that the model prefix "gemini/" was stripped from the URL.
		if !strings.Contains(r.URL.Path, "imagen-3.0-generate-001") {
			t.Errorf("URL path = %q, want to contain imagen-3.0-generate-001", r.URL.Path)
		}
		if strings.Contains(r.URL.Path, "gemini/") {
			t.Errorf("URL path = %q, should not contain provider prefix", r.URL.Path)
		}

		gemResp := geminiPredictResponse{
			Predictions: []geminiPrediction{
				{BytesBase64Encoded: "dGVzdA==", MimeType: "image/png"},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(gemResp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	_, err := p.CreateImage(context.Background(), &models.ImageRequest{
		Model:  "gemini/imagen-3.0-generate-001",
		Prompt: "test",
	})
	if err != nil {
		t.Fatalf("CreateImage() error = %v", err)
	}
}
