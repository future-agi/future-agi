package server

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
	anthropictrans "github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
)

// managedMessagesRouting enforces the credit-funded routing contract before
// ANY canonical /v1/messages execution: even a BYOK primary can fall back to
// credits. Read controls the translator would otherwise drop, and refuse
// unsupported ones before any credit is held or upstream called:
//   - a field the gateway does not implement: 400
//     routing_field_not_yet_supported, as on chat (applyRoutingOptions);
//   - plugins, route or transforms: 400 unsupported_routing_field, as on
//     chat (reqopts.CheckManaged);
//   - the provider object, providerOptions, models or fallbacks, preset and
//     caching: 400 unsupported_routing_field, because /v1/messages does not
//     run the route selection that honours them (chat does), and nothing
//     the gateway interprets is accepted and then ignored (decision 11).
//
// The body's session_id is kept on rc.Routing, as chat keeps it. A body
// that is not a JSON object is left to the translator to refuse.
func (h *Handlers) managedMessagesRouting(rc *models.RequestContext, body []byte) *models.APIError {
	var top map[string]json.RawMessage
	if json.Unmarshal(body, &top) != nil {
		return nil
	}
	known := anthropictrans.KnownRequestFields()
	extra := make(map[string]json.RawMessage, len(top))
	for k, v := range top {
		if _, ok := known[k]; !ok {
			extra[k] = v
		}
	}
	if !reqopts.HasRoutingFields(extra) {
		return nil
	}
	_, fromFallbacks := extra["fallbacks"]
	carrier := &models.ChatCompletionRequest{Extra: extra}
	if apiErr := h.applyRoutingOptions(rc, carrier); apiErr != nil {
		return apiErr
	}
	if apiErr := reqopts.CheckManaged(nil, &rc.Routing, nil); apiErr != nil {
		return apiErr
	}
	return messagesRoutingNotHonoured(&rc.Routing, fromFallbacks)
}

// messagesRoutingNotHonoured refuses the first routing field o carries that
// /v1/messages cannot honour for a managed request, nil when there is none.
func messagesRoutingNotHonoured(o *models.RoutingOptions, fromFallbacks bool) *models.APIError {
	var param string
	switch {
	case o.Provider != nil:
		param = "provider"
	case o.Gateway != nil:
		param = "providerOptions"
	case o.Models != nil && fromFallbacks:
		param = "fallbacks"
	case o.Models != nil:
		param = "models"
	case o.Preset != "":
		param = "preset"
	case o.Caching != nil:
		param = "caching"
	default:
		return nil
	}
	e := models.ErrRoutingField(reqopts.CodeUnsupportedRoutingField, param)
	e.Message = param + " is not available on /v1/messages with canonical routing; send the request to /v1/chat/completions to use it"
	return e
}
