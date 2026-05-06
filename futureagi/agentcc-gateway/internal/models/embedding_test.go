package models

import (
	"encoding/json"
	"testing"
)

func TestEmbeddingRequestMarshalRoundTrip(t *testing.T) {
	dims := 1536
	req := EmbeddingRequest{
		Model:          "text-embedding-3-small",
		Input:          json.RawMessage(`["hello world","goodbye"]`),
		EncodingFormat: "float",
		Dimensions:     &dims,
		User:           "user-123",
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"model", "input", "encoding_format", "dimensions", "user"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("expected key %q in marshaled JSON", key)
		}
	}
}

func TestEmbeddingRequestUnmarshal(t *testing.T) {
	input := `{
		"model": "text-embedding-3-small",
		"input": ["hello", "world"],
		"encoding_format": "base64",
		"dimensions": 256,
		"user": "u-1"
	}`

	var req EmbeddingRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if req.Model != "text-embedding-3-small" {
		t.Errorf("Model = %q, want %q", req.Model, "text-embedding-3-small")
	}
	if req.EncodingFormat != "base64" {
		t.Errorf("EncodingFormat = %q, want %q", req.EncodingFormat, "base64")
	}
	if req.Dimensions == nil || *req.Dimensions != 256 {
		t.Errorf("Dimensions = %v, want 256", req.Dimensions)
	}
	if req.User != "u-1" {
		t.Errorf("User = %q, want %q", req.User, "u-1")
	}
	if req.Input == nil {
		t.Error("Input should not be nil")
	}
}

func TestEmbeddingRequestUnmarshalStringInput(t *testing.T) {
	input := `{"model":"text-embedding-ada-002","input":"single string"}`

	var req EmbeddingRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if req.Model != "text-embedding-ada-002" {
		t.Errorf("Model = %q, want %q", req.Model, "text-embedding-ada-002")
	}

	var s string
	if err := json.Unmarshal(req.Input, &s); err != nil {
		t.Fatalf("Input is not a string: %v", err)
	}
	if s != "single string" {
		t.Errorf("Input = %q, want %q", s, "single string")
	}
}

func TestEmbeddingRequestOmitEmpty(t *testing.T) {
	req := EmbeddingRequest{
		Model: "text-embedding-3-small",
		Input: json.RawMessage(`"hello"`),
	}

	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal raw error: %v", err)
	}

	for _, key := range []string{"encoding_format", "dimensions", "user"} {
		if _, ok := raw[key]; ok {
			t.Errorf("key %q should be omitted when zero/nil", key)
		}
	}

	// Required fields must be present.
	for _, key := range []string{"model", "input"} {
		if _, ok := raw[key]; !ok {
			t.Errorf("required key %q should be present", key)
		}
	}
}

func TestEmbeddingResponseMarshalRoundTrip(t *testing.T) {
	usage := &EmbeddingUsage{PromptTokens: 10, TotalTokens: 10}
	resp := EmbeddingResponse{
		Object: "list",
		Data: []EmbeddingData{
			{
				Object:    "embedding",
				Index:     0,
				Embedding: json.RawMessage(`[0.1,0.2,0.3]`),
			},
		},
		Model: "text-embedding-3-small",
		Usage: usage,
	}

	data, err := json.Marshal(&resp)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var got EmbeddingResponse
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if got.Object != "list" {
		t.Errorf("Object = %q, want %q", got.Object, "list")
	}
	if got.Model != "text-embedding-3-small" {
		t.Errorf("Model = %q, want %q", got.Model, "text-embedding-3-small")
	}
	if len(got.Data) != 1 {
		t.Fatalf("Data length = %d, want 1", len(got.Data))
	}
	if got.Data[0].Object != "embedding" {
		t.Errorf("Data[0].Object = %q, want %q", got.Data[0].Object, "embedding")
	}
	if got.Data[0].Index != 0 {
		t.Errorf("Data[0].Index = %d, want 0", got.Data[0].Index)
	}
	if got.Usage == nil {
		t.Fatal("Usage should not be nil")
	}
	if got.Usage.PromptTokens != 10 {
		t.Errorf("Usage.PromptTokens = %d, want 10", got.Usage.PromptTokens)
	}
	if got.Usage.TotalTokens != 10 {
		t.Errorf("Usage.TotalTokens = %d, want 10", got.Usage.TotalTokens)
	}
}

func TestEmbeddingResponseUnmarshal(t *testing.T) {
	input := `{
		"object": "list",
		"data": [
			{"object": "embedding", "index": 0, "embedding": [0.001, 0.002]},
			{"object": "embedding", "index": 1, "embedding": [0.003, 0.004]}
		],
		"model": "text-embedding-ada-002",
		"usage": {"prompt_tokens": 5, "total_tokens": 5}
	}`

	var resp EmbeddingResponse
	if err := json.Unmarshal([]byte(input), &resp); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if resp.Object != "list" {
		t.Errorf("Object = %q, want %q", resp.Object, "list")
	}
	if len(resp.Data) != 2 {
		t.Fatalf("Data length = %d, want 2", len(resp.Data))
	}
	if resp.Data[1].Index != 1 {
		t.Errorf("Data[1].Index = %d, want 1", resp.Data[1].Index)
	}
	if resp.Usage == nil || resp.Usage.PromptTokens != 5 {
		t.Errorf("Usage.PromptTokens = %v, want 5", resp.Usage)
	}
}
