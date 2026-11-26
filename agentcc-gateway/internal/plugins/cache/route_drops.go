package cache

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// carriageField is a cache-only route marker. Keep the historical JSON true
// for Responses entries; Gemini entries use the string "gemini". Only the
// producing route is persisted, not caller-dependent warnings.
const carriageField = "x_agentcc_responses_carriage"

// withCarriage returns the copy of resp to store, marked when the attempt
// used a translation route. The field is always
// rewritten here, so an upstream body that happens to carry the same key is
// never taken for the gateway's mark. resp itself is not modified.
func withCarriage(resp *models.ChatCompletionResponse, route models.ChatRoute) *models.ChatCompletionResponse {
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
	switch route {
	case models.RouteResponses:
		extra[carriageField] = json.RawMessage(`true`)
	case models.RouteGemini:
		extra[carriageField] = json.RawMessage(`"gemini"`)
	}
	if len(extra) == 0 {
		extra = nil
	}
	stored.Extra = extra
	return &stored
}

// restoreCarriage strips carriageField from a cached response. When the entry
// was produced over a marked route, it reports what that route omits
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

	var drops []string
	switch string(raw) {
	case "true":
		rc.ChatRoute = models.RouteResponses
		drops = models.ResponsesOmittedParams(rc.Request)
	case `"gemini"`:
		rc.ChatRoute = models.RouteGemini
		drops = models.GeminiOmittedParams(rc.Request)
	default:
		return &served
	}
	rc.SetRouteDrops(models.RouteNotes(rc.Request, drops))
	return &served
}
