package server

import (
	"context"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

type responseContinuation struct {
	binding   string
	store     bool
	retriever providers.ResponsesRetriever
}

// prepareResponseContinuation runs after pre-plugins and native admission, on
// the exact body and credential context about to be dispatched. An upstream ID
// is not authority: it must have been created here by the authenticated org on
// the same upstream binding. Unknown, expired, deleted and foreign IDs all give
// the same refusal, including when storage/binding support is unavailable.
func (h *Handlers) prepareResponseContinuation(ctx context.Context, rp providers.ResponsesProvider, body []byte, ownerOrgID string) (responseContinuation, *models.APIError) {
	c := responseContinuation{store: true}
	if bound, ok := rp.(providers.ResponsesBindingProvider); ok {
		c.binding = bound.ResponsesBinding(ctx)
	}
	// Use exact JSON field names, not encoding/json's case-insensitive struct
	// matching: an uppercase lookalike must not hide the reference on the wire.
	var fields map[string]json.RawMessage
	if json.Unmarshal(body, &fields) != nil {
		return c, models.ErrBadRequest("invalid_json", "Invalid Responses request")
	}
	if raw, ok := fields["store"]; ok {
		var store *bool
		if json.Unmarshal(raw, &store) != nil {
			return c, models.ErrBadRequest("invalid_store", "store must be a boolean")
		}
		if store != nil {
			c.store = *store
		}
	}
	var previous string
	if raw, ok := fields["previous_response_id"]; ok {
		if json.Unmarshal(raw, &previous) != nil {
			return c, models.ErrBadRequest("invalid_previous_response_id", "previous_response_id must be a string")
		}
	}
	if previous != "" && (h.responsesStore == nil || !h.responsesStore.CanContinue(previous, ownerOrgID, c.binding)) {
		return c, models.ErrNotFound("response_not_found", "Response not found")
	}
	return c, nil
}

// rememberResponse records only IDs received from a successful upstream call.
// A streaming created/in-progress event records ownership before exposing its
// ID, but not a retrievable body: GET must not bypass output checks. The final
// body is published only after those checks pass, as for non-streamed answers.
func (h *Handlers) rememberResponse(body []byte, ownerOrgID string, c responseContinuation, readable bool) {
	if h.responsesStore == nil || !c.store {
		return
	}
	var parsed struct {
		ID     string `json:"id"`
		Store  *bool  `json:"store"`
		Status string `json:"status"`
	}
	if json.Unmarshal(body, &parsed) != nil || parsed.ID == "" || (parsed.Store != nil && !*parsed.Store) {
		return
	}
	var refresh func(context.Context) ([]byte, error)
	if !readable {
		body = nil
	} else if c.retriever != nil && (parsed.Status == "queued" || parsed.Status == "in_progress") {
		// Retain only the originating provider and ID, never a request context
		// or routing state that may change before a later GET.
		retriever, id := c.retriever, parsed.ID
		refresh = func(ctx context.Context) ([]byte, error) {
			body, _, err := retriever.GetResponse(ctx, id)
			return body, err
		}
	}
	h.responsesStore.PutWithRefresh(parsed.ID, ownerOrgID, c.binding, body, refresh)
}
