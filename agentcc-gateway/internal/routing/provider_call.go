package routing

import (
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// CircuitBreakers returns the persistent registry, including for per-request failover overrides.
func (f *Failover) CircuitBreakers() *CircuitBreakerRegistry {
	if f == nil {
		return nil
	}
	return f.cbReg
}

// CircuitOpenError distinguishes a local admission refusal from an upstream failure.
func CircuitOpenError() error {
	return &models.APIError{
		Status: 503, Type: models.ErrTypeServer, Code: "circuit_open",
		Message: "Provider circuit is open or at its probe limit",
	}
}

func IsCircuitOpen(err error) bool {
	var apiErr *models.APIError
	return errors.As(err, &apiErr) && apiErr.Code == "circuit_open"
}

// CallProvider applies this failover's circuit breaker to an already-resolved provider.
// Model fallbacks use it without adding routing, retries or timeouts of their own.
func (f *Failover) CallProvider(providerID string, call func() error) error {
	_, err := f.callProvider(providerID, call)
	return err
}

// callProvider distinguishes admission rejection from an error returned by the provider.
func (f *Failover) callProvider(providerID string, call func() error) (admitted bool, err error) {
	if !f.cbReg.IsEnabled() {
		return true, call()
	}

	cb := f.cbReg.Get(providerID)
	generation, allowed := cb.Allow()
	if !allowed {
		return false, CircuitOpenError()
	}

	// Keep one admission generation across the entire call, including any retries.
	err = call()
	if err == nil {
		cb.RecordSuccess(generation)
	} else {
		cb.RecordFailure(generation, err)
	}
	return true, err
}
