package capabilities

import (
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// CodeEndpointNotSupported is the 501 a launch endpoint returns when the
// routed provider does not serve it.
const CodeEndpointNotSupported = "endpoint_not_supported_by_provider"

// UnsupportedError is the 501 for provider on endpoint, naming the providers
// in m that do serve it.
func (m *Matrix) UnsupportedError(endpoint, provider string) *models.APIError {
	return &models.APIError{
		Status:   http.StatusNotImplemented,
		Type:     models.ErrTypeServer,
		Code:     CodeEndpointNotSupported,
		Message:  fmt.Sprintf("Provider %q does not support the %s endpoint; GET /v1/capabilities lists what each provider supports", provider, endpoint),
		Metadata: UnsupportedMeta{Endpoint: endpoint, Provider: provider, SupportedProviders: m.SupportedProviders(endpoint)},
	}
}

// UnsupportedMeta is the 501's error.metadata.
type UnsupportedMeta struct {
	Endpoint           string   `json:"endpoint"`
	Provider           string   `json:"provider"`
	SupportedProviders []string `json:"supported_providers"`
}
