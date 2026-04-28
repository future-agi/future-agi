package routes

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/handlers"
)

// RegisterAPICallRoutes registers API call routes
func RegisterAPICallRoutes(mux *http.ServeMux, apiCallHandler *handlers.APICallHandler) {
	mux.HandleFunc("POST /api/v1/api-call", apiCallHandler.HandleAPICall)
}
