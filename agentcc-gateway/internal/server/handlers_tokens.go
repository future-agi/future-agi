package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

// CountTokens handles POST /v1/count_tokens.
// Counts tokens for a given model + messages without making an LLM call.
func (h *Handlers) CountTokens(w http.ResponseWriter, r *http.Request) {
	// Parse request body.
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
		return
	}
	if int64(len(body)) > h.maxBodySize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "request_too_large",
			Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize),
		})
		return
	}

	var req models.CountTokensRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}

	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if len(req.Messages) == 0 {
		models.WriteError(w, models.ErrBadRequest("missing_messages", "messages is required and must not be empty"))
		return
	}

	// Estimate token count.
	tokenCount := tokenizer.EstimateTokens(req.Messages)

	resp := models.CountTokensResponse{
		Model:      req.Model,
		TokenCount: tokenCount,
	}

	// Look up model context window from modelDB if available.
	if db := h.ModelDB(); db != nil {
		if info, ok := db.Get(req.Model); ok {
			resp.MaxContextTokens = info.MaxInputTokens
			if info.MaxInputTokens > 0 {
				remaining := info.MaxInputTokens - tokenCount
				if remaining < 0 {
					remaining = 0
				}
				resp.RemainingTokens = remaining
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(resp)
}
