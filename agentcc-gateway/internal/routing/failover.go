package routing

import (
	"context"
	"errors"
	"net"
	"os"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// DefaultFailoverStatusCodes are HTTP status codes that trigger failover.
var DefaultFailoverStatusCodes = []int{429, 500, 502, 503, 504}

// Failover handles automatic provider failover on errors.
type Failover struct {
	cfg     config.FailoverConfig
	router  *Router
	retryer *Retryer
	cbReg   *CircuitBreakerRegistry
	codes   map[int]bool
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

	return &Failover{
		cfg:     cfg,
		router:  router,
		retryer: retryer,
		cbReg:   cbReg,
		codes:   codes,
	}
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
	excluded := make(map[string]bool)
	var lastErr error
	var originalProvider string

	for attempt := 1; attempt <= f.cfg.MaxAttempts; attempt++ {
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
				return nil, lastErr // Return the actual provider error, not "no targets"
			}
			return nil, err
		}

		if originalProvider == "" {
			originalProvider = result.Target.ProviderID
		}

		// Check circuit breaker before calling.
		if f.cbReg.IsEnabled() {
			cb := f.cbReg.Get(result.Target.ProviderID)
			if !cb.Allow() {
				excluded[result.Target.ProviderID] = true
				continue
			}
		}

		// Create per-attempt timeout if configured so later attempts get full time.
		attemptCtx := ctx
		var attemptCancel context.CancelFunc
		if f.cfg.PerAttemptTimeout > 0 {
			attemptCtx, attemptCancel = context.WithTimeout(ctx, f.cfg.PerAttemptTimeout)
		}

		// Call the provider (with retry if configured).
		var callErr error
		if f.retryer.IsEnabled() {
			_, callErr = f.retryer.Execute(attemptCtx, func() error {
				return call(attemptCtx, result.Target.ProviderID, result.Target.ModelOverride)
			})
		} else {
			callErr = call(attemptCtx, result.Target.ProviderID, result.Target.ModelOverride)
		}

		if attemptCancel != nil {
			attemptCancel()
		}

		// Record result in circuit breaker.
		if f.cbReg.IsEnabled() {
			cb := f.cbReg.Get(result.Target.ProviderID)
			if callErr == nil {
				cb.RecordSuccess()
			} else {
				cb.RecordFailure(callErr)
			}
		}

		if callErr == nil {
			return &FailoverResult{
				ProviderID:       result.Target.ProviderID,
				ModelOverride:    result.Target.ModelOverride,
				Attempts:         attempt,
				OriginalProvider: originalProvider,
				FallbackUsed:     attempt > 1,
				StrategyName:     result.StrategyName,
			}, nil
		}

		lastErr = callErr
		excluded[result.Target.ProviderID] = true

		// Check if we should failover.
		if !f.ShouldFailover(callErr) {
			return nil, callErr
		}

		// Check context cancellation.
		if ctx.Err() != nil {
			return nil, lastErr
		}
	}

	return nil, lastErr
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
