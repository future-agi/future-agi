package handlers

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/services"
)

// APICallHandler handles API call requests
type APICallHandler struct {
	httpClient *services.HTTPClient
}

// NewAPICallHandler creates a new API call handler
func NewAPICallHandler() *APICallHandler {
	return &APICallHandler{
		httpClient: services.NewHTTPClient(),
	}
}

// HandleAPICall executes an API call based on the provided configuration
func (h *APICallHandler) HandleAPICall(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	// Parse request body
	var apiCallReq models.APICallRequest
	if err := json.NewDecoder(r.Body).Decode(&apiCallReq); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	// Create request configuration
	config := services.RequestConfig{
		Method:      apiCallReq.Method,
		URL:         apiCallReq.URL,
		Headers:     apiCallReq.Headers,
		Body:        apiCallReq.Body,
		ContentType: apiCallReq.ContentType,
	}

	// Set timeout
	if apiCallReq.Timeout > 0 {
		config.Timeout = time.Duration(apiCallReq.Timeout) * time.Second
	} else {
		config.Timeout = 30 * time.Second
	}

	// Execute request
	response, err := h.httpClient.MakeRequest(ctx, config)
	if err != nil {
		http.Error(w, "API call failed: "+err.Error(), http.StatusInternalServerError)
		return
	}

	// Create response
	apiCallResp := models.APICallResponse{
		StatusCode: response.StatusCode,
		Headers:    response.Headers,
		Body:       response.Body,
	}

	// Send response
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(apiCallResp); err != nil {
		http.Error(w, "Failed to encode response", http.StatusInternalServerError)
		return
	}
}
