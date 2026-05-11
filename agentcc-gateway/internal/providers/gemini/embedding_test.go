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
// parseEmbeddingInput tests
// ---------------------------------------------------------------------------

func TestParseEmbeddingInput_SingleString(t *testing.T) {
	input := json.RawMessage(`"hello world"`)
	texts, err := parseEmbeddingInput(input)
	if err != nil {
		t.Fatalf("parseEmbeddingInput() error = %v", err)
	}
	if len(texts) != 1 || texts[0] != "hello world" {
		t.Errorf("texts = %v, want [\"hello world\"]", texts)
	}
}

func TestParseEmbeddingInput_StringArray(t *testing.T) {
	input := json.RawMessage(`["hello","world"]`)
	texts, err := parseEmbeddingInput(input)
	if err != nil {
		t.Fatalf("parseEmbeddingInput() error = %v", err)
	}
	if len(texts) != 2 || texts[0] != "hello" || texts[1] != "world" {
		t.Errorf("texts = %v, want [\"hello\", \"world\"]", texts)
	}
}

func TestParseEmbeddingInput_Empty(t *testing.T) {
	_, err := parseEmbeddingInput(nil)
	if err == nil {
		t.Error("expected error for nil input")
	}

	_, err = parseEmbeddingInput(json.RawMessage(``))
	if err == nil {
		t.Error("expected error for empty input")
	}
}

func TestParseEmbeddingInput_InvalidType(t *testing.T) {
	input := json.RawMessage(`123`)
	_, err := parseEmbeddingInput(input)
	if err == nil {
		t.Error("expected error for numeric input")
	}
}

// ---------------------------------------------------------------------------
// translateSingleEmbedResponse tests
// ---------------------------------------------------------------------------

func TestTranslateSingleEmbedResponse(t *testing.T) {
	gemResp := &geminiEmbedResponse{
		Embedding: &geminiEmbeddingValues{
			Values: []float64{0.1, 0.2, 0.3},
		},
	}

	resp := translateSingleEmbedResponse(gemResp, "text-embedding-004", "hello world")

	if resp.Object != "list" {
		t.Errorf("Object = %q, want %q", resp.Object, "list")
	}
	if resp.Model != "text-embedding-004" {
		t.Errorf("Model = %q, want %q", resp.Model, "text-embedding-004")
	}
	if len(resp.Data) != 1 {
		t.Fatalf("Data length = %d, want 1", len(resp.Data))
	}
	if resp.Data[0].Object != "embedding" {
		t.Errorf("Data[0].Object = %q, want %q", resp.Data[0].Object, "embedding")
	}
	if resp.Data[0].Index != 0 {
		t.Errorf("Data[0].Index = %d, want 0", resp.Data[0].Index)
	}

	// Verify embedding values.
	var values []float64
	if err := json.Unmarshal(resp.Data[0].Embedding, &values); err != nil {
		t.Fatalf("Unmarshal embedding: %v", err)
	}
	if len(values) != 3 {
		t.Errorf("embedding length = %d, want 3", len(values))
	}

	if resp.Usage == nil {
		t.Fatal("Usage should not be nil")
	}
	if resp.Usage.PromptTokens != 0 { // Gemini doesn't provide token counts
		t.Errorf("PromptTokens = %d, want 0", resp.Usage.PromptTokens)
	}
}

func TestTranslateSingleEmbedResponse_NilEmbedding(t *testing.T) {
	gemResp := &geminiEmbedResponse{
		Embedding: nil,
	}

	resp := translateSingleEmbedResponse(gemResp, "model", "test")

	if len(resp.Data) != 0 {
		t.Errorf("Data length = %d, want 0 for nil embedding", len(resp.Data))
	}
}

// ---------------------------------------------------------------------------
// translateBatchEmbedResponse tests
// ---------------------------------------------------------------------------

func TestTranslateBatchEmbedResponse(t *testing.T) {
	gemResp := &geminiBatchEmbedResponse{
		Embeddings: []geminiEmbeddingValues{
			{Values: []float64{0.1, 0.2}},
			{Values: []float64{0.3, 0.4}},
		},
	}

	resp := translateBatchEmbedResponse(gemResp, "text-embedding-004", []string{"hello", "world"})

	if resp.Object != "list" {
		t.Errorf("Object = %q, want %q", resp.Object, "list")
	}
	if len(resp.Data) != 2 {
		t.Fatalf("Data length = %d, want 2", len(resp.Data))
	}
	if resp.Data[0].Index != 0 {
		t.Errorf("Data[0].Index = %d, want 0", resp.Data[0].Index)
	}
	if resp.Data[1].Index != 1 {
		t.Errorf("Data[1].Index = %d, want 1", resp.Data[1].Index)
	}

	if resp.Usage == nil {
		t.Fatal("Usage should not be nil")
	}
	// Gemini doesn't provide token counts — expect 0.
	if resp.Usage.TotalTokens != 0 {
		t.Errorf("TotalTokens = %d, want 0", resp.Usage.TotalTokens)
	}
}

// ---------------------------------------------------------------------------
// Integration tests — single embed
// ---------------------------------------------------------------------------

