package routing

import (
	"context"
	"errors"
	"fmt"
	"time"

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
	Attempts         int // targets tried
	OriginalProvider string
	FallbackUsed     bool
	StrategyName     string
	// AttemptRecords is every attempt of the run, in order: each call (the
	// primary, each retry, each failover target), each target its open
	// circuit excluded, and each hop the caller refused. Index counts from 1;
	// the request-level fields (AttemptID, CredentialType, tokens, costs) are
	// the request path's to fill.
	AttemptRecords []models.Attempt
}

// ErrCircuitOpen is the error of an attempt its target's open circuit
// excluded without a call.
var ErrCircuitOpen = errors.New("routing: circuit open")

// ErrHopSkipped marks a fallback hop the gateway refused before dispatching
// it (the hop's eligibility or its hold). A refused hop is not a provider
// failure: Execute and the retryer recognise it with errors.Is, record no
// breaker result for its target, do not retry it, and keep the previous
// attempt's error as the run's error.
var ErrHopSkipped = errors.New("routing: fallback hop skipped")

// hopSkipped is a refused hop: why (its attempt reason) and the
// previous attempt's error, which it unwraps to.
type hopSkipped struct {
	reason string
	prev   error
}

func (e *hopSkipped) Error() string {
	if e.prev == nil {
		return ErrHopSkipped.Error() + " (" + e.reason + ")"
	}
	return ErrHopSkipped.Error() + " (" + e.reason + ") after: " + e.prev.Error()
}

func (e *hopSkipped) Is(target error) bool { return target == ErrHopSkipped }
func (e *hopSkipped) Unwrap() error        { return e.prev }

// SkipHop is the error of a fallback hop refused for reason
// (models.ReasonPolicy or models.ReasonSkippedInsufficientCredits) after
// prev, the previous attempt's error. A skip after a skip keeps the original
// provider error.
func SkipHop(prev error, reason string) error {
	if _, before, ok := SkippedHop(prev); ok {
		prev = before
	}
	return &hopSkipped{reason: reason, prev: prev}
}

// SkippedHop reports whether err is a refused hop and, if so, why and the
// provider error before it.
func SkippedHop(err error) (reason string, prev error, ok bool) {
	var s *hopSkipped
	if errors.As(err, &s) {
		return s.reason, s.prev, true
	}
	return "", nil, false
}

// IsHopSkipped reports whether err is a refused hop.
func IsHopSkipped(err error) bool { return errors.Is(err, ErrHopSkipped) }

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

// Settings returns f's failover config and retryer, so a router of an org's
// own can keep the global failover behaviour while selecting differently.
func (f *Failover) Settings() (config.FailoverConfig, *Retryer) {
	return f.cfg, f.retryer
}

// Breakers returns f's circuit breaker registry, nil when it has none.
func (f *Failover) Breakers() *CircuitBreakerRegistry {
	if f == nil {
		return nil
	}
	return f.cbReg
}

// IsEnabled returns true if failover is enabled and a router is available.
func (f *Failover) IsEnabled() bool {
	return f != nil && f.cfg.Enabled && f.router != nil
}

// ShouldFailover returns true if the error should trigger a failover
// attempt: its status or its provider's own status is in the failover list,
// or it is a provider timeout and on_timeout is set.
func (f *Failover) ShouldFailover(err error) bool {
	if err == nil {
		return false
	}

	var apiErr *models.APIError
	if errors.As(err, &apiErr) && matchesStatus(f.codes, apiErr) {
		return true
	}
	return f.cfg.OnTimeout && isProviderTimeout(err)
}

// Execute runs the failover loop, trying providers until one succeeds or all
// fail. On failure it returns no result, only the run's error: the request
// path records each attempt itself as it ends.
func (f *Failover) Execute(ctx context.Context, model string, call FailoverCallFunc) (*FailoverResult, error) {
	return f.ExecuteObserved(ctx, model, call, nil)
}

