package models

// CountTokensRequest represents a token counting request.
type CountTokensRequest struct {
	Model    string    `json:"model"`
	Messages []Message `json:"messages"`
}

// CountTokensResponse represents a token counting response.
type CountTokensResponse struct {
	Model            string `json:"model"`
	TokenCount       int    `json:"token_count"`
	MaxContextTokens int    `json:"max_context_tokens,omitempty"`
	RemainingTokens  int    `json:"remaining_tokens,omitempty"`
}
