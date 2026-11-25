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

// A dispatch rejection is a local policy decision for the selected request,
// not a provider response and not a circuit refusal to try another target.
// Keep its API error for presentation, even if its status is normally retryable.
type dispatchRejection struct{ api *models.APIError }

func (e *dispatchRejection) Error() string { return e.api.Error() }
func (e *dispatchRejection) Unwrap() error { return e.api }

func RejectDispatch(api *models.APIError) error { return &dispatchRejection{api: api} }

func DispatchRejection(err error) *models.APIError {
	if err == nil {
		return nil
	}
	var rejected *dispatchRejection
	if errors.As(err, &rejected) {
		return rejected.api
	}
	return nil
}

func CircuitOpenError() error {
	return &admissionRefusal{api: models.ErrProviderCircuitOpen()}
}

// ProvidersUnavailableError marks selection exhaustion without an upstream call.
func ProvidersUnavailableError(message string) error {
	return &admissionRefusal{api: models.ErrProvidersUnavailable(message)}
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
