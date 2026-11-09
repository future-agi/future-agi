package models_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The interface keeps this RED compilable on the parent, which has no origin
// method yet. Reuse the existing typed ErrorOrigin enum; never classify text.
func TestOptBR3ErrorOriginSetByConstructor(t *testing.T) {
	for _, tt := range []struct {
		name string
		err  *models.APIError
		want models.ErrorOrigin
	}{
		{"unknown_literal", &models.APIError{Status: 401, Code: "invalid_api_key", Message: "same diagnostic"}, ""},
		{"gateway_auth", models.ErrUnauthorized("same diagnostic"), models.OriginGateway},
		{"gateway_rate", models.ErrTooManyRequests("same diagnostic"), models.OriginGateway},
		{"provider", models.ErrUpstreamProvider(401, "same diagnostic"), models.OriginUpstream},
		{"gateway_embeds_provider", models.ErrInternal("gateway: " + models.ErrUpstreamProvider(502, "same diagnostic").Error()), models.OriginUpstream},
		{"gateway_timeout_with_provider_text", models.ErrGatewayTimeout("same diagnostic"), models.OriginUpstream},
	} {
		t.Run(tt.name, func(t *testing.T) {
			origin, ok := any(tt.err).(interface{ Origin() models.ErrorOrigin })
			if !ok {
				t.Fatal("APIError lacks constructor-set typed Origin")
			}
			if got := origin.Origin(); got != tt.want {
				t.Fatalf("origin = %q, want %q", got, tt.want)
			}
			data, err := json.Marshal(tt.err)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(strings.ToLower(string(data)), "origin") {
				t.Fatal("internal origin was serialized")
			}
		})
	}
}
