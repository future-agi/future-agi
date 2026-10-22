package routing

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestFailoverEveryAttemptCircuitOpenIsAnError: a run whose every attempt
// goes to an open circuit ends with an error, never with no result and no
// error. With per-model circuits nothing marks the router's targets
// unhealthy, so a bare router keeps offering them, and the chat handlers
// read a nil result as a success.
func TestFailoverEveryAttemptCircuitOpenIsAnError(t *testing.T) {
	for _, tc := range []struct {
		name        string
		providers   []string
		maxAttempts int
	}{
		{"attempts exhausted on open circuits", []string{"p1", "p2", "p3"}, 3},
		{"fewer attempts than targets", []string{"p1", "p2", "p3"}, 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			router := makeFailoverRouter(t, "gpt-4", tc.providers...)
			cbReg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
			for _, p := range tc.providers {
				cbReg.Get(BreakerKey(p, "gpt-4", "")).RecordFailure(&models.APIError{Status: 500})
			}
			f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: tc.maxAttempts}, router, nil, cbReg)
			calls := &scriptedCalls{}

			result, err := f.Execute(context.Background(), "gpt-4", calls.call)
			if len(calls.calls) != 0 {
				t.Fatalf("calls = %v, want none: every circuit is open", calls.calls)
			}
			if result != nil || err == nil {
				t.Fatalf("Execute = (%v, %v), want no result and an error", result, err)
			}
			var apiErr *models.APIError
			if !errors.As(err, &apiErr) || apiErr.Status != http.StatusServiceUnavailable || apiErr.Code != "all_providers_unavailable" {
				t.Fatalf("err = %#v, want a 503 all_providers_unavailable APIError", err)
			}
		})
	}
}
