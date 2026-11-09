package server

import (
	"encoding/json"
	"errors"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// nativeStreamProviderFailure gives an error-returning native handshake the
// same status-based client code as a native status/body response. The upstream
// code stays in metadata; gateway errors are not reclassified.
func nativeStreamProviderFailure(rc *models.RequestContext, err error) error {
	var original *models.APIError
	out := providerFailure(rc, err)
	if errors.As(err, &original) && original.ProviderStatus > 0 {
		if attributed, ok := out.(*models.APIError); ok && attributed.Code != codeProviderAuthFailed {
			attributed.Code = fmt.Sprintf("provider_%d", original.ProviderStatus)
		}
	}
	return out
}

// nativeProviderFailure normalizes a native HTTP error whose adapter returned
// a status/body and nil error. Call only at the final writer: the pipeline,
// usage accounting, retries and logs retain the original upstream outcome.
// Successful responses remain byte-for-byte passthroughs.
func nativeProviderFailure(rc *models.RequestContext, status int, body []byte) *models.APIError {
	if status < 400 {
		return nil
	}
	e := &models.APIError{Status: status, ProviderStatus: status, Message: nativeErrorMessage(body)}
	if rc != nil {
		e.Provider = rc.Provider
	}
	var envelope struct {
		Error struct {
			Type   string          `json:"type"`
			Status string          `json:"status"`
			Code   json.RawMessage `json:"code"`
		} `json:"error"`
	}
	if json.Unmarshal(body, &envelope) == nil {
		e.Type = envelope.Error.Type
		if e.Type == "" {
			e.Type = envelope.Error.Status // Google's RPC status is its error type.
		}
		// Google uses a numeric code; it is already represented by status.
		_ = json.Unmarshal(envelope.Error.Code, &e.Code)
	}
	out := attributeProviderFailure(e, e.Provider, credentialOwner(rc))
	if status != 401 && status != 403 {
		// Native clients get one stable code even if the upstream uses a
		// vendor-specific string. The original stays in provider_code.
		out.Code = fmt.Sprintf("provider_%d", status)
	}
	return publishClientProviderError(rc, e, out)
}
