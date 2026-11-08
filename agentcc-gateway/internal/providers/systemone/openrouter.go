package systemone

import (
	"bytes"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// openRouterDecisionBody keeps the selected model and provider restrictions,
// but disables upstream failover and requires supported parameters. Decision
// calls must not silently take a different route or drop evaluation criteria.
func openRouterDecisionBody(body []byte) ([]byte, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil || fields == nil {
		return nil, models.ErrBadRequest("invalid_json", "request body must be a single JSON object")
	}
	if _, present := fields["models"]; present {
		return nil, models.ErrBadRequest("decision_fallback_not_allowed", "Decision requests must select one model; model fallbacks are not supported.")
	}
	if _, present := fields["route"]; present {
		return nil, models.ErrBadRequest("decision_fallback_not_allowed", "Decision requests cannot override routing.")
	}

	preferences := make(map[string]json.RawMessage)
	if raw, present := fields["provider"]; present && !bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		if err := json.Unmarshal(raw, &preferences); err != nil || preferences == nil {
			return nil, models.ErrBadRequest("invalid_provider_preferences", "provider must be an object or null")
		}
	}
	preferences["allow_fallbacks"] = json.RawMessage("false")
	preferences["require_parameters"] = json.RawMessage("true")
	encodedPreferences, err := json.Marshal(preferences)
	if err != nil {
		return nil, models.ErrInternal("could not configure decision routing")
	}
	fields["provider"] = encodedPreferences
	encoded, err := json.Marshal(fields)
	if err != nil {
		return nil, models.ErrInternal("could not encode decision request")
	}
	return encoded, nil
}
