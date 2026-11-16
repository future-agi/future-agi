package routing

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// DefaultFailoverStatusCodes are HTTP status codes that trigger failover.
var DefaultFailoverStatusCodes = []int{429, 500, 502, 503, 504}

// Failover handles automatic provider failover on errors.
type Failover struct {
	cfg               config.FailoverConfig
	router            *Router
	retryer           *Retryer
	cbReg             *CircuitBreakerRegistry
	codes             map[int]bool
	useCircuitBreaker func(string) bool
	admit             func(context.Context, string) (*ProbeLease, error)
	// orgBreakers holds BYOK breakers keyed by (org, provider). breakerScope is
	// the org a request-scoped copy records into; empty means no org breaker.
	orgBreakers  *ScopedCircuitBreakerRegistry
	breakerScope string
}

// FailoverCallFunc is called for each failover attempt.
// providerID is the provider to use, modelOverride overrides the model name.
type FailoverCallFunc func(ctx context.Context, providerID string, modelOverride string) error

// FailoverResult contains the outcome of a failover execution.
type FailoverResult struct {
	ProviderID       string
	ModelOverride    string
	Attempts         int
	OriginalProvider string
	FallbackUsed     bool
	StrategyName     string
}

// NewFailover creates a failover handler with optional retry and circuit breaker.
func NewFailover(cfg config.FailoverConfig, router *Router, retryer *Retryer, cbReg *CircuitBreakerRegistry) *Failover {
	if len(cfg.OnStatusCodes) == 0 {
		cfg.OnStatusCodes = DefaultFailoverStatusCodes
	}
	if cfg.MaxAttempts <= 0 {
		cfg.MaxAttempts = 3
	}

	codes := make(map[int]bool, len(cfg.OnStatusCodes))
	for _, c := range cfg.OnStatusCodes {
		codes[c] = true
	}

	f := &Failover{
		cfg:     cfg,
		router:  router,
		retryer: retryer,
		cbReg:   cbReg,
		codes:   codes,
	}
	if cbReg.IsEnabled() {
		f.orgBreakers = cbReg.orgBreakers
	}
	return f
}

// WithCircuitBreakerFilter scopes admission and outcome recording per attempt.
// The request-local filter must identify the actual instance used by the call.
func (f *Failover) WithCircuitBreakerFilter(filter func(string) bool) *Failover {
	scoped := *f
	scoped.useCircuitBreaker = filter
	return &scoped
}

// WithOrgCircuitBreakers is WithCircuitBreakerFilter for an org's request:
// attempts the filter rejects (the org's own key) use a breaker for
// (orgID, provider), with the shared thresholds, so one org's failures never
// open the operator breaker or another org's. An empty orgID means none.
func (f *Failover) WithOrgCircuitBreakers(orgID string, filter func(string) bool) *Failover {
	scoped := f.WithCircuitBreakerFilter(filter)
	scoped.breakerScope = orgID
	return scoped
}

// circuitBreakerFor returns the breaker that admits and records an attempt
// on providerID, or nil when the attempt has none.
func (f *Failover) circuitBreakerFor(providerID string) *CircuitBreaker {
	if !f.cbReg.IsEnabled() {
		return nil
	}
	if f.useCircuitBreaker == nil || f.useCircuitBreaker(providerID) {
		return f.cbReg.Get(providerID)
	}
	if f.breakerScope == "" || f.orgBreakers == nil {
		return nil
	}
	return f.orgBreakers.Get(f.breakerScope, providerID)
}

// WithAdmission resolves the selected candidate and acquires its lease once,
// before the retry group. Only an explicit local refusal skips the attempt.
func (f *Failover) WithAdmission(admit func(context.Context, string) (*ProbeLease, error)) *Failover {
	scoped := *f
	scoped.admit = admit
	return &scoped
}

// IsEnabled returns true if failover is enabled and a router is available.
func (f *Failover) IsEnabled() bool {
	return f != nil && f.cfg.Enabled && f.router != nil
}

// ShouldFailover returns true if the error should trigger a failover attempt.
func (f *Failover) ShouldFailover(err error) bool {
	if err == nil {
		return false
	}

	// Check APIError status code.
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		if f.codes[apiErr.Status] {
			return true
		}
		return false
	}

	// Check timeout errors.
	if f.cfg.OnTimeout {
		if isTimeoutError(err) {
			return true
		}
	}

	return false
}

// Execute runs the failover loop, trying providers until one succeeds or all fail.
func (f *Failover) Execute(ctx context.Context, model string, call FailoverCallFunc) (*FailoverResult, error) {
	result, _, err := f.execute(ctx, model, call, false)
	return result, err
}

