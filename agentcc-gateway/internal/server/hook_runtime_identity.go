package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// runtimeTokenMiddleware is the chain's place for runtime tokens (signed
// short-lived JWTs from the Future AGI managed runtime), before license
// auth. It returns next itself: the chain is unchanged, and a request
// authenticates with a virtual key as it does without it.
func (s *Server) runtimeTokenMiddleware(next http.Handler) http.Handler {
	return next
}

// runtimeIdentity is call-order step 1, the step for a runtime token's
// identity. It is a no-op: rc.Runtime and the key's org stay as they are.
// Keep observeHook as its first call.
func (h *Handlers) runtimeIdentity(rc *models.RequestContext) *models.APIError {
	h.observeHook(hookRuntimeIdentity, rc)
	return nil
}
