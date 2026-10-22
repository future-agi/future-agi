package credits

import (
	"math"
	"time"
)

// Settings are the managed.* config values the managed-credit hot path uses
// (config.ManagedConfig): the markup the worst-case estimate includes, how
// long a hold lives, the output a request with no max_tokens is held for,
// what an unpriced model is held at, and the Retry-After of the 503 an
// opted-in org gets while its account is missing.
type Settings struct {
	MarkupPercent              float64
	HoldTTL                    time.Duration
	ReserveDefaultOutputTokens int
	UnpricedReserveUSD         float64
	AccountMissingRetryAfter   time.Duration
}

// DefaultSettings are config.DefaultConfig's managed values.
func DefaultSettings() Settings {
	return Settings{
		MarkupPercent:              5,
		HoldTTL:                    15 * time.Minute,
		ReserveDefaultOutputTokens: 4096,
		UnpricedReserveUSD:         0.10,
		AccountMissingRetryAfter:   30 * time.Second,
	}
}

// markupBps is MarkupPercent in basis points: 5 is 500.
func (s Settings) markupBps() int {
	if !(s.MarkupPercent > 0) {
		return 0
	}
	return int(math.Round(s.MarkupPercent * 100))
}

// holdTTL is HoldTTL, or the default when it is not positive: every hold
// keeps a positive lifetime.
func (s Settings) holdTTL() time.Duration {
	if s.HoldTTL <= 0 {
		return DefaultSettings().HoldTTL
	}
	return s.HoldTTL
}

// retryAfterSec is AccountMissingRetryAfter in whole seconds, at least 1.
func (s Settings) retryAfterSec() int {
	return max(int(math.Ceil(s.AccountMissingRetryAfter.Seconds())), 1)
}