// ExecuteStream transfers the successful attempt's lease to the stream consumer.
// Failed startup retries share a lease and complete only their final outcome.
func (f *Failover) ExecuteStream(ctx context.Context, model string, call FailoverCallFunc) (*FailoverResult, *ProbeLease, error) {
	return f.execute(ctx, model, call, true)
}

func (f *Failover) execute(ctx context.Context, model string, call FailoverCallFunc, transfer bool) (*FailoverResult, *ProbeLease, error) {
	excluded := make(map[string]bool)
	var lastErr error
	var originalProvider string

	// Refused targets are excluded, but only admitted provider calls spend the
	// attempt budget. Selection ends once all distinct providers are excluded.
	attempt := 0
	for attempt < f.cfg.MaxAttempts {
		if err := ctx.Err(); err != nil {
			if lastErr != nil {
				return nil, nil, lastErr
			}
			return nil, nil, err
		}
		// Select a target, excluding already-tried providers.
		var result *RoutingResult
		var err error
		if len(excluded) == 0 {
			result, err = f.router.Select(model)
		} else {
			result, err = f.router.SelectExcluding(model, excluded)
		}
		if err != nil {
			if lastErr != nil {
				return nil, nil, lastErr // Return the actual provider error, not "no targets"
			}
			if len(excluded) > 0 || f.router.TargetCount(model) > 0 {
				return nil, nil, ProvidersUnavailableError(fmt.Sprintf("all providers for model %q are unavailable", model))
			}
			return nil, nil, err
		}

		if originalProvider == "" {
			originalProvider = result.Target.ProviderID
		}

		// Resolve and admit before retrying; refusal is explicit local provenance.
		var lease *ProbeLease
		var admissionErr error
		if f.admit != nil {
			lease, admissionErr = f.admit(ctx, result.Target.ProviderID)
		} else {
			cb := f.circuitBreakerFor(result.Target.ProviderID)
			if ctx.Err() != nil {
				admissionErr = ctx.Err()
			} else {
				var allowed bool
				lease, allowed = cb.Acquire()
				if !allowed {
					admissionErr = CircuitOpenError()
				}
			}
		}
		if admissionErr != nil {
			if IsCircuitOpen(admissionErr) {
				excluded[result.Target.ProviderID] = true
				continue
			}
			if lastErr != nil {
				return nil, nil, lastErr
			}
			return nil, nil, admissionErr
		}

		// Create per-attempt timeout if configured so later attempts get full time.
		attemptCtx := ctx
		var attemptCancel context.CancelFunc
		if f.cfg.PerAttemptTimeout > 0 {
			attemptCtx, attemptCancel = context.WithTimeout(ctx, f.cfg.PerAttemptTimeout)
		}

		// Call the provider (with retry if configured).
		attempt++
		var callErr, previousErr error
		invoke := func() error {
			if err := attemptCtx.Err(); err != nil {
				if previousErr != nil {
					return previousErr
				}
				return err
			}
			previousErr = call(attemptCtx, result.Target.ProviderID, result.Target.ModelOverride)
			return previousErr
		}
		if f.retryer.IsEnabled() {
			_, callErr = f.retryer.Execute(attemptCtx, invoke)
		} else {
			callErr = invoke()
		}
		if callErr == nil && attemptCtx.Err() != nil {
			callErr = attemptCtx.Err()
		}

		// Classify before canceling the short-lived startup/attempt context.
		if callErr != nil || !transfer {
			lease.Complete(attemptCtx, callErr)
		}
		if attemptCancel != nil {
			attemptCancel()
		}

		if callErr == nil {
			return &FailoverResult{
				ProviderID:       result.Target.ProviderID,
				ModelOverride:    result.Target.ModelOverride,
				Attempts:         attempt,
				OriginalProvider: originalProvider,
				FallbackUsed:     result.Target.ProviderID != originalProvider,
				StrategyName:     result.StrategyName,
			}, lease, nil
		}

		lastErr = callErr
		excluded[result.Target.ProviderID] = true

		// Check if we should failover.
		if !f.ShouldFailover(callErr) {
			return nil, nil, callErr
		}

		// Check context cancellation.
		if ctx.Err() != nil {
			return nil, nil, lastErr
		}
	}

	return nil, nil, lastErr
}

// isTimeoutError checks if an error is a timeout.
func isTimeoutError(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return true
	}
	if errors.Is(err, os.ErrDeadlineExceeded) {
		return true
	}
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		return apiErr.Status == 408 || apiErr.Status == 504
	}
	return false
}
