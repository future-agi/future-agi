package routing

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An attempt the client cancelled no longer counts toward a provider's shared
// breaker, as neither a failure nor a success. A provider's own failure, with
// the client still connected, still counts and still opens it.

// breakerFixture is one provider behind a failover with an enabled breaker.
func breakerFixture(t *testing.T) (*Failover, *CircuitBreaker) {
	t.Helper()
	router := makeFailoverRouter(t, "gpt-4", "p1")
	cbReg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, Cooldown: time.Hour}, nil)
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, nil, cbReg)
	return f, cbReg.Get(BreakerKey("p1", "gpt-4", ""))
}

// countedFailure is an upstream failure the breaker counts.
func countedFailure() error {
	return &models.APIError{Status: DefaultCBStatusCodes[0], Type: models.ErrTypeUpstream}
}

// failures is the breaker's count of failures toward opening it.
func failures(cb *CircuitBreaker) int {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	return cb.failures
}

// cancelledRun runs one attempt that the client cancels while it is in
// flight, ending with result.
func cancelledRun(f *Failover, result error) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	_, _ = f.Execute(ctx, "gpt-4", func(context.Context, string, string) error {
		cancel()
		return result
	})
}

func TestFailoverClientCancelledAttemptCountsTowardNoBreaker(t *testing.T) {
	t.Run("a failed attempt the client cancelled is not a failure", func(t *testing.T) {
		f, cb := breakerFixture(t)
		for i := 0; i < cb.cfg.FailureThreshold; i++ {
			cancelledRun(f, countedFailure())
		}
		if got := failures(cb); got != 0 || cb.State() != StateClosed {
			t.Fatalf("breaker failures = %d state = %v after attempts the client cancelled, want none counted and closed", got, cb.State())
		}
	})
	t.Run("an attempt the client cancelled is not a success either", func(t *testing.T) {
		f, cb := breakerFixture(t)
		if _, err := f.Execute(context.Background(), "gpt-4", func(context.Context, string, string) error {
			return countedFailure()
		}); err == nil {
			t.Fatal("the provider's failure returned no error")
		}
		before := failures(cb)
		cancelledRun(f, nil)
		if got := failures(cb); got != before {
			t.Fatalf("breaker failures = %d after an attempt the client cancelled, want %d (not reset as a success)", got, before)
		}
	})
}

func TestFailoverProviderFailureWithTheClientConnectedStillOpensTheBreaker(t *testing.T) {
	f, cb := breakerFixture(t)
	for i := 0; i < cb.cfg.FailureThreshold; i++ {
		if _, err := f.Execute(context.Background(), "gpt-4", func(context.Context, string, string) error {
			return countedFailure()
		}); err == nil {
			t.Fatal("the provider's failure returned no error")
		}
	}
	if cb.State() != StateOpen {
		t.Fatalf("breaker state = %v after the provider's own failures, want open", cb.State())
	}
}
