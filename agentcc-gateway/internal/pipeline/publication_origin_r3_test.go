package pipeline_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

func TestOptBR3PublishedErrorOrigin(t *testing.T) {
	provider := models.ErrUpstreamProvider(401, "provider-private-diagnostic")
	auth := models.ErrUnauthorized("gateway credential is missing")
	rate := models.ErrTooManyRequests("gateway request budget exceeded")
	// Public status/type/code/message alone must not manufacture gateway origin.
	lookalike := &models.APIError{Status: auth.Status, Type: auth.Type, Code: auth.Code, Message: auth.Message}
	mutated := models.ErrUnauthorized("gateway credential is missing")
	mutated.Message = provider.Error()
	for _, tt := range []struct {
		name string
		err  error
		keep bool
	}{
		{"gateway_auth", auth, true},
		{"gateway_rate_limit", rate, true},
		{"provider_auth", provider, false},
		{"gateway_wraps_provider", fmt.Errorf("gateway dispatch: %w", provider), false},
		{"gateway_embeds_provider_text", models.ErrInternal("gateway dispatch: " + provider.Error()), false},
		{"untyped_plain", errors.New("gateway credential is missing"), false},
		{"untyped_api_lookalike", lookalike, false},
		{"untyped_wraps_gateway", fmt.Errorf("provider-private-diagnostic: %w", auth), false},
		{"joined_gateway_provider", errors.Join(auth, provider), false},
		{"mutated_gateway_message", mutated, false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "origin-correlation"
			before := tt.err.Error()
			got := pipeline.PublishedErrorText(rc, tt.err)
			if tt.keep {
				if got != before {
					t.Fatalf("gateway diagnostic lost: got %q, want %q", got, before)
				}
			} else if !strings.Contains(got, "correlation id: origin-correlation") || strings.Contains(got, "provider-private-diagnostic") || got == before {
				t.Fatalf("non-gateway origin was not projected: %q", got)
			}
			if tt.err.Error() != before {
				t.Fatal("publication mutated internal error")
			}
		})
	}
}

func TestOptBR3GatewayOriginStillAppliesTextPolicy(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.ErrorTextRedactor = func(text string) string { return strings.ReplaceAll(text, "policy-token", "[policy]") }
	err := models.ErrTooManyRequests("policy-token exhausted")
	if got := pipeline.PublishedErrorText(rc, err); got != "rate_limit_exceeded: [policy] exhausted" {
		t.Fatalf("gateway text bypassed effective text policy: %q", got)
	}
}

func TestOptBR3EngineWarningUsesTypedOrigin(t *testing.T) {
	for _, tt := range []struct {
		name string
		err  error
		keep bool
	}{
		{"auth", models.ErrUnauthorized("gateway credential is missing"), true},
		{"rate", models.ErrTooManyRequests("gateway request budget exceeded"), true},
		{"wrapped", fmt.Errorf("gateway: %w", models.ErrUpstreamProvider(502, "provider-private-diagnostic")), false},
		{"plain", errors.New("provider-private-diagnostic"), false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			var buf bytes.Buffer
			old := slog.Default()
			slog.SetDefault(slog.New(slog.NewJSONHandler(&buf, nil)))
			defer slog.SetDefault(old)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "engine-origin"
			got := pipeline.NewEngine().Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { return tt.err })
			if got != tt.err || len(rc.Errors) != 1 || rc.Errors[0] != tt.err {
				t.Fatal("engine replaced internal error")
			}
			found := false
			for _, line := range strings.Split(buf.String(), "\n") {
				var row map[string]any
				if json.Unmarshal([]byte(line), &row) != nil || row["msg"] != "provider call failed" {
					continue
				}
				found = true
				text, ok := row["error"].(string)
				if !ok {
					t.Fatal("warning has no error text")
				}
				if tt.keep && text != tt.err.Error() {
					t.Fatalf("gateway warning lost text: %q", text)
				}
				if !tt.keep && (!strings.Contains(text, "correlation id: engine-origin") || strings.Contains(text, "provider-private-diagnostic")) {
					t.Fatalf("warning leaked untrusted text: %q", text)
				}
			}
			if !found {
				t.Fatal("provider warning missing")
			}
		})
	}
}
