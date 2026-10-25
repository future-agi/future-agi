package routing

import (
	"context"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Request-level retry headers. They tune the retries of one
// request without an org config edit, and can only tighten the org's
// setting: a request never gets more tries per target than the org allows,
// and never gets retries when the org has none.
const (
	// HeaderMaxAttempts is the tries per target, the first call included
	// (Cloudflare's cf-aig-max-attempts meaning): 1 to 5, and never more
	// than the org's own (its max_retries + 1).
	HeaderMaxAttempts = "x-agentcc-max-attempts"
	// HeaderRetryDelayMs is the delay before the first retry, in
	// milliseconds: 0 to 60,000.
	HeaderRetryDelayMs = "x-agentcc-retry-delay-ms"
	// HeaderBackoff is how the delay grows from one retry to the next:
	// constant, linear or exponential.
	HeaderBackoff = "x-agentcc-backoff"

	// ErrCodeInvalidRetryHeader is the 400 for a malformed retry header;
	// the error's param names the header.
	ErrCodeInvalidRetryHeader = "invalid_retry_header"

	// MaxAttemptsOverrideLimit is the most x-agentcc-max-attempts accepts.
	MaxAttemptsOverrideLimit = 5
	// MaxRetryDelayOverride bounds x-agentcc-retry-delay-ms, and every
	// delay of a schedule the request chose.
	MaxRetryDelayOverride = 60 * time.Second
)

// The canonical forms of the header names, computed once: canonicalizing a
// lowercase name on every request allocates, and the request header map's
// keys are already canonical.
var (
	canonMaxAttempts  = http.CanonicalHeaderKey(HeaderMaxAttempts)
	canonRetryDelayMs = http.CanonicalHeaderKey(HeaderRetryDelayMs)
	canonBackoff      = http.CanonicalHeaderKey(HeaderBackoff)
)

// Backoff is a retry delay curve a request can choose.
type Backoff string

const (
	BackoffConstant    Backoff = "constant"    // every retry waits the delay
	BackoffLinear      Backoff = "linear"      // retry k waits delay × k
	BackoffExponential Backoff = "exponential" // retry k waits delay × multiplier^(k-1)
)

// RetryOverrides are one request's retry headers. The zero value overrides
// nothing.
type RetryOverrides struct {
	MaxAttempts int // 0: the org's
	Delay       time.Duration
	DelaySet    bool    // Delay was sent; a delay of 0 retries at once
	Backoff     Backoff // "": the org's
}

// IsZero reports whether o overrides nothing.
func (o RetryOverrides) IsZero() bool {
	return o.MaxAttempts == 0 && !o.DelaySet && o.Backoff == ""
}

// customSchedule reports whether o chooses the delays itself.
func (o RetryOverrides) customSchedule() bool {
	return o.DelaySet || o.Backoff != ""
}

// ParseRetryOverrides reads the retry headers of a request. A header that is
// sent but malformed or out of range is a 400 invalid_retry_header naming it:
// every header value sent is either applied or refused.
func ParseRetryOverrides(h http.Header) (RetryOverrides, *models.APIError) {
	var o RetryOverrides
	if v, ok := headerValue(h, canonMaxAttempts); ok {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > MaxAttemptsOverrideLimit {
			return RetryOverrides{}, invalidRetryHeader(HeaderMaxAttempts, "must be an integer from 1 to 5")
		}
		o.MaxAttempts = n
	}
	if v, ok := headerValue(h, canonRetryDelayMs); ok {
		ms, err := strconv.Atoi(v)
		if err != nil || ms < 0 || ms > int(MaxRetryDelayOverride/time.Millisecond) {
			return RetryOverrides{}, invalidRetryHeader(HeaderRetryDelayMs, "must be an integer from 0 to 60000")
		}
		o.Delay, o.DelaySet = time.Duration(ms)*time.Millisecond, true
	}
	if v, ok := headerValue(h, canonBackoff); ok {
		switch b := Backoff(strings.ToLower(v)); b {
		case BackoffConstant, BackoffLinear, BackoffExponential:
			o.Backoff = b
		default:
			return RetryOverrides{}, invalidRetryHeader(HeaderBackoff, "must be constant, linear or exponential")
		}
	}
	return o, nil
}

// headerValue is h's trimmed value for the canonical header name, and
// whether the header was sent at all (an empty value counts as sent).
func headerValue(h http.Header, canonical string) (string, bool) {
	vs, ok := h[canonical]
	if !ok || len(vs) == 0 {
		return "", false
	}
	return strings.TrimSpace(vs[0]), true
}

func invalidRetryHeader(header, reason string) *models.APIError {
	param := header
	apiErr := models.ErrBadRequest(ErrCodeInvalidRetryHeader, header+" "+reason)
	apiErr.Param = &param
	return apiErr
}

type retryOverridesKey struct{}

// WithRetryOverrides returns ctx carrying o, for every retry loop the
// request runs under it. Zero overrides return ctx unchanged.
func WithRetryOverrides(ctx context.Context, o RetryOverrides) context.Context {
	if o.IsZero() {
		return ctx
	}
	return context.WithValue(ctx, retryOverridesKey{}, o)
}

// RetryOverridesFromContext returns the overrides WithRetryOverrides put on
// ctx, if any.
func RetryOverridesFromContext(ctx context.Context) (RetryOverrides, bool) {
	o, ok := ctx.Value(retryOverridesKey{}).(RetryOverrides)
	return o, ok
}

// retryPlan is one run's retry schedule: the org's settings tightened by
// the request's overrides. A value, so planning costs no allocation.
type retryPlan struct {
	r         *Retryer
	retries   int // retries after the first try
	overrides RetryOverrides
}

// planFor is r's schedule under o. r must be enabled.
func (r *Retryer) planFor(o RetryOverrides) retryPlan {
	p := retryPlan{r: r, retries: r.cfg.MaxRetries, overrides: o}
	if o.MaxAttempts > 0 && o.MaxAttempts-1 < p.retries {
		p.retries = o.MaxAttempts - 1
	}
	return p
}

// delay is the wait before retry n+1 (n counts from 0). Without a delay or
// backoff override it is the org's jittered exponential schedule. With one
// it is the request's own deterministic curve, starting at its delay (else
// the org's initial delay), growing by the org's multiplier when
// exponential, and capped at MaxRetryDelayOverride rather than the org's
// max_delay, so a header value within its own bound is never cut short.
func (p retryPlan) delay(n int) time.Duration {
	if !p.overrides.customSchedule() {
		return p.r.backoff(n)
	}
	base := p.r.cfg.InitialDelay
	if p.overrides.DelaySet {
		base = p.overrides.Delay
	}
	var d float64
	switch p.overrides.Backoff {
	case BackoffConstant:
		d = float64(base)
	case BackoffLinear:
		d = float64(base) * float64(n+1)
	default: // exponential, the org's own curve
		d = float64(base) * math.Pow(p.r.cfg.Multiplier, float64(n))
	}
	if d > float64(MaxRetryDelayOverride) {
		return MaxRetryDelayOverride
	}
	return time.Duration(d)
}