func TestIntegration_CreateEmbedding_SingleText(t *testing.T) {
	gemResp := geminiEmbedResponse{
		Embedding: &geminiEmbeddingValues{
			Values: []float64{0.01, 0.02, 0.03, 0.04},
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("Method = %q, want POST", r.Method)
		}
		if !strings.Contains(r.URL.Path, "embedContent") {
			t.Errorf("URL path = %q, want to contain embedContent", r.URL.Path)
		}
		if r.URL.Query().Get("key") != "test-api-key" {
			t.Errorf("key = %q, want test-api-key", r.URL.Query().Get("key"))
		}

		// Verify request body.
		body, _ := io.ReadAll(r.Body)
		var req geminiEmbedRequest
		if err := json.Unmarshal(body, &req); err != nil {
			t.Fatalf("unmarshal request: %v", err)
		}
		if req.Content == nil || len(req.Content.Parts) != 1 {
			t.Fatalf("expected 1 content part, got %v", req.Content)
		}
		if req.Content.Parts[0].Text != "hello world" {
			t.Errorf("text = %q, want %q", req.Content.Parts[0].Text, "hello world")
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(gemResp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "gemini/text-embedding-004",
		Input: json.RawMessage(`"hello world"`),
	})
	if err != nil {
		t.Fatalf("CreateEmbedding() error = %v", err)
	}

	if resp.Object != "list" {
		t.Errorf("Object = %q, want %q", resp.Object, "list")
	}
	if len(resp.Data) != 1 {
		t.Fatalf("Data length = %d, want 1", len(resp.Data))
	}

	var values []float64
	if err := json.Unmarshal(resp.Data[0].Embedding, &values); err != nil {
		t.Fatalf("Unmarshal embedding: %v", err)
	}
	if len(values) != 4 {
		t.Errorf("embedding length = %d, want 4", len(values))
	}
}

// ---------------------------------------------------------------------------
// Integration tests — batch embed
// ---------------------------------------------------------------------------

func TestIntegration_CreateEmbedding_BatchTexts(t *testing.T) {
	gemResp := geminiBatchEmbedResponse{
		Embeddings: []geminiEmbeddingValues{
			{Values: []float64{0.1, 0.2}},
			{Values: []float64{0.3, 0.4}},
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, "batchEmbedContents") {
			t.Errorf("URL path = %q, want to contain batchEmbedContents", r.URL.Path)
		}

		body, _ := io.ReadAll(r.Body)
		var req geminiBatchEmbedRequest
		if err := json.Unmarshal(body, &req); err != nil {
			t.Fatalf("unmarshal request: %v", err)
		}
		if len(req.Requests) != 2 {
			t.Fatalf("expected 2 batch items, got %d", len(req.Requests))
		}
		// Verify model references include the models/ prefix.
		for i, item := range req.Requests {
			if !strings.HasPrefix(item.Model, "models/") {
				t.Errorf("item[%d].Model = %q, want models/ prefix", i, item.Model)
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(gemResp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "text-embedding-004",
		Input: json.RawMessage(`["hello","world"]`),
	})
	if err != nil {
		t.Fatalf("CreateEmbedding() error = %v", err)
	}

	if len(resp.Data) != 2 {
		t.Fatalf("Data length = %d, want 2", len(resp.Data))
	}
	if resp.Data[0].Index != 0 || resp.Data[1].Index != 1 {
		t.Error("indexes not correct")
	}
}

// ---------------------------------------------------------------------------
// Integration tests — dimensions parameter
// ---------------------------------------------------------------------------

func TestIntegration_CreateEmbedding_WithDimensions(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req geminiEmbedRequest
		if err := json.Unmarshal(body, &req); err != nil {
			t.Fatalf("unmarshal request: %v", err)
		}
		if req.OutputDimensionality == nil {
			t.Fatal("expected outputDimensionality to be set")
		}
		if *req.OutputDimensionality != 256 {
			t.Errorf("outputDimensionality = %d, want 256", *req.OutputDimensionality)
		}

		gemResp := geminiEmbedResponse{
			Embedding: &geminiEmbeddingValues{
				Values: make([]float64, 256),
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(gemResp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	dims := 256

	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model:      "text-embedding-004",
		Input:      json.RawMessage(`"test"`),
		Dimensions: &dims,
	})
	if err != nil {
		t.Fatalf("CreateEmbedding() error = %v", err)
	}
	if len(resp.Data) != 1 {
		t.Fatalf("Data length = %d, want 1", len(resp.Data))
	}
}

// ---------------------------------------------------------------------------
// Error handling tests
// ---------------------------------------------------------------------------

func TestIntegration_CreateEmbedding_EmptyArray(t *testing.T) {
	// Empty arrays should be rejected before making any HTTP call.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("should not reach the server for empty input")
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "text-embedding-004",
		Input: json.RawMessage(`[]`),
	})
	if err == nil {
		t.Fatal("expected error for empty array input")
	}
}

func TestIntegration_CreateEmbedding_NilInput(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("should not reach the server for nil input")
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "text-embedding-004",
		Input: nil,
	})
	if err == nil {
		t.Fatal("expected error for nil input")
	}
}

func TestIntegration_CreateEmbedding_ProviderError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(geminiErrorResponse{
			Error: geminiErrorDetail{
				Code:    400,
				Message: "Invalid model",
				Status:  "INVALID_ARGUMENT",
			},
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "invalid-model",
		Input: json.RawMessage(`"hello"`),
	})
	if err == nil {
		t.Fatal("expected error from provider")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadRequest)
	}
}
