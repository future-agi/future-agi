package routing

import "github.com/futureagi/agentcc-gateway/internal/models"

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
		return false, &models.APIError{
			Status: 503, Type: models.ErrTypeServer, Code: "circuit_open",
			Message: "Provider circuit is open or at its probe limit",
		}
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
