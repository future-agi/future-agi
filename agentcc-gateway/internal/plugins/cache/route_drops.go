package cache

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// routeDropsField carries a cached response's Responses-route notes inside
// the stored response, so every backend keeps them without an interface
// change: each one round-trips the response through JSON, and Extra is
// written beside the named fields. The field exists only in stored copies.
// It is removed from every response read back before anything is sent.
const routeDropsField = "x_agentcc_responses_route_drops"

// restorableRouteDrops are the only notes a cache hit may report: the names
// the Responses route omits, as the chat route (<name>_unsupported) and the
// translated routes (<name>) report them. Anything else in a stored entry is
// ignored, so the notes stay a fixed vocabulary even if an entry is altered.
var restorableRouteDrops = func() map[string]bool {
	names := map[string]bool{}
	for _, name := range []string{"stop", "seed", "logprobs", "top_logprobs", "frequency_penalty", "presence_penalty", "logit_bias", "n"} {
		names[name] = true
		names[name+"_unsupported"] = true
	}
	return names
}()

// withRouteDrops returns the copy of resp to store: the attempt's route notes
// under routeDropsField, or no such field when there are none. The field is
// always rewritten here, so an upstream body that happens to carry the same
// key is never replayed as gateway notes. resp itself is not modified.
func withRouteDrops(resp *models.ChatCompletionResponse, drops []string) *models.ChatCompletionResponse {
	if resp == nil {
		return nil
	}
	stored := *resp
	extra := make(map[string]json.RawMessage, len(resp.Extra)+1)
	for k, v := range resp.Extra {
		if k != routeDropsField {
			extra[k] = v
		}
	}
	if len(drops) > 0 {
		if b, err := json.Marshal(drops); err == nil {
			extra[routeDropsField] = b
		}
	}
	if len(extra) == 0 {
		extra = nil
	}
	stored.Extra = extra
	return &stored
}

// restoreRouteDrops strips routeDropsField from a cached response and
// restores its notes on rc, combined with this request's translator notes,
// for the request log and the x-agentcc-translation-drops header. It returns
// a copy rather than editing resp: an in-memory backend may share resp's
// Extra map with the stored entry, which later hits still need.
func restoreRouteDrops(rc *models.RequestContext, resp *models.ChatCompletionResponse) *models.ChatCompletionResponse {
	if resp == nil {
		return nil
	}
	raw, ok := resp.Extra[routeDropsField]
	if !ok {
		return resp
	}
	served := *resp
	extra := make(map[string]json.RawMessage, len(resp.Extra))
	for k, v := range resp.Extra {
		if k != routeDropsField {
			extra[k] = v
		}
	}
	if len(extra) == 0 {
		extra = nil
	}
	served.Extra = extra

	var names []string
	if json.Unmarshal(raw, &names) == nil {
		var trusted []string
		for _, name := range names {
			if restorableRouteDrops[name] {
				trusted = append(trusted, name)
			}
		}
		if len(trusted) > 0 {
			rc.SetResponsesRouteDrops(trusted)
		}
	}
	return &served
}
