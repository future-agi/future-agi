package routing

import (
	"context"
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func (f *Failover) CircuitBreakers() *CircuitBreakerRegistry {
	if f == nil {
		return nil
	}
	return f.cbReg
}

// admissionRefusal is local provenance, not an upstream API code string.
type admissionRefusal struct{ api *models.APIError }

func (e *admissionRefusal) Error() string { return e.api.Error() }
func (e *admissionRefusal) Unwrap() error { return e.api }

func CircuitOpenError() error {
	return &admissionRefusal{api: &models.APIError{
		Status: 503, Type: models.ErrTypeServer, Code: "circuit_open",
		Message: "Provider circuit is open or at its probe limit",
	}}
}

// ProvidersUnavailableError marks selection exhaustion without an upstream call.
func ProvidersUnavailableError(message string) error {
	return &admissionRefusal{api: models.ErrServiceUnavailable(message)}
}

func IsAdmissionRefusal(err error) bool {
	var refusal *admissionRefusal
	return errors.As(err, &refusal)
}

func IsCircuitOpen(err error) bool {
	var refusal *admissionRefusal
	return errors.As(err, &refusal) && refusal.api.Code == "circuit_open"
}

// CallProvider admits an already resolved provider using the same scope as Execute.
func (f *Failover) CallProvider(providerID string, call func() error) error {
	return f.CallProviderContext(context.Background(), providerID, call)
}

func (f *Failover) CallProviderContext(ctx context.Context, providerID string, call func() error) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	lease, allowed := f.circuitBreakerFor(providerID).Acquire()
	if !allowed {
		return CircuitOpenError()
	}
	defer lease.Abandon()
	err := call()
	lease.Complete(ctx, err)
	return err
}
