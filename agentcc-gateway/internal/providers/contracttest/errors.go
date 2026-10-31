package contracttest

import (
	"errors"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ErrorResult is the golden form of the error an adapter returned for an
// error case: what the gateway's error path and its client will see.
type ErrorResult struct {
	Status  int     `json:"status"`
	Type    string  `json:"type"`
	Code    string  `json:"code"`
	Message string  `json:"message"`
	Param   *string `json:"param,omitempty"`
	// RetryAfter is the retry hint the error carries, from a
	// RetryAfter() time.Duration method; empty when it has none.
	RetryAfter string `json:"retry_after,omitempty"`
	// GoType is set when the error is not a *models.APIError, which the
	// gateway turns into a 500.
	GoType string `json:"go_type,omitempty"`
}

// retryHinter is what an error carrying the upstream's retry hint
// (retry-after, retry-after-ms, Gemini RetryInfo) implements. Nothing does
// yet; AssertSaneError reports that as a sanity failure an error case lists
// in case.json's sanity_gaps.
type retryHinter interface {
	RetryAfter() time.Duration
}

// ErrorGolden renders err in its golden form.
func ErrorGolden(err error) ErrorResult {
	if err == nil {
		return ErrorResult{GoType: "<nil>"}
	}
	var r ErrorResult
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		r = ErrorResult{Status: apiErr.Status, Type: apiErr.Type, Code: apiErr.Code, Message: apiErr.Message, Param: apiErr.Param}
	} else {
		r = ErrorResult{Message: err.Error(), GoType: fmt.Sprintf("%T", err)}
	}
	var rh retryHinter
	if errors.As(err, &rh) && rh.RetryAfter() > 0 {
		r.RetryAfter = rh.RetryAfter().String()
	}
	return r
}

// AssertSaneError checks the invariants of the error an adapter returns for
// error case c (c.Status >= 400), subject to c's sanity_gaps (see
// ExpectGaps):
//
//   - there is an error, and it is a *models.APIError with a message;
//   - an upstream 4xx stays a 4xx (a 502 would be retried, failed over and
//     counted against the provider's circuit breaker), a 429 stays a 429,
//     and an upstream 5xx stays a 5xx;
//   - when the upstream sent retry-after or retry-after-ms, the error
//     carries a retry hint (a RetryAfter() time.Duration method).
func AssertSaneError(t testing.TB, c Case, err error) {
	t.Helper()
	ExpectGaps(t, c, func(t testing.TB) { saneError(t, c, err) })
}

func saneError(t testing.TB, c Case, err error) {
	t.Helper()
	if err == nil {
		t.Errorf("the upstream answered %d but the adapter returned no error", c.Status)
		return
	}
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) {
		t.Errorf("the adapter returned a %T, not a *models.APIError: %v", err, err)
		return
	}
	if strings.TrimSpace(apiErr.Message) == "" {
		t.Errorf("the canonical error has no message")
	}
	up, got := c.Status, apiErr.Status
	switch {
	case up == http.StatusTooManyRequests && got != http.StatusTooManyRequests:
		t.Errorf("upstream status 429 became %d: a rate limit must stay a 429", got)
	case up >= 400 && up < 500 && (got < 400 || got >= 500):
		t.Errorf("upstream status %d became %d: a client error turned into a retryable server error", up, got)
	case up >= 500 && (got < 500 || got > 599):
		t.Errorf("upstream status %d became %d: a server error must stay a 5xx", up, got)
	}
	for name, value := range c.Headers {
		lower := strings.ToLower(name)
		if lower != "retry-after" && lower != "retry-after-ms" {
			continue
		}
		var rh retryHinter
		if !errors.As(err, &rh) || rh.RetryAfter() <= 0 {
			t.Errorf("upstream sent %s: %q but the canonical error carries no retry hint, so the gateway retries on its own schedule", lower, value)
		}
	}
}
