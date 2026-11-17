package restmcp

import (
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Retry bounds. A provider's retry block names the statuses; these bound
// how often and how long the runtime retries.
const (
	maxAttempts  = 3                      // the first call and two retries
	maxRetryWait = 10 * time.Second       // a longer Retry-After is returned to the caller instead
	retryBase    = 500 * time.Millisecond // the first backoff when the vendor names no wait
)

// retryPolicy is a provider's retry block (manifest retry: on, and
// honorRetryAfter). The zero policy never retries.
type retryPolicy struct {
	on              []int
	honorRetryAfter bool
}

func compileRetry(r *manifest.Retry) retryPolicy {
	if r == nil {
		return retryPolicy{}
	}
	return retryPolicy{on: append([]int(nil), r.On...), honorRetryAfter: r.HonorRetryAfter}
}

// applies reports whether an answer with status is retried. A 429 means the
// vendor refused the request before doing it, so any tool is retried; any
// other listed status (a 503, say) may come after the vendor acted, so only
// an idempotent tool is: a retried send must not send twice.
func (p retryPolicy) applies(status int, idempotent bool) bool {
	listed := false
	for _, s := range p.on {
		if s == status {
			listed = true
			break
		}
	}
	return listed && (status == http.StatusTooManyRequests || idempotent)
}

// wait is how long to wait before retry number attempt (1 for the first):
// the vendor's Retry-After when the policy honours it and the answer names
// one, else an exponential backoff from base. ok is false when the vendor
// asked for longer than maxRetryWait.
func (p retryPolicy) wait(h http.Header, attempt int, base time.Duration) (time.Duration, bool) {
	if p.honorRetryAfter {
		if d, named := retryAfter(h.Get("Retry-After"), time.Now()); named {
			return d, d <= maxRetryWait
		}
	}
	return base << (attempt - 1), true
}

// maxDelaySeconds is the longest delay-seconds a time.Duration holds.
const maxDelaySeconds = math.MaxInt64 / int64(time.Second)

// retryAfter reads a Retry-After value: delay-seconds (one or more digits)
// or an HTTP-date (RFC 9110 10.2.3). A date in the past is no wait; a delay
// too long for a time.Duration is the longest one, so it is still longer
// than any call waits.
func retryAfter(v string, now time.Time) (time.Duration, bool) {
	v = strings.TrimSpace(v)
	if v == "" {
		return 0, false
	}
	if strings.Trim(v, "0123456789") == "" {
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil || n > maxDelaySeconds { // only a range error: v is all digits
			return time.Duration(math.MaxInt64), true
		}
		return time.Duration(n) * time.Second, true
	}
	if t, err := http.ParseTime(v); err == nil {
		if d := t.Sub(now); d > 0 {
			return d, true
		}
		return 0, true
	}
	return 0, false
}
