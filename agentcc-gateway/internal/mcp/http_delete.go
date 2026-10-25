package mcp

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// HandleDelete handles DELETE on the MCP endpoint. Session termination is not
// supported, so it answers 405 exactly as the router answers a method a path
// does not serve.
func (s *Server) HandleDelete(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.authenticateHTTP(w, r); !ok {
		return
	}
	models.WriteError(w, &models.APIError{
		Status:  http.StatusMethodNotAllowed,
		Type:    models.ErrTypeInvalidRequest,
		Code:    "method_not_allowed",
		Message: "Method not allowed for this endpoint",
	})
}
