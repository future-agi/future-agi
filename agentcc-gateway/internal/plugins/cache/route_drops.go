package cache

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// carriageField marks a stored response that was produced over the Responses
// route. It is the only thing stored: the settings that route omits (stop, n,
// logprobs, the penalties, logit_bias) are not part of the cache key, so a hit
// names them from the request it answers, not from the one that produced the
// entry. Every backend keeps the mark without an interface change: each one
// round-trips the response through JSON, and Extra is written beside the named
// fields. It exists only in stored copies and is removed from every response
// read back before anything is sent.
const carriageField = "x_agentcc_responses_carriage"

// withCarriage returns the copy of resp to store, marked when the attempt
// that produced it went over the Responses route. The field is always
// rewritten here, so an upstream body that happens to carry the same key is
// never taken for the gateway's mark. resp itself is not modified.
func withCarriage(resp *models.ChatCompletionResponse, carriage bool) *models.ChatCompletionResponse {
	if resp == nil {
		return nil
	}
	stored := *resp
	extra := make(map[string]json.RawMessage, len(resp.Extra)+1)
	for k, v := range resp.Extra {
		if k != carriageField {
			extra[k] = v
		}
	}
	if carriage {
		extra[carriageField] = json.RawMessage(`true`)
	}
	if len(extra) == 0 {
		extra = nil
	}
	stored.Extra = extra
	return &stored
}

// restoreCarriage strips carriageField from a cached response. When the entry
// was produced over the Responses route, it reports what that route omits
// from the current request, combined with this request's translator notes,
// for the request log and the x-agentcc-translation-drops header. It returns
// a copy rather than editing resp: an in-memory backend may share resp's
// Extra map with the stored entry, which later hits still need.
func restoreCarriage(rc *models.RequestContext, resp *models.ChatCompletionResponse) *models.ChatCompletionResponse {
	if resp == nil {
		return nil
	}
	raw, ok := resp.Extra[carriageField]
	if !ok {
		return resp
	}
	served := *resp
	extra := make(map[string]json.RawMessage, len(resp.Extra))
	for k, v := range resp.Extra {
		if k != carriageField {
			extra[k] = v
		}
	}
	if len(extra) == 0 {
		extra = nil
	}
	served.Extra = extra

	var carriage bool
	if json.Unmarshal(raw, &carriage) == nil && carriage {
		rc.ResponsesCarriage = true
		rc.SetResponsesRouteDrops(models.ResponsesRouteNotes(rc.Request))
	}
	return &served
}