// ExecuteObserved is Execute that also calls onExclude, when it is not nil,
// each time an open circuit excludes a target, at that moment and with the
// attempt the run records for it (outcome circuit_open), before the next
// target is selected. call is never made for an excluded target, so this is
// how the request path learns of one, also on a run that fails and returns
// no result. The attempt's Index is the run's own count; past
// models.MaxAttempts it is still reported but not kept in AttemptRecords, and
// the request's own record (rc.AddAttempt) applies its own cap.
func (f *Failover) ExecuteObserved(ctx context.Context, model string, call FailoverCallFunc, onExclude func(models.Attempt)) (*FailoverResult, error) {
	excluded := make(map[string]bool)
	res := &FailoverResult{}
	var lastErr error // the run's error: the latest provider failure
	var prevErr error // what triggered the next attempt, for its reason

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

		target := result.Target
		targetModel := target.ModelFor(model)
		if res.OriginalProvider == "" {
			res.OriginalProvider = target.ProviderID
		}
		kind := models.AttemptPrimary
		if attempt > 1 {
			kind = models.AttemptFailover
		}

		// Check circuit breaker before calling.
		if f.cbReg.IsEnabled() {
			cb := f.cbReg.Get(BreakerKey(target.ProviderID, targetModel, ""))
			if !cb.Allow() {
				a := res.record(ctx, kind, AttemptReason(prevErr), target.ProviderID, targetModel, time.Now(), ErrCircuitOpen)
				if onExclude != nil {
					onExclude(a)
				}
				prevErr = ErrCircuitOpen
				excluded[target.ProviderID] = true
				continue
			}
		}

		callErr := f.callTarget(ctx, res, kind, prevErr, target, targetModel, call)
		excluded[target.ProviderID] = true

		// A hop the caller refused was never sent: it is no provider failure,
		// and the run's error stays the previous provider's.
		if _, before, skipped := SkippedHop(callErr); skipped {
			if lastErr == nil {
				lastErr = before
				if lastErr == nil {
					lastErr = callErr
				}
			}
			if ctx.Err() != nil {
				return nil, lastErr
			}
			continue
		}

		// Record result in circuit breaker.
		if f.cbReg.IsEnabled() {
			cb := f.cbReg.Get(BreakerKey(target.ProviderID, targetModel, ""))
			if callErr == nil {
				cb.RecordSuccess()
			} else {
				cb.RecordFailure(callErr)
			}
		}

		if callErr == nil {
			res.ProviderID = target.ProviderID
			res.ModelOverride = target.ModelOverride
			res.Attempts = attempt
			res.FallbackUsed = attempt > 1
			res.StrategyName = result.StrategyName
			return res, nil
		}

		lastErr, prevErr = callErr, callErr

		// Check if we should failover.
		if !f.ShouldFailover(callErr) {
			return nil, callErr
		}

		// Check context cancellation.
		if ctx.Err() != nil {
			return nil, lastErr
		}
	}

	// Every attempt went to an open circuit: end with the 503 a circuit-aware
	// strategy returns when all circuits refuse, never with no result and no
	// error (the chat handlers read a nil result as a success).
	if lastErr == nil && errors.Is(prevErr, ErrCircuitOpen) {
		return nil, models.ErrServiceUnavailable(fmt.Sprintf("every provider tried for model %q has an open circuit", model))
	}
	return nil, lastErr
}

// callTarget calls one target, through the retryer when it is enabled, and
// records each try: the first as kind, every later one as a retry. prevErr
// is the error before the first try. It returns the last try's error.
func (f *Failover) callTarget(ctx context.Context, res *FailoverResult, kind models.AttemptKind, prevErr error, target RoutingTarget, targetModel string, call FailoverCallFunc) error {
	// Create per-attempt timeout if configured so later attempts get full time.
	attemptCtx := ctx
	if f.cfg.PerAttemptTimeout > 0 {
		var cancel context.CancelFunc
		attemptCtx, cancel = context.WithTimeout(ctx, f.cfg.PerAttemptTimeout)
		defer cancel()
	}

	var started time.Time
	try := func() error {
		started = time.Now()
		return call(attemptCtx, target.ProviderID, target.ModelOverride)
	}
	onTry := func(n int, err error) {
		k := kind
		if n > 0 {
			k = models.AttemptRetry
		}
		res.record(attemptCtx, k, AttemptReason(prevErr), target.ProviderID, targetModel, started, err)
		prevErr = err
	}

	// Call the provider (with retry if configured).
	_, err := f.retryer.ExecuteObserved(attemptCtx, try, onTry)
	return err
}

// record builds the attempt that started at started and ended with err,
// appends it while the run is under models.MaxAttempts, and returns it.
func (r *FailoverResult) record(ctx context.Context, kind models.AttemptKind, reason, providerID, model string, started time.Time, err error) models.Attempt {
	outcome, class, origin := AttemptResult(ctx, err)
	if skipReason, _, ok := SkippedHop(err); ok {
		reason = skipReason
	}
	a := models.Attempt{
		Index:        len(r.AttemptRecords) + 1,
		Kind:         kind,
		Reason:       reason,
		Provider:     providerID,
		Model:        model,
		StatusCode:   AttemptStatus(err),
		Outcome:      outcome,
		ErrorClass:   class,
		ErrorOrigin:  origin,
		ErrorMessage: AttemptErrorMessage(err),
		StartedAt:    started,
	}
	if outcome != models.OutcomeSkipped && outcome != models.OutcomeCircuitOpen {
		a.LatencyMs = time.Since(started).Milliseconds()
	}
	if len(r.AttemptRecords) < models.MaxAttempts {
		r.AttemptRecords = append(r.AttemptRecords, a)
	}
	return a
}
