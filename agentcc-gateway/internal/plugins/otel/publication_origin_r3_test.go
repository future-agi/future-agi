package otel

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestOptBR3OTelTypedErrorOrigin(t *testing.T) {
	for _, tt := range []struct {
		name string
		err  error
		keep bool
	}{
		{"gateway_auth", models.ErrUnauthorized("gateway credential is missing"), true},
		{"gateway_rate", models.ErrTooManyRequests("gateway request budget exceeded"), true},
		{"gateway_wraps_provider", fmt.Errorf("gateway: %w", models.ErrUpstreamProvider(502, "provider-private-diagnostic")), false},
		{"gateway_embeds_provider", models.ErrInternal("gateway: provider-private-diagnostic"), false},
		{"plain", errors.New("provider-private-diagnostic"), false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			exp := &captureExporter{}
			p := NewWithExporter(exp, 1, true)
			rc := newTestRC()
			rc.Errors = []error{tt.err}
			p.ProcessRequest(context.Background(), rc)
			p.ProcessResponse(context.Background(), rc)
			spans := exp.Spans()
			if len(spans) != 1 || spans[0].Status != "ERROR" {
				t.Fatal("missing error span")
			}
			text, ok := spans[0].Attributes["error.message"].(string)
			if !ok {
				t.Fatal("missing error.message")
			}
			if tt.keep && text != tt.err.Error() {
				t.Fatalf("gateway span lost text: %q", text)
			}
			if !tt.keep && (!strings.Contains(text, "correlation id: "+rc.RequestID) || strings.Contains(text, "provider-private-diagnostic")) {
				t.Fatalf("span published untrusted text: %q", text)
			}
			if rc.Errors[0] != tt.err {
				t.Fatal("export mutated internal error")
			}
		})
	}
}
