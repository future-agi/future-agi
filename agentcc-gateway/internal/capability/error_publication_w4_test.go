package capability

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestW4GatewayOriginPublication(t *testing.T) {
	gateway := models.ErrGatewayUpstream("upstream stream closed before model output")
	mutated := *gateway
	mutated.Message = "opaque-provider-diagnostic"
	for _, tc := range []struct {
		name string
		err  *models.APIError
		keep bool
	}{
		{"gateway", gateway, true},
		{"budget", models.ErrGatewayRefusal(429, models.ErrTypeRateLimit, "budget_exceeded", "budget exceeded"), true},
		{"upstream lookalike", models.ErrUpstreamProvider(502, gateway.Message), false},
		{"untyped lookalike", &models.APIError{Status: 502, Message: gateway.Message}, false},
		{"mutated gateway", &mutated, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			before := tc.err.Message
			called := false
			out := PublishProviderError(tc.err, models.CredentialManaged, tc.err.Status, "w4", func(s string) string { called = true; return s })
			if !called || (out.Message == before) != tc.keep || out == tc.err || tc.err.Message != before {
				t.Fatalf("publication=%+v keep=%t redacted=%t", out, tc.keep, called)
			}
			if !tc.keep && (!strings.Contains(out.Message, "correlation id: w4") || out.Metadata != nil) {
				t.Fatalf("unproven text escaped: %+v", out)
			}
		})
	}
	out := PublishProviderError(gateway, models.CredentialManaged, 502, "w4", func(s string) string { return strings.ReplaceAll(s, "model output", "[policy]") })
	if !strings.Contains(out.Message, "[policy]") {
		t.Fatal("gateway exception skipped policy redaction")
	}
}
