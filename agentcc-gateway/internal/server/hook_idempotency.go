package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// idempotencyBegin is call-order step 9 and reports whether it wrote the
// response. It writes none, so the request goes on. Keep observeHook as its
// first call.
func (h *Handlers) idempotencyBegin(w http.ResponseWriter, r *http.Request, rc *models.RequestContext, body []byte) (handled bool) {
	h.observeHook(hookIdempotencyBegin, rc)
	return false
}

// idempotencyFinish is call-order step 18 and gets how the request ended:
// its status, its headers and, when rc.Billing.IdempotencyKey is set, its
// body. Every response a chat request writes after step 9 calls it once
// (writeChatResponse, failChat, and the stream before its first chunk). It
// keeps nothing. Keep observeHook as its first call.
func (h *Handlers) idempotencyFinish(rc *models.RequestContext, status int, hdr http.Header, body []byte) {
	h.observeHook(hookIdempotencyFinish, rc)
}
