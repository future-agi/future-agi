package openai

import (
	"encoding/json"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An SSE error after HTTP 200 is not an empty successful completion. Both
// OpenRouter's error object and event:error providers carry this envelope.
// Retain a real accompanying usage report, but never synthesize one here.
func streamErrorEnvelope(data []byte, credentials ...string) (*models.APIError, models.StreamChunk) {
	var envelope struct {
		Error *struct {
			Message string          `json:"message"`
			Code    json.RawMessage `json:"code"`
		} `json:"error"`
		Usage    *models.Usage `json:"usage"`
		Provider string        `json:"provider"`
		Model    string        `json:"model"`
		ID       string        `json:"id"`
	}
	if json.Unmarshal(data, &envelope) != nil || envelope.Error == nil {
		return nil, models.StreamChunk{}
	}
	status := http.StatusBadGateway
	var code int
	if json.Unmarshal(envelope.Error.Code, &code) == nil && code >= 400 && code <= 599 {
		status = code
	}
	message := envelope.Error.Message
	if message == "" {
		message = "upstream stream failed"
	}
	chunk := models.StreamChunk{ID: envelope.ID, Model: envelope.Model, Usage: envelope.Usage, UpstreamProvider: envelope.Provider}
	err := common.ExactAPIError(models.ErrUpstreamProvider(status, message), credentials...)
	err.ProviderRejected = true
	err.BillingEvidence = &models.ProviderBillingEvidence{Generation: models.GenerationFromResponse(&chunk), Usage: envelope.Usage}
	return err, chunk
}
