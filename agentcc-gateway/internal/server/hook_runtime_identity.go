package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// runtimeTokenMiddleware verifies a runtime token (a signed short-lived JWT
// from the Future AGI managed runtime) presented in place of a virtual key.
// It runs before license auth. No-op until G33: it returns next itself, so
// the chain is unchanged.
func (s *Server) runtimeTokenMiddleware(next http.Handler) http.Handler {
	return next
}

// runtimeIdentity is call-order step 1: when a runtime token authenticated
// the call, it fills rc.Runtime and the key's org from the token's claims.
// No-op until G33. Keep observeHook as its first call.
func (h *Handlers) runtimeIdentity(rc *models.RequestContext) *models.APIError {
	h.observeHook(hookRuntimeIdentity, rc)
	return nil
}
