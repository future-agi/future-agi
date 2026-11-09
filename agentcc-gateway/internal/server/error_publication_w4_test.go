package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestDXW4GatewayOriginSurfaces(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Provider, rc.RequestID = "fixture", "w4"
	rc.ErrorTextRedactor = func(s string) string { return strings.ReplaceAll(s, "output", "[policy]") }
	for name, publish := range map[string]func(*models.APIError) *models.APIError{
		"client": func(e *models.APIError) *models.APIError { return providerFailure(rc, e).(*models.APIError) },
		"stream": func(e *models.APIError) *models.APIError { return clientStreamError(rc, e) },
		"native": func(e *models.APIError) *models.APIError {
			return nativeStreamProviderFailure(rc, e).(*models.APIError)
		},
	} {
		t.Run(name, func(t *testing.T) {
			const message = "upstream stream closed before model output"
			gateway := models.ErrGatewayUpstream(message)
			out := publish(gateway)
			if out.Message != "upstream stream closed before model [policy]" || out.Status != 502 || out.Code != "provider_502" || gateway.Message != message {
				t.Fatalf("gateway publication=%+v original=%+v", out, gateway)
			}
			out = publish(models.ErrUpstreamProvider(502, message))
			if strings.Contains(out.Message, "stream closed") || !strings.HasPrefix(out.Message, "upstream_unavailable:") {
				t.Fatalf("provider lookalike=%+v", out)
			}
		})
	}
}
