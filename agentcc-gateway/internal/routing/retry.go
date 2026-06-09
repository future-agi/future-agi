package routing

import (
	"context"
	"errors"
	"math"
	"math/rand"
	"net"
	"os"
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

// ShouldRetry returns true if the error is retryable.
func (r *Retryer) ShouldRetry(err error) bool {
	if err == nil {
		return false
	}

	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		return r.codes[apiErr.Status]
	}

	if r.cfg.OnTimeout {
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
	}

	return false
}

// Execute runs fn with retry logic. Returns the number of retries performed and the final error.
// retries=0 means the first call succeeded (or failed with a non-retryable error).
func (r *Retryer) Execute(ctx context.Context, fn func() error) (int, error) {
	if !r.IsEnabled() {
		return 0, fn()
	}

	var lastErr error
	for attempt := 0; attempt <= r.cfg.MaxRetries; attempt++ {
		if attempt > 0 {
			delay := r.backoff(attempt - 1)
			select {
			case <-time.After(delay):
			case <-ctx.Done():
				if lastErr != nil {
					return attempt, lastErr
				}
				return attempt, ctx.Err()
			}
		}

		err := fn()
		if err == nil {
			return attempt, nil
		}

		lastErr = err
		if !r.ShouldRetry(err) {
			return attempt, err
		}
	}

	return r.cfg.MaxRetries, lastErr
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
