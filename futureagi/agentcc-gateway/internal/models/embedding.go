package models

import "encoding/json"

// EmbeddingRequest represents an OpenAI-compatible embedding request.
type EmbeddingRequest struct {
	Model          string          `json:"model"`
	Input          json.RawMessage `json:"input"`                     // string or []string
	EncodingFormat string          `json:"encoding_format,omitempty"` // "float" | "base64"
	Dimensions     *int            `json:"dimensions,omitempty"`
	User           string          `json:"user,omitempty"`
}

// EmbeddingResponse represents an OpenAI-compatible embedding response.
type EmbeddingResponse struct {
	Object string          `json:"object"` // "list"
	Data   []EmbeddingData `json:"data"`
	Model  string          `json:"model"`
	Usage  *EmbeddingUsage `json:"usage"`
}

// EmbeddingData is a single embedding result.
type EmbeddingData struct {
	Object    string          `json:"object"` // "embedding"
	Index     int             `json:"index"`
	Embedding json.RawMessage `json:"embedding"` // []float64 or base64 string
}

// EmbeddingUsage tracks token usage for embedding requests.
type EmbeddingUsage struct {
	PromptTokens int `json:"prompt_tokens"`
	TotalTokens  int `json:"total_tokens"`
}
