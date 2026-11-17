package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// idempotencyBegin is call-order step 9: for a request with an
// Idempotency-Key it replays a finished response, or refuses a key in use,
// and reports whether it wrote the response. A request it lets through has
// rc.Billing.IdempotencyKey set. A no-op in this release. Keep observeHook as its
// first call.
func (h *Handlers) idempotencyBegin(w http.ResponseWriter, r *http.Request, rc *models.RequestContext, body []byte) (handled bool) {
	h.observeHook(hookIdempotencyBegin, rc)
	return false
}

// idempotencyFinish is call-order step 18: it records how the request ended
// (status, headers and, when rc.Billing.IdempotencyKey is set, the body) so a
// retry with the same key can be answered. Every response a chat request
// writes after step 9 calls it once (writeChatResponse, failChat, and the
// stream before its first chunk). A no-op in this release. Keep observeHook as its
// first call.
func (h *Handlers) idempotencyFinish(rc *models.RequestContext, status int, hdr http.Header, body []byte) {
	h.observeHook(hookIdempotencyFinish, rc)
}
