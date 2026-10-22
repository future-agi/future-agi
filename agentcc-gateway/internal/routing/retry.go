package routing

import (
	"context"
	"errors"
	"math"
	"math/rand"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// DefaultRetryStatusCodes are HTTP status codes that trigger retry.
var DefaultRetryStatusCodes = []int{429, 500, 502, 503, 504}

// Retryer retries a function call with exponential backoff and jitter.
type Retryer struct {
	cfg   config.RetryConfig
	codes map[int]bool
}

// NewRetryer creates a retryer from config.
func NewRetryer(cfg config.RetryConfig) *Retryer {
	if cfg.MaxRetries <= 0 {
		cfg.MaxRetries = 2
	}
	if cfg.InitialDelay <= 0 {
		cfg.InitialDelay = 500 * time.Millisecond
	}
	if cfg.MaxDelay <= 0 {
		cfg.MaxDelay = 10 * time.Second
	}
	if cfg.Multiplier <= 0 {
		cfg.Multiplier = 2.0
	}
	if len(cfg.OnStatusCodes) == 0 {
		cfg.OnStatusCodes = DefaultRetryStatusCodes
	}

	codes := make(map[int]bool, len(cfg.OnStatusCodes))
	for _, c := range cfg.OnStatusCodes {
		codes[c] = true
	}

	return &Retryer{
		cfg:   cfg,
		codes: codes,
	}
}

// IsEnabled returns true if retry is enabled.
func (r *Retryer) IsEnabled() bool {
	return r != nil && r.cfg.Enabled
}

// ShouldRetry returns true if the error is retryable: its status or its
// provider's own status is in the retry list (a list naming 503
// matches an upstream 503 the gateway answers as 502), or it is a provider
// timeout and on_timeout is set. A fallback hop the gateway refused
// (ErrHopSkipped) never is: retrying it would run its admission, and take
// its hold, again.
func (r *Retryer) ShouldRetry(err error) bool {
	if err == nil || IsHopSkipped(err) {
		return false
	}

	var apiErr *models.APIError
	if errors.As(err, &apiErr) && matchesStatus(r.codes, apiErr) {
		return true
	}
	return r.cfg.OnTimeout && isProviderTimeout(err)
}

// Execute runs fn with retry logic. Returns the number of retries performed and the final error.
// retries=0 means the first call succeeded (or failed with a non-retryable error).
func (r *Retryer) Execute(ctx context.Context, fn func() error) (int, error) {
	return r.ExecuteObserved(ctx, fn, nil)
}

// ExecuteObserved is Execute that reports every try to onTry as it ends: n is
// the try's number (0 for the first call), err its result. onTry may be nil.
// A retryer that is nil or disabled makes one try. The request's retry
// headers, when ctx carries them (WithRetryOverrides), tighten the number of
// tries and may choose the delays; they never add tries.
func (r *Retryer) ExecuteObserved(ctx context.Context, fn func() error, onTry func(n int, err error)) (int, error) {
	try := func(n int) error {
		err := fn()
		if onTry != nil {
			onTry(n, err)
		}
		return err
	}
	if !r.IsEnabled() {
		return 0, try(0)
	}

	overrides, _ := RetryOverridesFromContext(ctx)
	plan := r.planFor(overrides)
	var lastErr error
	for attempt := 0; attempt <= plan.retries; attempt++ {
		if attempt > 0 {
			delay := plan.delay(attempt - 1)
			select {
			case <-time.After(delay):
			case <-ctx.Done():
				if lastErr != nil {
					return attempt, lastErr
				}
				return attempt, ctx.Err()
			}
		}

		err := try(attempt)
		if err == nil {
			return attempt, nil
		}

		lastErr = err
		if !r.ShouldRetry(err) {
			return attempt, err
		}
	}

	return plan.retries, lastErr
}

// backoff calculates the delay for a given retry attempt using full jitter.
func (r *Retryer) backoff(attempt int) time.Duration {
	delay := float64(r.cfg.InitialDelay) * math.Pow(r.cfg.Multiplier, float64(attempt))
	if delay > float64(r.cfg.MaxDelay) {
		delay = float64(r.cfg.MaxDelay)
	}
	if delay <= 0 {
		return 0
	}
	// Full jitter: random in [0, delay)
	return time.Duration(rand.Int63n(int64(delay)))
}
