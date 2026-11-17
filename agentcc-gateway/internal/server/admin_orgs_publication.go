package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Never expose the underlying error, configuration, identifiers or panic value.
func writeOrgPublicationError(w http.ResponseWriter, err error) {
	status, code := http.StatusServiceUnavailable, "tenant_config_publication_refused"
	errorType := "server_error"
	message := "The gateway couldn't complete the configuration request."
	switch err {
	case tenant.ErrConfigGraph:
		status, code = http.StatusBadRequest, "tenant_config_unsupported"
		errorType = "invalid_request_error"
		message = "This configuration isn't supported by the gateway."
	case tenant.ErrConfigPreparation:
		status, code = http.StatusInternalServerError, "tenant_config_preparation_failed"
		message = "The gateway couldn't prepare the configuration."
	}
	models.WriteError(w, &models.APIError{Status: status, Type: errorType, Code: code, Message: message})
}
