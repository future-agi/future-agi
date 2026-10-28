package routing

import (
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCircuitBreakerIgnoresLocalAdmissionFailures(t *testing.T) {
	cb := NewCircuitBreaker("provider", config.CircuitBreakerConfig{FailureThreshold: 1}, nil)
	cb.RecordFailure(fmt.Errorf("wrapped: %w", &models.AdmissionError{Err: models.ErrTooManyRequests("local quota")}))
	if cb.State() != StateClosed {
		t.Fatalf("local admission opened circuit: %v", cb.State())
	}
	cb.RecordFailure(&models.APIError{Status: 429, Type: models.ErrTypeUpstream, Message: "provider overloaded"})
	if cb.State() != StateOpen {
		t.Fatal("genuine provider overload no longer counts")
	}
}
