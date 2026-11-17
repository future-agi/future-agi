package common

import (
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// NativeHTTPError records an actual HTTP refusal at a native stream handshake.
// Unlike ErrUpstreamProvider, it cannot represent a synthetic gateway failure.
// Keep the upstream's type/code separate from the final writer's status-based
// fallback: invented values must not be presented as provider evidence.
func NativeHTTPError(status int, body []byte) *models.APIError {
	var envelope struct {
		Error struct {
			Message string          `json:"message"`
			Type    string          `json:"type"`
			Status  string          `json:"status"`
			Code    json.RawMessage `json:"code"`
		} `json:"error"`
	}
	e := &models.APIError{Status: status, UpstreamStatus: status, ProviderStatus: status}
	if status >= http.StatusInternalServerError {
		e.Status = http.StatusBadGateway
	}
	if json.Unmarshal(body, &envelope) == nil {
		e.Message, e.Type = envelope.Error.Message, envelope.Error.Type
		if e.Type == "" {
			e.Type = envelope.Error.Status // Google's RPC status is its error type.
		}
		// Google's numeric code is already represented by ProviderStatus.
		_ = json.Unmarshal(envelope.Error.Code, &e.Code)
	}
	if e.Message == "" {
		e.Message = string(body)
		if len(e.Message) > 500 {
			e.Message = e.Message[:500] + "..."
		}
	}
	e.Message = models.RedactCredentials(e.Message)
	return e
}
